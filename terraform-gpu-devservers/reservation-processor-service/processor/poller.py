"""
Poller service - polls PGMQ and spawns Kubernetes Jobs.
Replaces the synchronous main.py processing loop with distributed job orchestration.
"""
import json
import logging
import os
import sys
import time
from typing import Dict, Any
from kubernetes import client, config

# Add parent directory to path for shared imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
root_dir = os.path.dirname(os.path.dirname(parent_dir))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Import shared utilities
try:
    from shared import get_db_cursor, init_connection_pool, close_connection_pool
    from shared.retry_utils import should_retry, get_retry_info
except ImportError as e:
    logger.error(f"Failed to import shared utilities: {e}")
    sys.exit(1)

# Import job manager
try:
    from processor.job_manager import JobManager
except ImportError as e:
    logger.error(f"Failed to import JobManager: {e}")
    sys.exit(1)

# Environment variables
QUEUE_NAME = os.environ.get("QUEUE_NAME", "gpu_reservations")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
VISIBILITY_TIMEOUT_SECONDS = int(os.environ.get("VISIBILITY_TIMEOUT_SECONDS", "900"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1"))
MAX_CONCURRENT_JOBS = int(os.environ.get("MAX_CONCURRENT_JOBS", "50"))
MAX_RETRIES = 3  # Maximum retry attempts before archiving

# Named constants for clarity
PENDING_JOB_WARNING_THRESHOLD_SECONDS = 300  # Warn if job pending > 5 minutes
STATUS_LOG_INTERVAL = 12  # Log "no messages" every N polls (12 * 5s = ~1 minute)

# Job tracking: msg_id -> {"job_name": str, "created_at": timestamp}
active_jobs: Dict[int, Dict[str, Any]] = {}


def poll_messages(batch_size: int = 1) -> list:
    """
    Poll messages from PGMQ queue.
    
    Args:
        batch_size: Number of messages to fetch
    
    Returns:
        List of message dictionaries
    """
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT * FROM pgmq.read(%s, %s, %s)",
                (QUEUE_NAME, VISIBILITY_TIMEOUT_SECONDS, batch_size)
            )
            messages = cur.fetchall()
            return [dict(msg) for msg in messages]
    except Exception as e:
        logger.error(f"Error polling messages: {e}", exc_info=True)
        return []


def archive_message(msg_id: int, reason: str) -> bool:
    """
    Archive message to PGMQ archive (dead letter queue).
    
    Args:
        msg_id: Message ID to archive
        reason: Reason for archiving
    
    Returns:
        True if archived successfully
    """
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT pgmq.archive(%s, %s)",
                (QUEUE_NAME, msg_id)
            )
            logger.warning(f"📦 Archived message {msg_id} to dead letter queue: {reason}")
            return True
    except Exception as e:
        logger.error(f"Error archiving message {msg_id}: {e}", exc_info=True)
        return False


def check_job_status(job_manager: JobManager, msg_id: int, job_info: Dict[str, Any]):
    """
    Check status of running job and handle completion.
    
    This is called periodically to monitor active jobs. When a job completes
    (success or failure), we remove it from tracking. PGMQ handles the rest:
    - On success: worker deleted the message
    - On failure: message becomes visible again after timeout
    
    Args:
        job_manager: JobManager instance
        msg_id: Message ID
        job_info: Job tracking info with job_name, created_at
    """
    job_name = job_info["job_name"]
    status = job_manager.get_job_status(job_name)
    
    if not status:
        logger.warning(f"Job {job_name} (msg {msg_id}) not found - removing from tracking")
        del active_jobs[msg_id]
        return
    
    if status["phase"] == "Succeeded":
        logger.info(f"✅ Job {job_name} (msg {msg_id}) succeeded")
        # Worker deleted the message, remove from tracking
        del active_jobs[msg_id]
        
    elif status["phase"] == "Failed":
        logger.warning(f"❌ Job {job_name} (msg {msg_id}) failed")
        # Message will become visible again after visibility timeout
        # Poller will pick it up and check retry count
        del active_jobs[msg_id]
        
        # Optionally log the pod logs for debugging
        try:
            logs = job_manager.get_job_logs(job_name, tail_lines=20)
            if logs:
                logger.warning(f"Last 20 lines of failed job {job_name}:\n{logs}")
        except Exception as e:
            logger.debug(f"Could not retrieve logs for {job_name}: {e}")
    
    elif status["phase"] == "Running":
        # Still running - this is normal
        logger.debug(f"⏳ Job {job_name} (msg {msg_id}) still running")
    
    elif status["phase"] == "Pending":
        # Still pending - check if it's been too long
        created_at = job_info.get("created_at", 0)
        age_seconds = time.time() - created_at
        if age_seconds > PENDING_JOB_WARNING_THRESHOLD_SECONDS:
            logger.warning(f"⚠️  Job {job_name} (msg {msg_id}) pending for {age_seconds:.0f}s")


