"""
Worker script that runs inside Kubernetes Jobs.
Processes a single reservation message from PGMQ.
"""
import json
import logging
import os
import sys
from typing import Optional

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
except ImportError:
    logger.error("Failed to import shared utilities - check PYTHONPATH")
    sys.exit(1)

# Environment variables
QUEUE_NAME = os.environ.get("QUEUE_NAME", "gpu_reservations")


def get_message_body_from_env() -> Optional[dict]:
    """
    Get message body from environment variable.
    
    The poller passes the message body directly via MESSAGE_BODY env var
    to avoid the visibility timeout issue (message is invisible after
    poller reads it, so worker can't read it again from queue).
    
    Returns:
        Message body dict or None if not found
    """
    try:
        message_json = os.environ.get("MESSAGE_BODY")
        if not message_json:
            logger.error("MESSAGE_BODY environment variable not set")
            return None
        
        message_body = json.loads(message_json)
        return message_body
        
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse MESSAGE_BODY JSON: {e}")
        return None
    except Exception as e:
        logger.error(f"Error reading message body from env: {e}", exc_info=True)
        return None


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
        logger.error(f"Error deleting message {msg_id}: {e}", exc_info=True)
        return False


def process_message(msg_id: int) -> bool:
    """
    Process a single message by ID.
    
    This calls the existing reservation_handler code that was originally
    designed for Lambda. We wrap it in a Lambda-like event structure.
    
    The message body is passed via MESSAGE_BODY environment variable
    (set by the poller when creating the job) to avoid the visibility
    timeout issue.
    
    Args:
        msg_id: Message ID to process
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Get message body from environment variable
        # (passed by poller, avoids visibility timeout issue)
        msg_body = get_message_body_from_env()
        if not msg_body:
            logger.error(f"Failed to get message body for message {msg_id}")
            return False
        action = msg_body.get('action', 'unknown')
        user_id = msg_body.get('user_id', 'unknown')
        
        logger.info(f"Processing message {msg_id}: action={action}, user={user_id}")
        
        # Validate message structure
        if not msg_body.get('action'):
            logger.error(f"Invalid message format - missing action: {msg_body}")
            return False
        
        # Import reservation handler (done here to avoid import errors at startup)
        try:
            from processor import reservation_handler
        except ImportError as e:
            logger.error(f"Failed to import reservation_handler: {e}")
            return False
        
        # Create Lambda-like event structure
        # The handler expects an event like Lambda would receive from SQS
        event = {
            'Records': [{
                'eventSource': 'aws:sqs',  # Required by handler to process the record
                'messageId': str(msg_id),
                'body': json.dumps(msg_body),
                'messageAttributes': {}
            }]
        }
        
        context = {}  # Empty context (not used by handler logic)
        
        # Call the handler
        logger.info(f"Calling reservation_handler for message {msg_id}")
        result = reservation_handler.handler(event, context)
        
        # Check result
        if result and result.get('statusCode') == 200:
            logger.info(f"Message {msg_id} processed successfully: action={action}")
            
            # Delete message from queue on success
            if delete_message(msg_id):
                logger.info(f"Message {msg_id} deleted from queue")
                return True
            else:
                logger.error(f"Failed to delete message {msg_id} - will retry")
                # Return False so job fails and message becomes visible again
                return False
        else:
            logger.error(f"Handler returned error for message {msg_id}: {result}")
            return False
            
    except Exception as e:
        logger.error(f"Error processing message {msg_id}: {e}", exc_info=True)
        return False


def main():
    """Main entry point for worker job."""
    # Get message ID from command line argument
    if len(sys.argv) < 2:
        logger.error("Usage: worker.py <msg_id>")
        logger.error("This script must be called with a message ID argument")
        sys.exit(1)
    
    try:
        msg_id = int(sys.argv[1])
    except ValueError:
        logger.error(f"Invalid msg_id argument: {sys.argv[1]} (must be an integer)")
        sys.exit(1)
    
    logger.info(f"=== Worker started for message {msg_id} ===")
    logger.info(f"Queue: {QUEUE_NAME}")
    logger.info(f"PID: {os.getpid()}")
    
    # Initialize connection pool with reduced size for worker jobs
    # Workers should use small pools to avoid exhausting database connections
    # when many jobs run concurrently (50 jobs * 10 max connections = 500!)
    try:
        logger.info("Initializing database connection pool (worker mode: small pool)...")
        init_connection_pool(minconn=1, maxconn=3)
        logger.info("Connection pool initialized successfully (min=1, max=3)")
    except Exception as e:
        logger.error(f"Failed to initialize connection pool: {e}", exc_info=True)
        logger.error("Cannot process message without database connection")
        sys.exit(1)
    
    # Process message
    exit_code = 1  # Default to failure
    try:
        success = process_message(msg_id)
        exit_code = 0 if success else 1
        
        if success:
            logger.info(f"=== Worker succeeded for message {msg_id} ===")
        else:
            logger.error(f"=== Worker failed for message {msg_id} ===")
            
    except Exception as e:
        logger.error(f"Unexpected error in worker: {e}", exc_info=True)
        exit_code = 1
        
    finally:
        # Cleanup
        try:
            close_connection_pool()
            logger.info("Connection pool closed")
        except Exception as e:
            logger.error(f"Error closing connection pool: {e}")
    
    logger.info(f"Worker exiting with code {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

