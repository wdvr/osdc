"""
Disk Database Operations

This module provides database operations for persistent disks, replacing DynamoDB
interactions with PostgreSQL queries. All functions use the connection pool from
db_pool.py for efficient database access.

Usage:
    from shared.disk_db import (
        create_disk,
        get_disk,
        update_disk,
        delete_disk,
        list_disks_by_user,
        mark_disk_in_use
    )
"""

import logging
from datetime import datetime, UTC
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .db_pool import get_db_cursor

logger = logging.getLogger(__name__)


def create_disk(disk_data: Dict[str, Any]) -> bool:
    """
    Create a new disk record in PostgreSQL.
    
    Args:
        disk_data: Dictionary containing disk fields
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Required fields
        disk_name = disk_data['disk_name']
        user_id = disk_data['user_id']
        
        # Optional fields with defaults
        disk_id = disk_data.get('disk_id', str(uuid4()))
        size_gb = disk_data.get('size_gb')
        created_at = disk_data.get('created_at', datetime.now(UTC))
        last_used = disk_data.get('last_used')
        in_use = disk_data.get('in_use', False)
        reservation_id = disk_data.get('reservation_id')
        is_backing_up = disk_data.get('is_backing_up', False)
        is_deleted = disk_data.get('is_deleted', False)
        delete_date = disk_data.get('delete_date')
        snapshot_count = disk_data.get('snapshot_count', 0)
        pending_snapshot_count = disk_data.get('pending_snapshot_count', 0)
        ebs_volume_id = disk_data.get('ebs_volume_id')
        last_snapshot_at = disk_data.get('last_snapshot_at')
        operation_id = disk_data.get('operation_id')
        operation_status = disk_data.get('operation_status')
        operation_error = disk_data.get('operation_error')
        latest_snapshot_content_s3 = disk_data.get('latest_snapshot_content_s3')
        disk_size = disk_data.get('disk_size')  # Human-readable size like "1.2G"
        
        with get_db_cursor() as cur:
            # Check if disk_size column exists (for backwards compatibility during migration)
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.columns 
                    WHERE table_name = 'disks' AND column_name = 'disk_size'
                )
            """)
            disk_size_column_exists = cur.fetchone()['exists']
            
            if disk_size_column_exists:
                # New schema with disk_size column
                cur.execute("""
                    INSERT INTO disks (
                        disk_id, disk_name, user_id, size_gb, created_at, last_used,
                        in_use, reservation_id, is_backing_up, is_deleted, delete_date,
                        snapshot_count, pending_snapshot_count, ebs_volume_id, last_snapshot_at,
                        operation_id, operation_status, operation_error,
                        latest_snapshot_content_s3, disk_size
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (user_id, disk_name) DO UPDATE SET
                        size_gb = EXCLUDED.size_gb,
                        last_used = EXCLUDED.last_used,
                        in_use = EXCLUDED.in_use,
                        reservation_id = EXCLUDED.reservation_id,
                        is_deleted = EXCLUDED.is_deleted,
                        operation_id = EXCLUDED.operation_id,
                        operation_status = EXCLUDED.operation_status,
                        operation_error = EXCLUDED.operation_error,
                        disk_size = EXCLUDED.disk_size
                """, (
                    disk_id, disk_name, user_id, size_gb, created_at, last_used,
                    in_use, reservation_id, is_backing_up, is_deleted, delete_date,
                    snapshot_count, pending_snapshot_count, ebs_volume_id, last_snapshot_at,
                    operation_id, operation_status, operation_error,
                    latest_snapshot_content_s3, disk_size
                ))
            else:
                # Old schema without disk_size column (backwards compatibility)
                logger.warning("disk_size column does not exist yet - using old schema")
                cur.execute("""
                    INSERT INTO disks (
                        disk_id, disk_name, user_id, size_gb, created_at, last_used,
                        in_use, reservation_id, is_backing_up, is_deleted, delete_date,
                        snapshot_count, pending_snapshot_count, ebs_volume_id, last_snapshot_at,
                        operation_id, operation_status, operation_error,
                        latest_snapshot_content_s3
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (user_id, disk_name) DO UPDATE SET
                        size_gb = EXCLUDED.size_gb,
                        last_used = EXCLUDED.last_used,
                        in_use = EXCLUDED.in_use,
                        reservation_id = EXCLUDED.reservation_id,
                        is_deleted = EXCLUDED.is_deleted,
                        operation_id = EXCLUDED.operation_id,
                        operation_status = EXCLUDED.operation_status,
                        operation_error = EXCLUDED.operation_error
                """, (
                    disk_id, disk_name, user_id, size_gb, created_at, last_used,
                    in_use, reservation_id, is_backing_up, is_deleted, delete_date,
                    snapshot_count, pending_snapshot_count, ebs_volume_id, last_snapshot_at,
                    operation_id, operation_status, operation_error,
                    latest_snapshot_content_s3
                ))
            
        logger.info(f"Created/updated disk '{disk_name}' for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating disk: {e}", exc_info=True)
        return False