def rebuild_active_jobs_from_k8s(job_manager: JobManager):
    """
    Rebuild active jobs tracking from existing K8s jobs.
    Called on startup to recover from poller restarts.
    """
    try:
        jobs = job_manager.batch_api.list_namespaced_job(
            namespace=job_manager.namespace,
            label_selector="app=reservation-worker"
        )
        
        recovered = 0
        for job in jobs.items:
            # Only track active jobs
            if job.status.active and job.status.active > 0:
                job_name = job.metadata.name
                # Extract msg_id from job name: "reservation-worker-123"
                try:
                    msg_id = int(job_name.split("-")[-1])
                    if msg_id <= 0:
                        logger.warning(
                            f"Skipping job {job_name}: extracted msg_id {msg_id} is not positive"
                        )
                        continue
                    active_jobs[msg_id] = {
                        "job_name": job_name,
                        "created_at": job.metadata.creation_timestamp.timestamp() if job.metadata.creation_timestamp else time.time(),
                        "action": job.metadata.labels.get("action", "unknown"),
                        "user_id": job.metadata.annotations.get("user_id", "unknown") if job.metadata.annotations else "unknown"
                    }
                    recovered += 1
                except (ValueError, IndexError, AttributeError):
                    logger.warning(
                        f"Skipping job {job_name}: name does not match expected format 'reservation-worker-<msg_id>'"
                    )
        
        logger.info(f"✅ Recovered {recovered} active jobs from Kubernetes")
        return recovered
    except Exception as e:
        logger.error(f"Failed to rebuild active jobs from K8s: {e}", exc_info=True)
        return 0


