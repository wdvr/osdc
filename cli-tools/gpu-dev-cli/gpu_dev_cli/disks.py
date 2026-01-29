"""
Disk management for GPU Dev CLI
Handles named persistent disks with snapshot-first workflow

All disk operations now use the API service instead of direct DynamoDB/SQS access.
"""

import re
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timezone
from .config import Config


def get_disk_in_use_status(disk_name: str, user_id: str, config: Config) -> Tuple[bool, Optional[str]]:
    """
    Check if a disk is currently in use by any reservation via API.
    Returns (is_in_use, reservation_id)

    Uses the API to get disk info which includes in_use status and reservation_id.
    """
    from .api_client import APIClient
    
    try:
        api_client = APIClient(config)
        disk_info = api_client.get_disk_info(disk_name)
        
        is_in_use = disk_info.get('in_use', False)
        reservation_id = disk_info.get('reservation_id')
        
        return is_in_use, reservation_id

    except Exception as e:
        # If disk doesn't exist or API error, assume not in use
        # This matches the old behavior of returning False on errors
        return False, None


def list_disks(user_id: str, config: Config) -> List[Dict]:
    """
    List all disks for a user via API.
    Returns list of disk info dicts with: name, size, last_used, created_at, snapshot_count, in_use, reservation_id
    """
    from .api_client import APIClient
    
    try:
        api_client = APIClient(config)
        response = api_client.list_disks()
        
        disks = []
        for disk_item in response.get('disks', []):
            # Parse datetime strings from API
            created_at_str = disk_item.get('created_at')
            last_used_str = disk_item.get('last_used')

            created_at = datetime.fromisoformat(created_at_str.replace('Z', '+00:00')) if created_at_str else None
            last_used = datetime.fromisoformat(last_used_str.replace('Z', '+00:00')) if last_used_str else None

            # Parse delete_date if present (it's a date string, not datetime)
            delete_date = disk_item.get('delete_date')

            disks.append({
                'name': disk_item.get('disk_name'),
                'size_gb': disk_item.get('size_gb', 0),
                'disk_size': None,  # Legacy field, not in API response
                'created_at': created_at,
                'last_used': last_used,
                'snapshot_count': disk_item.get('snapshot_count', 0),
                'pending_snapshot_count': 0,  # Not tracked in new system
                'in_use': disk_item.get('in_use', False),
                'is_backing_up': disk_item.get('is_backing_up', False),
                'reservation_id': disk_item.get('reservation_id'),
                'is_deleted': disk_item.get('is_deleted', False),
                'delete_date': delete_date,
            })

        # Sort by last_used (most recent first)
        disks.sort(key=lambda d: d['last_used'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

        return disks
        
    except Exception as e:
        print(f"Error listing disks: {e}")
        return []


def create_disk(disk_name: str, user_id: str, config: Config) -> Optional[str]:
    """
    Create a new disk by sending request to API service.
    Job processor will create the disk entry in PostgreSQL.
    Returns operation_id on success, None on failure.
    """
    from .api_client import APIClient

    # Check if disk already exists
    existing_disks = list_disks(user_id, config)
    if any(d['name'] == disk_name for d in existing_disks):
        print(f"Error: Disk '{disk_name}' already exists")
        return None

    # Validate disk name (alphanumeric + hyphens + underscores)
    if not re.match(r'^[a-zA-Z0-9_-]+$', disk_name):
        print(f"Error: Disk name must contain only letters, numbers, hyphens, and underscores")
        return None

    # Send create request via API
    try:
        api_client = APIClient(config)
        response = api_client.create_disk(disk_name=disk_name)
        return response.get('operation_id')

    except Exception as e:
        print(f"Error sending create request: {e}")
        return None


def list_disk_content(disk_name: str, user_id: str, config: Config) -> Optional[str]:
    """
    Fetch and return the contents of the latest snapshot for a disk via API.
    Returns contents string or None if not found.
    """
    from .api_client import APIClient
    
    try:
        api_client = APIClient(config)
        response = api_client.get_disk_content(disk_name)
        
        # Check if there's a message (no content available)
        message = response.get('message')
        if message:
            print(message)
            return None

        # Return the content
        content = response.get('content')
        if content is None:
            print(f"No snapshot contents available for disk '{disk_name}'")
            return None

        return content
        
    except Exception as e:
        print(f"Error fetching disk content: {e}")
        return None


def delete_disk(disk_name: str, user_id: str, config: Config) -> Optional[str]:
    """
    Soft delete a disk by sending delete request to API service.
    Job processor will handle marking in PostgreSQL and tagging snapshots.
    Returns operation_id on success, None on failure.
    """
    from .api_client import APIClient

    # Check if disk exists
    disks = list_disks(user_id, config)
    disk = next((d for d in disks if d['name'] == disk_name), None)

    if not disk:
        print(f"Error: Disk '{disk_name}' not found")
        return None

    # Check if disk is in use
    if disk['in_use']:
        print(f"Error: Cannot delete disk '{disk_name}' - it is currently in use")
        print(f"Reservation ID: {disk['reservation_id']}")
        return None

    # Send delete request via API
    try:
        api_client = APIClient(config)
        response = api_client.delete_disk(disk_name=disk_name)
        return response.get('operation_id')

    except Exception as e:
        print(f"Error sending delete request: {e}")
        return None


def poll_disk_operation(
    operation_id: str,
    operation_type: str,
    disk_name: str,
    user_id: str,
    config: Config,
    timeout_seconds: int = 60
) -> Tuple[bool, str]:
    """
    Poll API for disk operation completion.

    Args:
        operation_id: Operation ID returned from create/delete
        operation_type: 'create' or 'delete'
        disk_name: Name of the disk
        user_id: User ID
        config: Config object
        timeout_seconds: Max time to wait

    Returns:
        Tuple of (success, message)
    """
    from .api_client import APIClient
    import time

    api_client = APIClient(config)
    start_time = time.time()
    poll_interval = 2  # seconds

    while time.time() - start_time < timeout_seconds:
        try:
            # Poll operation status via API
            status = api_client.get_disk_operation_status(disk_name, operation_id)
            
            operation_status = status.get('status', 'unknown')
            is_completed = status.get('completed', False)
            error = status.get('error')
            
            if is_completed:
                if operation_status == 'completed':
                    if operation_type == 'create':
                        return True, f"Disk '{disk_name}' created successfully"
                    else:  # delete
                        delete_date = status.get('delete_date', 'in 30 days')
                        return True, f"Disk '{disk_name}' marked for deletion. Snapshots will be permanently deleted on {delete_date}"
                elif operation_status == 'failed':
                    error_msg = error or "Unknown error"
                    return False, f"Disk operation failed: {error_msg}"

            time.sleep(poll_interval)

        except Exception as e:
            # If operation not found yet (404), continue polling
            # For other errors, continue polling as well
            time.sleep(poll_interval)

    # Timeout
    if operation_type == 'create':
        return False, f"Timed out waiting for disk '{disk_name}' to be created. It may still be processing."
    else:
        return False, f"Timed out waiting for disk '{disk_name}' deletion to complete. It may still be processing."


def rename_disk(old_name: str, new_name: str, user_id: str, config: Config) -> bool:
    """
    Rename a disk via API.
    Returns True on success, False on failure.
    """
    from .api_client import APIClient

    # Validate new disk name
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name):
        print(f"Error: Disk name must contain only letters, numbers, hyphens, and underscores")
        return False

    print(f"Renaming disk '{old_name}' to '{new_name}'...")

    try:
        api_client = APIClient(config)
        response = api_client.rename_disk(old_name, new_name)
        
        message = response.get('message', '')
        snapshots_updated = response.get('snapshots_updated', 0)
        errors = response.get('errors', [])
        
        # Print the result message
        print(f"✓ {message}")
        
        # If there were any errors, print them
        if errors:
            print(f"⚠ Some snapshots could not be updated:")
            for error in errors:
                print(f"  ✗ {error}")
        
        return True

    except Exception as e:
        error_msg = str(e)
        
        # Parse HTTP errors for better messages
        if '404' in error_msg or 'not found' in error_msg.lower():
            print(f"Error: Disk '{old_name}' not found")
        elif '409' in error_msg or 'conflict' in error_msg.lower():
            if 'in use' in error_msg.lower():
                print(f"Error: Cannot rename disk '{old_name}' - it is currently in use")
            elif 'already exists' in error_msg.lower():
                print(f"Error: Disk '{new_name}' already exists")
            else:
                print(f"Error: {error_msg}")
        elif '410' in error_msg or 'gone' in error_msg.lower():
            print(f"Error: Disk '{old_name}' is marked for deletion")
        else:
            print(f"Error renaming disk: {error_msg}")
        
        return False