def get_disk(user_id: str, disk_name: str) -> Optional[Dict[str, Any]]:
    """
    Get a disk by user_id and disk_name.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
    
    Returns:
        Disk dictionary or None if not found
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            cur.execute("""
                SELECT * FROM disks
                WHERE user_id = %s AND disk_name = %s
            """, (user_id, disk_name))
            
            result = cur.fetchone()
            return dict(result) if result else None
            
    except Exception as e:
        logger.error(f"Error getting disk '{disk_name}' for user {user_id}: {e}")
        return None


def get_disk_by_id(disk_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a disk by its UUID.
    
    Args:
        disk_id: The disk UUID
    
    Returns:
        Disk dictionary or None if not found
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            cur.execute("""
                SELECT * FROM disks
                WHERE disk_id = %s
            """, (disk_id,))
            
            result = cur.fetchone()
            return dict(result) if result else None
            
    except Exception as e:
        logger.error(f"Error getting disk by ID {disk_id}: {e}")
        return None


def try_acquire_disk(user_id: str, disk_name: str, reservation_id: str) -> tuple[bool, str]:
    """
    Atomically try to acquire a disk for exclusive use.
    
    Uses SELECT FOR UPDATE to lock the row and check availability in a single
    atomic transaction, preventing race conditions where multiple reservations
    could try to claim the same disk simultaneously.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
        reservation_id: The reservation ID trying to acquire the disk
    
    Returns:
        Tuple of (success: bool, message: str)
        - (True, "Disk acquired") if successfully acquired
        - (False, error_message) if disk is unavailable or error occurred
    """
    try:
        from .db_pool import get_db_transaction
        
        with get_db_transaction() as conn:
            with conn.cursor() as cur:
                # Lock row and check availability in single atomic operation
                # NOWAIT ensures we fail fast if another process holds the lock
                try:
                    cur.execute("""
                        SELECT in_use, reservation_id as current_reservation, 
                               is_deleted, is_backing_up
                        FROM disks
                        WHERE user_id = %s AND disk_name = %s
                        FOR UPDATE NOWAIT
                    """, (user_id, disk_name))
                    
                    disk = cur.fetchone()
                    
                    if not disk:
                        return False, f"Disk '{disk_name}' not found"
                    
                    # Check if disk is deleted
                    if disk['is_deleted']:
                        return False, f"Disk '{disk_name}' has been deleted"
                    
                    # Check if disk is already in use
                    if disk['in_use']:
                        current_res = disk['current_reservation'] or 'unknown'
                        return False, f"Disk '{disk_name}' is currently in use by reservation {current_res}"
                    
                    # Check if disk is backing up (not safe to attach)
                    if disk['is_backing_up']:
                        return False, f"Disk '{disk_name}' is currently backing up, please try again later"
                    
                    # Disk is available - claim it atomically
                    cur.execute("""
                        UPDATE disks
                        SET in_use = TRUE, 
                            reservation_id = %s, 
                            last_used = %s
                        WHERE user_id = %s AND disk_name = %s
                    """, (reservation_id, datetime.now(UTC), user_id, disk_name))
                    
                    logger.info(f"Acquired disk '{disk_name}' for reservation {reservation_id}")
                    # Commit happens automatically on context exit
                    return True, "Disk acquired"
                    
                except Exception as lock_error:
                    # Check if it's a lock wait error
                    if hasattr(lock_error, 'pgcode'):
                        # 55P03 = lock_not_available
                        if lock_error.pgcode == '55P03':
                            return False, f"Disk '{disk_name}' is locked by another process, please try again"
                    raise  # Re-raise if it's a different error
                    
    except Exception as e:
        logger.error(f"Error acquiring disk '{disk_name}' for user {user_id}: {e}", exc_info=True)
        return False, f"Error acquiring disk: {str(e)}"


def update_disk(user_id: str, disk_name: str, updates: Dict[str, Any]) -> bool:
    """
    Update a disk with the provided field updates.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
        updates: Dictionary of field names and values to update
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if not updates:
            logger.warning(f"No updates provided for disk '{disk_name}'")
            return True
        
        # Build SET clause dynamically
        set_clauses = []
        params = []
        
        for field, value in updates.items():
            set_clauses.append(f"{field} = %s")
            params.append(value)
        
        # Add user_id and disk_name for WHERE clause
        params.extend([user_id, disk_name])
        
        # Build query
        query = """
            UPDATE disks
            SET """ + ', '.join(set_clauses) + """
            WHERE user_id = %s AND disk_name = %s
        """
        
        with get_db_cursor() as cur:
            cur.execute(query, params)
            
            if cur.rowcount > 0:
                logger.debug(f"Updated disk '{disk_name}' for user {user_id}")
                return True
            else:
                logger.warning(f"No disk found: '{disk_name}' for user {user_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error updating disk '{disk_name}' for user {user_id}: {e}", exc_info=True)
        return False


