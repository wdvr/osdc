"""
GPU Reservation Processor Service
Replaces Lambda function - polls PGMQ and processes reservation requests
"""

import json
import logging
import os
import sys
import time
from typing import Optional

# Add parent directory to path for shared imports
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

# Import shared utilities
from shared import get_db_cursor, init_connection_pool, close_connection_pool

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# Environment variables
QUEUE_NAME = os.environ.get("QUEUE_NAME", "gpu_reservations")
POLL_INTERVAL_SECONDS = int(os.environ.get("POLL_INTERVAL_SECONDS", "5"))
VISIBILITY_TIMEOUT_SECONDS = int(os.environ.get("VISIBILITY_TIMEOUT_SECONDS", "300"))
BATCH_SIZE = int(os.environ.get("BATCH_SIZE", "1"))


def poll_messages(batch_size: int = 1) -> list:
    """
    Poll messages from PGMQ queue using shared connection pool.
    
    Args:
        batch_size: Number of messages to fetch (default 1)
    
    Returns:
        List of message dictionaries with 'msg_id', 'read_ct', 'enqueued_at', 'vt', 'message'
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            # pgmq.read(queue_name, vt, limit) -> reads messages with visibility timeout
            cur.execute(
                "SELECT * FROM pgmq.read(%s, %s, %s)",
                (QUEUE_NAME, VISIBILITY_TIMEOUT_SECONDS, batch_size)
            )
            messages = cur.fetchall()
            return [dict(msg) for msg in messages]
    except Exception as e:
        logger.error(f"Error polling messages: {e}")
        return []


def delete_message(msg_id: int) -> bool:
    """
    Delete message from PGMQ queue after successful processing.
    
    Args:
        msg_id: Message ID to delete
    
    Returns:
        True if deleted successfully
    """
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT pgmq.delete(%s, %s)",
                (QUEUE_NAME, msg_id)
            )
            result = cur.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Error deleting message {msg_id}: {e}")
        return False


def archive_message(msg_id: int) -> bool:
    """
    Archive message to PGMQ archive (for failed messages).
    
    Args:
        msg_id: Message ID to archive
    
    Returns:
        True if archived successfully
    """
    try:
        with get_db_cursor() as cur:
            cur.execute(
                "SELECT pgmq.archive(%s, %s)",
                (QUEUE_NAME, msg_id)
            )
            result = cur.fetchone()
            return result is not None
    except Exception as e:
        logger.error(f"Error archiving message {msg_id}: {e}")
        return False


def process_reservation_message(message: dict) -> bool:
    """
    Process a single reservation message.
    
    Args:
        message: Message dictionary from PGMQ
    
    Returns:
        True if processing succeeded, False otherwise
    """
    msg_id = message['msg_id']
    msg_body = message['message']
    
    try:
        action = msg_body.get('action', 'unknown')
        user_id = msg_body.get('user_id', 'unknown')
        
        logger.info(f"Processing message {msg_id}: action={action}, user={user_id}")
        
        # Validate message structure
        if not msg_body.get('action'):
            logger.error(f"Invalid message format - missing action: {msg_body}")
            return False
        
        # Import and call the reservation handler
        from processor import reservation_handler
        
        # Call handler with PGMQ message format
        # The handler expects an event like Lambda would receive
        # Create a Lambda-like event structure
        event = {
            'Records': [{
                'messageId': str(msg_id),
                'body': json.dumps(msg_body),
                'messageAttributes': {}
            }]
        }
        
        context = {}  # Empty context (not used by handler logic)
        
        result = reservation_handler.handler(event, context)
        
        # Handler returns a response dict with statusCode
        if result and result.get('statusCode') == 200:
            logger.info(f"Message {msg_id} processed successfully: action={action}")
            return True
        else:
            logger.error(f"Handler returned error for message {msg_id}: {result}")
            return False
        
    except Exception as e:
        logger.error(f"Error processing message {msg_id}: {e}", exc_info=True)
        return False


def process_loop():
    """Main processing loop - polls PGMQ and processes messages"""
    logger.info("Starting reservation processor service")
    logger.info(f"Queue: {QUEUE_NAME}")
    logger.info(f"Poll interval: {POLL_INTERVAL_SECONDS}s")
    logger.info(f"Visibility timeout: {VISIBILITY_TIMEOUT_SECONDS}s")
    logger.info(f"Batch size: {BATCH_SIZE}")
    
    # Initialize connection pool at startup
    try:
        logger.info("Initializing connection pool...")
        init_connection_pool()
        logger.info("Connection pool initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize connection pool: {e}")
        logger.error("Cannot start service without database connection")
        return
    
    # Error handling with retry
    retry_delay = 5
    max_retry_delay = 60
    consecutive_errors = 0
    
    while True:
        try:
            # Poll for messages (uses connection pool internally)
            messages = poll_messages(batch_size=BATCH_SIZE)
            
            if messages:
                logger.info(f"Received {len(messages)} message(s)")
                consecutive_errors = 0  # Reset error count on success
                retry_delay = 5  # Reset retry delay
                
                for message in messages:
                    msg_id = message['msg_id']
                    
                    # Process the message
                    success = process_reservation_message(message)
                    
                    if success:
                        # Delete message from queue
                        if delete_message(msg_id):
                            logger.info(f"Message {msg_id} deleted from queue")
                        else:
                            logger.warning(f"Failed to delete message {msg_id}")
                    else:
                        # Archive failed message
                        logger.warning(f"Message {msg_id} processing failed, archiving")
                        if archive_message(msg_id):
                            logger.info(f"Message {msg_id} archived")
                        else:
                            logger.warning(f"Failed to archive message {msg_id}")
            else:
                # No messages, wait before polling again
                logger.debug("No messages available, waiting...")
                time.sleep(POLL_INTERVAL_SECONDS)
                
        except KeyboardInterrupt:
            logger.info("Received shutdown signal, exiting...")
            break
            
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Error in processing loop (count: {consecutive_errors}): {e}", exc_info=True)
            
            # Exponential backoff for repeated errors
            if consecutive_errors > 3:
                logger.warning(f"Multiple consecutive errors, backing off for {retry_delay}s")
                time.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            else:
                time.sleep(POLL_INTERVAL_SECONDS)
    
    # Cleanup
    try:
        logger.info("Closing connection pool...")
        close_connection_pool()
        logger.info("Connection pool closed")
    except Exception as e:
        logger.error(f"Error closing connection pool: {e}")
    
    logger.info("Reservation processor service stopped")


if __name__ == "__main__":
    process_loop()
