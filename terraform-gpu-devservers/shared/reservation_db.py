"""
Reservation Database Operations

This module provides database operations for GPU reservations, replacing DynamoDB
interactions with PostgreSQL queries. All functions use the connection pool from
db_pool.py for efficient database access.

Usage:
    from shared.reservation_db import (
        create_reservation,
        get_reservation,
        update_reservation,
        delete_reservation,
        list_reservations_by_user,
        list_reservations_by_status
    )
"""

import json
import logging
from datetime import datetime, timezone, UTC
from typing import Any, Dict, List, Optional

from .db_pool import get_db_cursor

logger = logging.getLogger(__name__)


def create_reservation(reservation_data: Dict[str, Any]) -> bool:
    """
    Create a new reservation record in PostgreSQL.
    
    Args:
        reservation_data: Dictionary containing reservation fields
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Required fields
        reservation_id = reservation_data['reservation_id']
        user_id = reservation_data['user_id']
        status = reservation_data['status']
        duration_hours = reservation_data['duration_hours']
        created_at = reservation_data.get('created_at', datetime.now(UTC))
        
        # Optional fields with defaults
        gpu_type = reservation_data.get('gpu_type')
        gpu_count = reservation_data.get('gpu_count')
        instance_type = reservation_data.get('instance_type')
        launched_at = reservation_data.get('launched_at')
        expires_at = reservation_data.get('expires_at')
        name = reservation_data.get('name')
        github_user = reservation_data.get('github_user')
        pod_name = reservation_data.get('pod_name')
        namespace = reservation_data.get('namespace', 'default')
        node_ip = reservation_data.get('node_ip')
        node_port = reservation_data.get('node_port')
        ssh_command = reservation_data.get('ssh_command')
        jupyter_enabled = reservation_data.get('jupyter_enabled', False)
        jupyter_url = reservation_data.get('jupyter_url')
        jupyter_port = reservation_data.get('jupyter_port')
        jupyter_token = reservation_data.get('jupyter_token')
        jupyter_error = reservation_data.get('jupyter_error')
        ebs_volume_id = reservation_data.get('ebs_volume_id')
        disk_name = reservation_data.get('disk_name')
        failure_reason = reservation_data.get('failure_reason')
        current_detailed_status = reservation_data.get('current_detailed_status')
        status_history = reservation_data.get('status_history', [])
        pod_logs = reservation_data.get('pod_logs')
        warning = reservation_data.get('warning')
        secondary_users = reservation_data.get('secondary_users', [])
        is_multinode = reservation_data.get('is_multinode', False)
        master_reservation_id = reservation_data.get('master_reservation_id')
        node_index = reservation_data.get('node_index')
        total_nodes = reservation_data.get('total_nodes')
        cli_version = reservation_data.get('cli_version')
        
        with get_db_cursor() as cur:
            cur.execute("""
                INSERT INTO reservations (
                    reservation_id, user_id, status, gpu_type, gpu_count, instance_type,
                    duration_hours, created_at, launched_at, expires_at, name, github_user,
                    pod_name, namespace, node_ip, node_port, ssh_command,
                    jupyter_enabled, jupyter_url, jupyter_port, jupyter_token, jupyter_error,
                    ebs_volume_id, disk_name, failure_reason, current_detailed_status,
                    status_history, pod_logs, warning, secondary_users,
                    is_multinode, master_reservation_id, node_index, total_nodes, cli_version
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s
                )
            """, (
                reservation_id, user_id, status, gpu_type, gpu_count, instance_type,
                duration_hours, created_at, launched_at, expires_at, name, github_user,
                pod_name, namespace, node_ip, node_port, ssh_command,
                jupyter_enabled, jupyter_url, jupyter_port, jupyter_token, jupyter_error,
                ebs_volume_id, disk_name, failure_reason, current_detailed_status,
                json.dumps(status_history), pod_logs, warning, json.dumps(secondary_users),
                is_multinode, master_reservation_id, node_index, total_nodes, cli_version
            ))
            
        logger.info(f"Created reservation {reservation_id} for user {user_id}")
        return True
        
    except Exception as e:
        logger.error(f"Error creating reservation: {e}", exc_info=True)
        return False


def get_reservation(reservation_id: str) -> Optional[Dict[str, Any]]:
    """
    Get a single reservation by ID.
    
    Args:
        reservation_id: The reservation ID
    
    Returns:
        Reservation dictionary or None if not found
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                SELECT * FROM reservations
                WHERE reservation_id = %s
            """, (reservation_id,))
            
            result = cur.fetchone()
            if result:
                # Convert JSONB fields to Python objects
                result = dict(result)
                if 'status_history' in result and result['status_history']:
                    result['status_history'] = result['status_history']  # Already parsed by RealDictCursor
                if 'secondary_users' in result and result['secondary_users']:
                    result['secondary_users'] = result['secondary_users']  # Already parsed by RealDictCursor
                return result
            return None
            
    except Exception as e:
        logger.error(f"Error getting reservation {reservation_id}: {e}")
        return None


def update_reservation(reservation_id: str, updates: Dict[str, Any]) -> bool:
    """
    Update a reservation with the provided field updates.
    
    Args:
        reservation_id: The reservation ID to update
        updates: Dictionary of field names and values to update
    
    Returns:
        True if successful, False otherwise
    """
    try:
        if not updates:
            logger.warning(f"No updates provided for reservation {reservation_id}")
            return True
        
        # Build SET clause dynamically
        set_clauses = []
        params = []
        
        for field, value in updates.items():
            # Handle JSONB fields
            if field in ('status_history', 'secondary_users'):
                if not isinstance(value, str):
                    value = json.dumps(value)
            
            set_clauses.append(f"{field} = %s")
            params.append(value)
        
        # Add reservation_id for WHERE clause
        params.append(reservation_id)
        
        # Build query
        query = """
            UPDATE reservations
            SET """ + ', '.join(set_clauses) + """
            WHERE reservation_id = %s
        """
        
        with get_db_cursor() as cur:
            cur.execute(query, params)
            
            if cur.rowcount > 0:
                logger.debug(f"Updated reservation {reservation_id} with {len(updates)} fields")
                return True
            else:
                logger.warning(f"No reservation found with ID {reservation_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error updating reservation {reservation_id}: {e}", exc_info=True)
        return False


def delete_reservation(reservation_id: str) -> bool:
    """
    Delete a reservation from the database.
    
    Args:
        reservation_id: The reservation ID to delete
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                DELETE FROM reservations
                WHERE reservation_id = %s
            """, (reservation_id,))
            
            if cur.rowcount > 0:
                logger.info(f"Deleted reservation {reservation_id}")
                return True
            else:
                logger.warning(f"No reservation found with ID {reservation_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error deleting reservation {reservation_id}: {e}")
        return False


def list_reservations_by_user(user_id: str, status: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
    """
    List reservations for a specific user.
    
    Args:
        user_id: The user ID
        status: Optional status filter
        limit: Maximum number of results
    
    Returns:
        List of reservation dictionaries
    """
    try:
        with get_db_cursor() as cur:
            if status:
                cur.execute("""
                    SELECT * FROM reservations
                    WHERE user_id = %s AND status = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (user_id, status, limit))
            else:
                cur.execute("""
                    SELECT * FROM reservations
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                """, (user_id, limit))
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error listing reservations for user {user_id}: {e}")
        return []


