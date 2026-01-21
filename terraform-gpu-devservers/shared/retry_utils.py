"""
Retry utilities for job orchestration.

Provides retry tracking and decision logic for PGMQ message processing.
Messages include retry metadata to prevent infinite retry loops.
"""
from typing import Dict, Any
from datetime import datetime, timezone

MAX_RETRIES = 3


def should_retry(message: Dict[str, Any]) -> bool:
    """
    Determine if a message should be retried based on retry count.
    
    Args:
        message: PGMQ message with _metadata.retry_count
    
    Returns:
        True if should retry, False if should archive (dead letter)
    """
    metadata = message.get("_metadata", {})
    retry_count = metadata.get("retry_count", 0)
    max_retries = metadata.get("max_retries", MAX_RETRIES)
    
    return retry_count < max_retries


def increment_retry_count(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Increment retry count in message metadata.
    
    This should be called when re-queuing a failed message.
    
    Args:
        message: PGMQ message
    
    Returns:
        Updated message with incremented retry_count
    """
    if "_metadata" not in message:
        message["_metadata"] = {}
    
    message["_metadata"]["retry_count"] = message["_metadata"].get("retry_count", 0) + 1
    message["_metadata"]["last_retry_at"] = datetime.now(timezone.utc).isoformat()
    
    return message


def get_retry_info(message: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get retry information from message metadata.
    
    Args:
        message: PGMQ message
    
    Returns:
        Dictionary with retry_count, max_retries, created_at, last_retry_at
    """
    metadata = message.get("_metadata", {})
    return {
        "retry_count": metadata.get("retry_count", 0),
        "max_retries": metadata.get("max_retries", MAX_RETRIES),
        "created_at": metadata.get("created_at"),
        "last_retry_at": metadata.get("last_retry_at")
    }


def create_message_metadata(max_retries: int = MAX_RETRIES) -> Dict[str, Any]:
    """
    Create initial message metadata for a new message.
    
    Args:
        max_retries: Maximum number of retry attempts (default: 3)
    
    Returns:
        Metadata dictionary to include in message
    """
    return {
        "retry_count": 0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "max_retries": max_retries
    }


def is_dead_letter(message: Dict[str, Any]) -> bool:
    """
    Check if message has exceeded max retries and should be archived.
    
    Args:
        message: PGMQ message
    
    Returns:
        True if message should be archived (dead letter)
    """
    return not should_retry(message)