def process_loop():
    """Main poller loop - orchestrates job creation and monitoring."""
    logger.info("=" * 80)
    logger.info("🚀 Starting Reservation Processor Poller Service")
    logger.info("=" * 80)
    logger.info(f"Queue: {QUEUE_NAME}")
    logger.info(f"Poll interval: {POLL_INTERVAL_SECONDS}s")
    logger.info(f"Job timeout: {VISIBILITY_TIMEOUT_SECONDS}s")
    logger.info(f"Batch size: {BATCH_SIZE}")
    logger.info(f"Max concurrent jobs: {MAX_CONCURRENT_JOBS}")
    logger.info(f"Max retries: {MAX_RETRIES}")
    logger.info("=" * 80)
    
    # Initialize connection pool
    try:
        logger.info("Initializing database connection pool...")
        init_connection_pool()
        logger.info("✅ Connection pool initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize connection pool: {e}")
        logger.error("Cannot start poller without database connection")
        return
    
    # Initialize Kubernetes client
    try:
        logger.info("Initializing Kubernetes client...")
        config.load_incluster_config()
        batch_api = client.BatchV1Api()
        core_api = client.CoreV1Api()
        job_manager = JobManager(batch_api, core_api)
        logger.info("✅ Kubernetes client initialized")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Kubernetes client: {e}")
        logger.error("Cannot start poller without Kubernetes access")
        return
    
    # Rebuild active jobs from existing K8s jobs (recovery from restart)
    rebuild_active_jobs_from_k8s(job_manager)
    
    logger.info("🎯 Poller is ready to process messages")
    logger.info("=" * 80)
    
    consecutive_errors = 0
    retry_delay = 5
    max_retry_delay = 60
    poll_count = 0
    
    while True:
        try:
            poll_count += 1
            
            # Check status of active jobs.
            # We snapshot keys via list() because check_job_status may delete
            # entries from active_jobs. This is safe: the poller is single-threaded
            # (no threading used), so no concurrent mutation can occur.
            if active_jobs:
                logger.debug(f"Checking status of {len(active_jobs)} active job(s)")
                for msg_id in list(active_jobs.keys()):
                    job_info = active_jobs[msg_id]
                    check_job_status(job_manager, msg_id, job_info)
            
            # Backpressure: Check if we're at max concurrent jobs
            if len(active_jobs) >= MAX_CONCURRENT_JOBS:
                logger.warning(
                    f"⚠️  Max concurrent jobs ({MAX_CONCURRENT_JOBS}) reached. "
                    f"Waiting before polling new messages..."
                )
                time.sleep(POLL_INTERVAL_SECONDS * 2)  # Wait longer
                continue
            
            # Poll for new messages
            messages = poll_messages(batch_size=BATCH_SIZE)
            
            if messages:
                logger.info(f"📨 Received {len(messages)} message(s) from queue")
                consecutive_errors = 0
                retry_delay = 5
                
                for message in messages:
                    msg_id = message['msg_id']
                    msg_body = message['message']  # This is the actual message content (dict)
                    
                    # Log message details
                    action = msg_body.get("action", "unknown")
                    user_id = msg_body.get("user_id", "unknown")
                    
                    # Use PGMQ's built-in read_ct for retry tracking
                    # This is automatically incremented by PGMQ on each read
                    read_count = message.get('read_ct', 0)
                    
                    logger.info(
                        f"Processing msg {msg_id}: "
                        f"action={action}, user={user_id}, "
                        f"read_count={read_count}/{MAX_RETRIES}"
                    )
                    
                    # Check if already processing
                    if msg_id in active_jobs:
                        logger.debug(f"Message {msg_id} already has active job - skipping")
                        continue
                    
                    # Check retry count using PGMQ's read_ct
                    if read_count >= MAX_RETRIES:
                        logger.error(
                            f"💀 Message {msg_id} exceeded max retries "
                            f"(read_count={read_count}/{MAX_RETRIES})"
                        )
                        archive_message(msg_id, f"Max retries exceeded: {read_count}/{MAX_RETRIES}")
                        continue
                    
                    # Create job for message
                    # Pass the full message body to the job so worker doesn't need to re-read
                    # (message is invisible due to visibility timeout set by this read)
                    try:
                        job_name = job_manager.create_job(msg_id, msg_body)  # Pass msg_body (dict) not message
                        active_jobs[msg_id] = {
                            "job_name": job_name,
                            "created_at": time.time(),
                            "action": action,
                            "user_id": user_id
                        }
                        logger.info(f"✨ Created job {job_name} for message {msg_id}")
                        
                    except Exception as e:
                        logger.error(f"❌ Failed to create job for message {msg_id}: {e}", exc_info=True)
                        # Message will become visible again and we'll retry
            else:
                # No messages - only log occasionally to reduce noise
                if poll_count % STATUS_LOG_INTERVAL == 0:
                    logger.debug(f"No messages in queue ({len(active_jobs)} active jobs)")
                
                time.sleep(POLL_INTERVAL_SECONDS)
        
        except KeyboardInterrupt:
            logger.info("🛑 Received shutdown signal, exiting gracefully...")
            break
        
        except Exception as e:
            consecutive_errors += 1
            logger.error(
                f"❌ Error in poller loop (error count: {consecutive_errors}): {e}",
                exc_info=True
            )
            
            if consecutive_errors > 3:
                logger.warning(f"⚠️  Multiple errors, backing off for {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            else:
                time.sleep(POLL_INTERVAL_SECONDS)
    
    # Cleanup on shutdown
    logger.info("=" * 80)
    logger.info("🧹 Cleaning up poller service...")
    
    # Log active jobs
    if active_jobs:
        logger.warning(f"⚠️  {len(active_jobs)} job(s) still active at shutdown:")
        for msg_id, job_info in active_jobs.items():
            logger.warning(f"  - msg {msg_id}: {job_info['job_name']}")
        logger.warning("These jobs will continue running and will be cleaned up by Kubernetes")
    
    # Close database connection pool
    try:
        close_connection_pool()
        logger.info("✅ Connection pool closed")
    except Exception as e:
        logger.error(f"❌ Error closing connection pool: {e}")
    
    logger.info("👋 Poller service stopped")
    logger.info("=" * 80)


if __name__ == "__main__":
    process_loop()