def list_reservations_by_status(status: str, limit: int = 1000) -> List[Dict[str, Any]]:
    """
    List all reservations with a specific status.
    
    Args:
        status: The status to filter by
        limit: Maximum number of results
    
    Returns:
        List of reservation dictionaries
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                SELECT * FROM reservations
                WHERE status = %s
                ORDER BY created_at DESC
                LIMIT %s
            """, (status, limit))
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error listing reservations with status {status}: {e}")
        return []


def append_status_history(reservation_id: str, status_entry: Dict[str, Any]) -> bool:
    """
    Append a status entry to the reservation's status history.
    Atomically updates the JSONB array using PostgreSQL's || operator.
    
    Args:
        reservation_id: The reservation ID
        status_entry: Status entry dictionary with 'status', 'timestamp', 'message', etc.
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure timestamp is present
        if 'timestamp' not in status_entry:
            status_entry['timestamp'] = datetime.now(UTC).isoformat()
        
        with get_db_cursor() as cur:
            # Use PostgreSQL's || operator to append to JSONB array atomically
            cur.execute("""
                UPDATE reservations
                SET status_history = COALESCE(status_history, '[]'::jsonb) || %s::jsonb
                WHERE reservation_id = %s
            """, (json.dumps([status_entry]), reservation_id))
            
            if cur.rowcount > 0:
                logger.debug(f"Appended status to history for reservation {reservation_id}")
                return True
            else:
                logger.warning(f"No reservation found with ID {reservation_id}")
                return False
                
    except Exception as e:
        logger.error(f"Error appending status history for reservation {reservation_id}: {e}")
        return False


def add_secondary_user_atomic(reservation_id: str, username: str) -> bool:
    """
    Atomically add a secondary user to the reservation.
    
    Uses PostgreSQL's JSONB operators for atomic append without read-modify-write,
    preventing race conditions when multiple users are added simultaneously.
    Only adds the user if not already present.
    
    Args:
        reservation_id: The reservation ID
        username: The username to add
    
    Returns:
        True if successful, False otherwise
    """
    try:
        with get_db_cursor() as cur:
            # Use JSONB ? operator to check if user exists, then conditionally append
            # This is atomic - no race condition possible
            cur.execute("""
                UPDATE reservations
                SET secondary_users = CASE
                    WHEN COALESCE(secondary_users, '[]'::jsonb) ? %s
                    THEN secondary_users  -- User already exists, don't add
                    ELSE COALESCE(secondary_users, '[]'::jsonb) || %s::jsonb  -- Add user
                END
                WHERE reservation_id = %s
            """, (username, json.dumps([username]), reservation_id))
            
            if cur.rowcount > 0:
                logger.info(f"Added secondary user {username} to reservation {reservation_id}")
                return True
            else:
                logger.warning(f"Reservation {reservation_id} not found")
                return False
            
    except Exception as e:
        logger.error(f"Error adding secondary user to {reservation_id}: {e}", exc_info=True)
        return False


def list_multinode_reservations(master_reservation_id: str) -> List[Dict[str, Any]]:
    """
    Get all nodes in a multinode reservation.
    
    Args:
        master_reservation_id: The master reservation ID
    
    Returns:
        List of reservation dictionaries for all nodes
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                SELECT * FROM reservations
                WHERE master_reservation_id = %s OR reservation_id = %s
                ORDER BY node_index
            """, (master_reservation_id, master_reservation_id))
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error listing multinode reservations for {master_reservation_id}: {e}")
        return []


def count_active_reservations_by_gpu_type(gpu_type: str) -> int:
    """
    Count active reservations for a specific GPU type.
    
    Args:
        gpu_type: The GPU type to count
    
    Returns:
        Number of active reservations
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) as count
                FROM reservations
                WHERE gpu_type = %s 
                  AND status IN ('active', 'pending', 'preparing', 'queued')
            """, (gpu_type,))
            
            result = cur.fetchone()
            return result['count'] if result else 0
            
    except Exception as e:
        logger.error(f"Error counting active reservations for {gpu_type}: {e}")
        return 0


def list_expired_reservations(limit: int = 100) -> List[Dict[str, Any]]:
    """
    List reservations that have passed their expiration time.
    
    Args:
        limit: Maximum number of results
    
    Returns:
        List of expired reservation dictionaries
    """
    try:
        with get_db_cursor() as cur:
            cur.execute("""
                SELECT * FROM reservations
                WHERE expires_at IS NOT NULL
                  AND expires_at < NOW()
                  AND status IN ('active', 'pending', 'preparing')
                ORDER BY expires_at ASC
                LIMIT %s
            """, (limit,))
            
            results = cur.fetchall()
            return [dict(row) for row in results]
            
    except Exception as e:
        logger.error(f"Error listing expired reservations: {e}")
        return []


def update_reservation_status(
    reservation_id: str, 
    new_status: str, 
    detailed_status: Optional[str] = None,
    failure_reason: Optional[str] = None,
    add_to_history: bool = True,
    force: bool = False
) -> bool:
    """
    Update reservation status with protection for terminal states.
    
    Terminal states (cancelled, failed) cannot be overwritten unless force=True.
    This prevents race conditions where status updates overwrite cancellations.
    
    Args:
        reservation_id: The reservation ID
        new_status: The new status value
        detailed_status: Optional detailed status message
        failure_reason: Optional failure reason
        add_to_history: Whether to add entry to status_history
        force: If True, allow overwriting terminal states (use with caution!)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Terminal states that should not be overwritten
        TERMINAL_STATES = ['cancelled', 'failed']
        
        # Build the update
        set_clauses = []
        params = []
        
        set_clauses.append("status = %s")
        params.append(new_status)
        
        if detailed_status is not None:
            set_clauses.append("current_detailed_status = %s")
            params.append(detailed_status)
        
        if failure_reason is not None:
            set_clauses.append("failure_reason = %s")
            params.append(failure_reason)
        
        # Add updated_at timestamp
        set_clauses.append("updated_at = NOW()")
        
        # Add reservation_id for WHERE clause
        params.append(reservation_id)
        
        # Build query with terminal state protection
        if force:
            # Force mode: Update regardless of current status
            where_clause = "WHERE reservation_id = %s"
        else:
            # Normal mode: Only update if not in terminal state
            where_clause = f"""
                WHERE reservation_id = %s 
                AND status NOT IN ({','.join(['%s'] * len(TERMINAL_STATES))})
            """
            params.extend(TERMINAL_STATES)
        
        query = f"""
            UPDATE reservations
            SET {', '.join(set_clauses)}
            {where_clause}
        """
        
        with get_db_cursor() as cur:
            cur.execute(query, params)
            
            if cur.rowcount == 0:
                # Check if reservation exists and is in terminal state
                cur.execute("""
                    SELECT status FROM reservations 
                    WHERE reservation_id = %s
                """, (reservation_id,))
                
                row = cur.fetchone()
                if row:
                    current_status = row['status']
                    if current_status in TERMINAL_STATES:
                        logger.info(
                            f"Skipped status update for {reservation_id}: "
                            f"already in terminal state '{current_status}' "
                            f"(tried to set '{new_status}')"
                        )
                        return False
                else:
                    logger.warning(f"Reservation {reservation_id} not found")
                    return False
            
            logger.debug(f"Updated reservation {reservation_id} status to {new_status}")
            # Capture rowcount before exiting context manager (cursor closed after)
            rows_updated = cur.rowcount

        # Add to history if requested and update was successful
        if rows_updated > 0 and add_to_history:
            status_entry = {
                'status': new_status,
                'timestamp': datetime.now(UTC).isoformat(),
            }
            if detailed_status:
                status_entry['message'] = detailed_status
            if failure_reason:
                status_entry['failure_reason'] = failure_reason

            append_status_history(reservation_id, status_entry)

        return rows_updated > 0
        
    except Exception as e:
        logger.error(f"Error updating reservation status for {reservation_id}: {e}", exc_info=True)
        return False