def delete_disk(user_id: str, disk_name: str) -> bool:
    """
    Physically delete a disk record from the database.
    Note: Consider using mark_disk_deleted() instead for soft deletion.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                DELETE FROM disks
                WHERE user_id = %s AND disk_name = %s
            """, (user_id, disk_name))
            
            if cur.rowcount > 0:
                logger.info(f"Deleted disk '{disk_name}' for user {user_id}")
                return True
            else:
                logger.warning(f"No disk found: '{disk_name}' for user {user_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error deleting disk '{disk_name}' for user {user_id}: {e}")
        return False


def list_disks_by_user(user_id: str, include_deleted: bool = False) -> List[Dict[str, Any]]:
    """
    List all disks for a specific user.
    
    Args:
        user_id: The user ID
        include_deleted: Whether to include soft-deleted disks
    
    Returns:
        List of disk dictionaries
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            if include_deleted:
                cur.execute("""
                    SELECT * FROM disks
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                """, (user_id,))
            else:
                cur.execute("""
                    SELECT * FROM disks
                    WHERE user_id = %s AND is_deleted = FALSE
                    ORDER BY created_at DESC
                """, (user_id,))
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error listing disks for user {user_id}: {e}")
        return []


def mark_disk_in_use(user_id: str, disk_name: str, reservation_id: str, in_use: bool = True) -> bool:
    """
    Mark a disk as in use or not in use.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
        reservation_id: The reservation using the disk
        in_use: True to mark in use, False to mark as free
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                UPDATE disks
                SET in_use = %s,
                    reservation_id = %s,
                    last_used = %s
                WHERE user_id = %s AND disk_name = %s
            """, (in_use, reservation_id if in_use else None, datetime.now(UTC), user_id, disk_name))
            
            if cur.rowcount > 0:
                logger.info(f"Marked disk '{disk_name}' as {'in use' if in_use else 'free'}")
                return True
            else:
                logger.warning(f"No disk found: '{disk_name}' for user {user_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error marking disk '{disk_name}' in use: {e}")
        return False


def mark_disk_deleted(user_id: str, disk_name: str, delete_date: Optional[datetime] = None) -> bool:
    """
    Soft-delete a disk by marking it as deleted.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
        delete_date: Optional deletion date (defaults to today)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if delete_date is None:
            delete_date = datetime.now(UTC).date()
        
        with get_db_cursor() as cur:
            cur.execute("""
                UPDATE disks
                SET is_deleted = TRUE,
                    delete_date = %s,
                    in_use = FALSE,
                    reservation_id = NULL
                WHERE user_id = %s AND disk_name = %s
            """, (delete_date, user_id, disk_name))
            
            if cur.rowcount > 0:
                logger.info(f"Marked disk '{disk_name}' as deleted with date {delete_date}")
                return True
            else:
                logger.warning(f"No disk found: '{disk_name}' for user {user_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error marking disk '{disk_name}' as deleted: {e}")
        return False


def get_disks_in_use() -> List[Dict[str, Any]]:
    """
    Get all disks currently in use.
    
    Returns:
        List of disk dictionaries
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            cur.execute("""
                SELECT * FROM disks
                WHERE in_use = TRUE AND is_deleted = FALSE
                ORDER BY last_used DESC
            """)
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error getting disks in use: {e}")
        return []


def get_disks_pending_deletion() -> List[Dict[str, Any]]:
    """
    Get all disks marked for deletion.
    
    Returns:
        List of disk dictionaries
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            cur.execute("""
                SELECT * FROM disks
                WHERE is_deleted = TRUE
                ORDER BY delete_date ASC
            """)
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error getting disks pending deletion: {e}")
        return []


def update_disk_operation(
    user_id: str, 
    disk_name: str, 
    operation_id: str,
    operation_status: str,
    operation_error: Optional[str] = None
) -> bool:
    """
    Update disk operation status.
    
    Args:
        user_id: The user ID
        disk_name: The disk name
        operation_id: The operation UUID
        operation_status: The operation status
        operation_error: Optional error message
    
    Returns:
        True if successful, False otherwise
    """
    try:
        updates = {
            'operation_id': operation_id,
            'operation_status': operation_status,
        }
        
        if operation_error is not None:
            updates['operation_error'] = operation_error
        
        return update_disk(user_id, disk_name, updates)
        
    except Exception as e:
        logger.error(f"Error updating disk operation for '{disk_name}': {e}")
        return False

