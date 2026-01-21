"""
Shared snapshot utilities for GPU development server services
"""

import boto3
import time
import logging
import os
import subprocess
import json
from kubernetes import client
from kubernetes.stream import stream
from decimal import Decimal

from .db_pool import get_db_cursor

logger = logging.getLogger(__name__)
ec2_client = boto3.client("ec2")
s3_client = boto3.client("s3")


def safe_create_snapshot(volume_id, user_id, snapshot_type="shutdown", disk_name=None, content_s3_path=None, disk_size=None):
    """
    Safely create snapshot, avoiding duplicates if one is already in progress.
    
    Returns (snapshot_id, was_created) on success.
    
    IMPORTANT: If snapshot creation succeeds but database update fails, this function
    will attempt to delete the snapshot and raise an exception to prevent inconsistent state.
    The operation is atomic: both AWS snapshot AND database update must succeed.

    Args:
        volume_id: EBS volume ID
        user_id: User identifier (email or username)
        snapshot_type: Type of snapshot (shutdown, migration, etc.)
        disk_name: Named disk identifier (for tagged disks) - if provided, database will be updated
        content_s3_path: S3 path to disk contents listing
        disk_size: Disk usage size (e.g., "1.2G") from du -sh
        
    Returns:
        tuple: (snapshot_id, was_created) where was_created is True for new snapshots, False for existing
        
    Raises:
        Exception: If snapshot creation fails, or if database update fails (after attempting cleanup)
    """
    try:
        logger.info(f"Checking for existing snapshots for volume {volume_id}")

        # Check for any in-progress snapshots for this volume
        ongoing_response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=[
                {"Name": "volume-id", "Values": [volume_id]},
                {"Name": "status", "Values": ["pending"]}
            ]
        )

        ongoing_snapshots = ongoing_response.get('Snapshots', [])
        if ongoing_snapshots:
            latest_ongoing = max(ongoing_snapshots, key=lambda s: s['StartTime'])
            logger.info(f"Found ongoing snapshot {latest_ongoing['SnapshotId']} for volume {volume_id}")
            return latest_ongoing['SnapshotId'], False

        # No ongoing snapshots - create a new one
        logger.info(f"Creating new {snapshot_type} snapshot for volume {volume_id}")

        timestamp = int(time.time())

        tags = [
            {"Key": "Name", "Value": f"gpu-dev-{snapshot_type}-{user_id.split('@')[0]}-{timestamp}"},
            {"Key": "gpu-dev-user", "Value": user_id},
            {"Key": "gpu-dev-snapshot-type", "Value": snapshot_type},
            {"Key": "SnapshotType", "Value": snapshot_type},
            {"Key": "created_at", "Value": str(timestamp)},
        ]

        # Add disk_name tag if provided
        if disk_name:
            tags.append({"Key": "disk_name", "Value": disk_name})

        # Add content_s3_path tag if provided
        if content_s3_path:
            tags.append({"Key": "snapshot_content_s3", "Value": content_s3_path})

        # Add disk_size tag if provided
        if disk_size:
            tags.append({"Key": "disk_size", "Value": disk_size})

        snapshot_response = ec2_client.create_snapshot(
            VolumeId=volume_id,
            Description=f"gpu-dev {snapshot_type} snapshot for {user_id}" + (f" (disk: {disk_name})" if disk_name else "") + (f" ({disk_size})" if disk_size else ""),
            TagSpecifications=[{
                "ResourceType": "snapshot",
                "Tags": tags
            }]
        )

        snapshot_id = snapshot_response["SnapshotId"]
        logger.info(f"Created new snapshot {snapshot_id} for volume {volume_id}" + (f" (disk: {disk_name})" if disk_name else "") + (f" size: {disk_size}" if disk_size else ""))

        # Update PostgreSQL to mark disk as backing up
        # CRITICAL: If this fails, we must not return success, even though snapshot was created
        if disk_name:
            try:
                logger.debug(f"Updating database: marking disk '{disk_name}' as backing up")
                with get_db_cursor() as cur:
                    cur.execute("""
                        UPDATE disks
                        SET is_backing_up = TRUE,
                            pending_snapshot_count = COALESCE(pending_snapshot_count, 0) + 1
                        WHERE user_id = %s AND disk_name = %s
                    """, (user_id, disk_name))
                    
                    # Verify the update actually affected a row
                    if cur.rowcount == 0:
                        raise Exception(f"Disk '{disk_name}' not found in database for user {user_id}")
                
                logger.debug(f"Updated database for disk '{disk_name}' - marked as backing up")
            except Exception as db_error:
                # Database update failed - snapshot created but database state is inconsistent
                logger.error(
                    f"CRITICAL: Snapshot {snapshot_id} created successfully, "
                    f"but database update failed for disk '{disk_name}': {db_error}"
                )
                
                # Attempt to clean up the snapshot to maintain consistency
                try:
                    logger.warning(f"Attempting to delete snapshot {snapshot_id} to maintain consistency")
                    ec2_client.delete_snapshot(SnapshotId=snapshot_id)
                    logger.info(f"Successfully deleted snapshot {snapshot_id}")
                except Exception as cleanup_error:
                    logger.error(
                        f"Failed to delete snapshot {snapshot_id}: {cleanup_error}. "
                        f"Snapshot exists but is not tracked in database. Manual cleanup required!"
                    )
                
                # Propagate the error so caller knows the operation failed
                raise Exception(
                    f"Snapshot creation failed: database update error for disk '{disk_name}': {db_error}"
                ) from db_error

        return snapshot_id, True

    except Exception as e:
        logger.error(f"Error creating snapshot for volume {volume_id}: {str(e)}")
        return None, False


def create_pod_shutdown_snapshot(volume_id, user_id, snapshot_type="shutdown"):
    """
    Create a snapshot when pod is shutting down.
    """
    try:
        if not volume_id:
            logger.info(f"No persistent volume for user {user_id} - skipping {snapshot_type} snapshot")
            return None

        logger.info(f"Creating {snapshot_type} snapshot for user {user_id}, volume {volume_id}")

        # Create snapshot (or get existing one if in progress)
        snapshot_id, was_created = safe_create_snapshot(volume_id, user_id, snapshot_type)

        if was_created:
            logger.info(f"Started {snapshot_type} snapshot {snapshot_id} for user {user_id}")
        else:
            logger.info(f"Using existing snapshot {snapshot_id} for user {user_id}")

        return snapshot_id

    except Exception as e:
        logger.error(f"Error creating {snapshot_type} snapshot: {str(e)}")
        return None


def update_disk_snapshot_completed(user_id, disk_name, size_gb=None, content_s3_path=None, disk_size=None):
    """
    Update PostgreSQL when a snapshot completes.
    Decrements pending_snapshot_count, increments snapshot_count, clears is_backing_up if no more pending.
    
    This operation is ATOMIC - all updates happen in a single query to prevent race conditions.

    Args:
        user_id: User identifier
        disk_name: Disk name
        size_gb: Volume size in GB (optional, updates size_gb if provided)
        content_s3_path: S3 path to snapshot contents (optional, updates latest_snapshot_content_s3 if provided)
        disk_size: Disk usage size like "1.2G" from du -sh (optional, updates disk_size if provided)
    """
    try:
        logger.info(f"Updating database: snapshot completed for disk '{disk_name}'")

        # Build update query dynamically
        from datetime import datetime, UTC
        
        # ATOMIC UPDATE: All changes in a single query to prevent race conditions
        # The CASE statement ensures is_backing_up is cleared atomically when count reaches 0
        set_clauses = [
            "snapshot_count = COALESCE(snapshot_count, 0) + 1",
            "pending_snapshot_count = GREATEST(COALESCE(pending_snapshot_count, 1) - 1, 0)",
            # Atomically clear is_backing_up when pending count reaches 0
            "is_backing_up = CASE WHEN GREATEST(COALESCE(pending_snapshot_count, 1) - 1, 0) <= 0 THEN FALSE ELSE is_backing_up END",
            "last_used = %s"
        ]
        params = [datetime.now(UTC)]

        if size_gb is not None:
            set_clauses.append("size_gb = %s")
            params.append(int(size_gb))

        if content_s3_path is not None:
            set_clauses.append("latest_snapshot_content_s3 = %s")
            params.append(content_s3_path)

        if disk_size is not None:
            set_clauses.append("disk_size = %s")
            params.append(disk_size)

        # Add user_id and disk_name for WHERE clause
        params.extend([user_id, disk_name])

        # Build query string WITHOUT f-strings (security best practice)
        # Note: set_clauses contains only hardcoded SQL fragments, no user input
        query = """
            UPDATE disks
            SET """ + ', '.join(set_clauses) + """
            WHERE user_id = %s AND disk_name = %s
        """

        with get_db_cursor() as cur:
            # Single atomic UPDATE - no race conditions!
            cur.execute(query, params)
            
            if cur.rowcount > 0:
                logger.info(f"Updated database for disk '{disk_name}' - snapshot completed")
            else:
                logger.warning(f"No disk found for user {user_id}, disk {disk_name}")

    except Exception as e:
        logger.warning(f"Could not update database for snapshot completion: {e}")


def cleanup_old_snapshots(user_id, keep_count=3, max_age_days=7, max_deletions_per_run=10):
    """
    Clean up old snapshots for a user, keeping only the most recent ones.
    Keeps 'keep_count' newest snapshots and deletes any older than max_age_days.
    Limited to max_deletions_per_run to prevent lambda timeouts.
    Returns number of snapshots deleted.
    """
    try:
        from datetime import datetime, timedelta, UTC

        logger.info(f"Cleaning up old snapshots for user {user_id}")

        # Get all snapshots for this user (with pagination)
        paginator = ec2_client.get_paginator('describe_snapshots')
        page_iterator = paginator.paginate(
            OwnerIds=["self"],
            Filters=[
                {"Name": "tag:gpu-dev-user", "Values": [user_id]},
                {"Name": "status", "Values": ["completed"]}
            ],
            PaginationConfig={'PageSize': 100}
        )

        snapshots = []
        for page in page_iterator:
            snapshots.extend(page.get('Snapshots', []))
        if len(snapshots) <= keep_count:
            logger.debug(f"User {user_id} has {len(snapshots)} snapshots, no cleanup needed")
            return 0

        # Sort by creation time (newest first)
        snapshots.sort(key=lambda s: s['StartTime'], reverse=True)

        cutoff_date = datetime.now(UTC) - timedelta(days=max_age_days)
        deleted_count = 0

        for i, snapshot in enumerate(snapshots):
            # Limit deletions per run to prevent timeouts
            if deleted_count >= max_deletions_per_run:
                logger.info(f"Reached max deletions per run ({max_deletions_per_run}) for user {user_id}")
                break

            snapshot_id = snapshot['SnapshotId']
            snapshot_date = snapshot['StartTime'].replace(tzinfo=None)

            # Keep the newest 'keep_count' snapshots
            if i < keep_count:
                logger.debug(f"Keeping recent snapshot {snapshot_id}")
                continue

            # Delete if older than cutoff date or beyond keep_count
            if snapshot_date < cutoff_date or i >= keep_count:
                try:
                    logger.info(f"Deleting old snapshot {snapshot_id} from {snapshot_date}")
                    ec2_client.delete_snapshot(SnapshotId=snapshot_id)
                    deleted_count += 1
                except Exception as delete_error:
                    logger.warning(f"Could not delete snapshot {snapshot_id}: {delete_error}")

        logger.info(f"Cleaned up {deleted_count} old snapshots for user {user_id}")
        return deleted_count

    except Exception as e:
        logger.error(f"Error cleaning up snapshots for user {user_id}: {str(e)}")
        return 0


def get_latest_snapshot(user_id, volume_id=None, include_pending=False):
    """
    Get the most recent snapshot for a user.
    If volume_id provided, gets snapshots for that specific volume.
    If include_pending is True, includes pending snapshots.
    Returns the latest snapshot dict or None.
    """
    try:
        status_values = ["completed"]
        if include_pending:
            status_values.extend(["pending"])

        filters = [
            {"Name": "tag:gpu-dev-user", "Values": [user_id]},
            {"Name": "status", "Values": status_values},
        ]

        if volume_id:
            filters.append({"Name": "volume-id", "Values": [volume_id]})

        # Use pagination to handle users with many snapshots
        paginator = ec2_client.get_paginator('describe_snapshots')
        page_iterator = paginator.paginate(
            OwnerIds=["self"],
            Filters=filters,
            PaginationConfig={'PageSize': 100}
        )

        snapshots = []
        for page in page_iterator:
            snapshots.extend(page.get('Snapshots', []))

        # Filter out soft-deleted snapshots (those with delete-date tag)
        active_snapshots = []
        for snap in snapshots:
            tags = {tag['Key']: tag['Value'] for tag in snap.get('Tags', [])}
            if 'delete-date' not in tags:
                active_snapshots.append(snap)

        if not active_snapshots:
            status_desc = "completed or pending" if include_pending else "completed"
            logger.info(f"No {status_desc} snapshots found for user {user_id}")
            return None

        # Get most recent snapshot by start time
        latest_snapshot = max(active_snapshots, key=lambda s: s['StartTime'])
        logger.info(
            f"Found latest snapshot {latest_snapshot['SnapshotId']} ({latest_snapshot['State']}) for user {user_id}")
        return latest_snapshot

    except Exception as e:
        logger.error(f"Error finding latest snapshot for user {user_id}: {str(e)}")
        return None


def cleanup_all_user_snapshots(max_users_per_run=20):
    """
    Run scheduled cleanup of old snapshots for all users.
    This runs separately from expiry processing.
    Limited to max_users_per_run to prevent lambda timeouts.
    """
    try:
        logger.info("Starting scheduled snapshot cleanup for all users")

        # Get all gpu-dev snapshots grouped by user (with pagination)
        paginator = ec2_client.get_paginator('describe_snapshots')
        page_iterator = paginator.paginate(
            OwnerIds=["self"],
            Filters=[
                {"Name": "tag-key", "Values": ["gpu-dev-user"]},
            ],
            PaginationConfig={'PageSize': 100}
        )

        all_snapshots = []
        for page in page_iterator:
            all_snapshots.extend(page.get('Snapshots', []))

        # Group snapshots by user
        users_snapshots = {}
        for snapshot in all_snapshots:
            user_tag = next((tag['Value'] for tag in snapshot['Tags'] if tag['Key'] == 'gpu-dev-user'), None)
            if user_tag:
                if user_tag not in users_snapshots:
                    users_snapshots[user_tag] = []
                users_snapshots[user_tag].append(snapshot)

        total_deleted = 0
        users_processed = 0

        # Sort users by number of snapshots (process users with most snapshots first)
        sorted_users = sorted(users_snapshots.keys(), key=lambda u: len(users_snapshots[u]), reverse=True)

        for user_id in sorted_users:
            if users_processed >= max_users_per_run:
                logger.info(f"Reached max users per run ({max_users_per_run}), will process remaining users in next run")
                break

            deleted_count = cleanup_old_snapshots(user_id)
            total_deleted += deleted_count
            users_processed += 1

        logger.info(
            f"Scheduled snapshot cleanup completed: cleaned up {total_deleted} snapshots for {users_processed}/{len(users_snapshots)} users")
        return total_deleted

    except Exception as e:
        logger.error(f"Error during scheduled snapshot cleanup: {str(e)}")
        return 0


def capture_disk_contents(pod_name, namespace, user_id, disk_name, snapshot_id, k8s_client=None, mount_path="/workspace"):
    """
    Capture disk contents via Kubernetes API exec and upload to S3.
    Returns tuple (s3_path, disk_size) or (None, None) if failed.

    Args:
        pod_name: Kubernetes pod name
        namespace: Kubernetes namespace
        user_id: User identifier
        disk_name: Named disk identifier
        snapshot_id: Snapshot ID for file naming
        k8s_client: Configured Kubernetes API client (required for EKS)
        mount_path: Mount point in pod (default: /workspace)

    Returns:
        tuple: (s3_path, disk_size) where disk_size is like "1.2G" or None if failed
    """
    try:
        bucket_name = os.environ.get('DISK_CONTENTS_BUCKET')
        if not bucket_name:
            logger.error("DISK_CONTENTS_BUCKET environment variable not set")
            return None, None

        logger.info(f"Capturing disk contents for disk '{disk_name}' in pod {pod_name}")

        # Use Kubernetes API to exec into pod and capture disk contents
        # Use tree for clean hierarchical view, fall back to find if tree not available
        exec_command = [
            "sh", "-c",
            f"du -sh {mount_path} 2>/dev/null && echo '---' && if command -v tree >/dev/null 2>&1; then tree -a -L 3 --dirsfirst --noreport -I '.oh-my-zsh|.git' {mount_path} 2>/dev/null | head -1000; else find {mount_path} -maxdepth 3 \\( -name '.oh-my-zsh' -o -name '.git' \\) -prune -o -print 2>/dev/null | sort | head -1000; fi"
        ]

        logger.debug(f"Running exec command in pod {pod_name}: {' '.join(exec_command)}")

        # Create Kubernetes API client with proper configuration
        v1 = client.CoreV1Api(k8s_client) if k8s_client else client.CoreV1Api()

        # Execute command in pod
        disk_size = None
        try:
            resp = stream(
                v1.connect_get_namespaced_pod_exec,
                pod_name,
                namespace,
                command=exec_command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _preload_content=False
            )

            # Read output
            contents = ""
            while resp.is_open():
                resp.update(timeout=1)
                if resp.peek_stdout():
                    contents += resp.read_stdout()
                if resp.peek_stderr():
                    stderr = resp.read_stderr()
                    if stderr:
                        logger.debug(f"stderr from exec: {stderr}")

            resp.close()

            if contents:
                logger.info(f"Successfully captured {len(contents)} bytes of disk contents")

                # Parse disk size from first line (format: "1.2G\t/home/dev")
                try:
                    first_line = contents.split('\n')[0]
                    if first_line and '\t' in first_line:
                        disk_size = first_line.split('\t')[0].strip()
                        logger.info(f"Disk size: {disk_size}")
                except Exception as parse_error:
                    logger.warning(f"Could not parse disk size: {parse_error}")
            else:
                logger.warning(f"No contents captured from pod {pod_name}")
                contents = f"Pod {pod_name} returned empty contents.\n\nThis snapshot was created but disk may be empty."

        except Exception as exec_error:
            logger.warning(f"Kubernetes exec failed: {exec_error}")
            contents = f"Failed to capture contents: {str(exec_error)}\n\nThis snapshot was created but contents could not be listed."

        # Upload to S3
        s3_key = f"{user_id}/{disk_name}/{snapshot_id}-contents.txt"
        s3_path = f"s3://{bucket_name}/{s3_key}"

        logger.info(f"Uploading disk contents to {s3_path}")

        metadata = {
            'user_id': user_id,
            'disk_name': disk_name,
            'snapshot_id': snapshot_id,
            'pod_name': pod_name,
            'capture_time': str(int(time.time()))
        }

        # Add disk size to metadata if available
        if disk_size:
            metadata['disk_size'] = disk_size

        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=contents.encode('utf-8'),
            ContentType='text/plain',
            Metadata=metadata
        )

        logger.info(f"Successfully uploaded disk contents to {s3_path}")
        return s3_path, disk_size

    except Exception as e:
        logger.error(f"Error capturing disk contents: {str(e)}")
        return None, None


def get_snapshot_contents(snapshot_id=None, s3_path=None):
    """
    Fetch snapshot contents from S3.
    Either snapshot_id or s3_path must be provided.

    Args:
        snapshot_id: Snapshot ID to fetch contents for (will look up S3 path from tags)
        s3_path: Direct S3 path (e.g., s3://bucket/user/disk/snap-123-contents.txt)

    Returns:
        str: Contents text or None if not found
    """
    try:
        # If snapshot_id provided, look up S3 path from tags
        if snapshot_id and not s3_path:
            logger.info(f"Looking up S3 path for snapshot {snapshot_id}")
            response = ec2_client.describe_snapshots(SnapshotIds=[snapshot_id])

            if not response.get('Snapshots'):
                logger.error(f"Snapshot {snapshot_id} not found")
                return None

            snapshot = response['Snapshots'][0]
            tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}
            s3_path = tags.get('snapshot_content_s3')

            if not s3_path:
                logger.warning(f"Snapshot {snapshot_id} has no content_s3_path tag")
                return None

        if not s3_path:
            logger.error("No S3 path provided or found")
            return None

        # Parse S3 path (s3://bucket/key)
        if not s3_path.startswith('s3://'):
            logger.error(f"Invalid S3 path format: {s3_path}")
            return None

        path_parts = s3_path[5:].split('/', 1)  # Remove 's3://' and split bucket/key
        if len(path_parts) != 2:
            logger.error(f"Invalid S3 path format: {s3_path}")
            return None

        bucket_name, s3_key = path_parts

        logger.info(f"Fetching disk contents from {s3_path}")

        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        contents = response['Body'].read().decode('utf-8')

        logger.info(f"Successfully fetched {len(contents)} bytes from S3")
        return contents

    except s3_client.exceptions.NoSuchKey:
        logger.error(f"S3 object not found: {s3_path}")
        return None
    except Exception as e:
        logger.error(f"Error fetching snapshot contents: {str(e)}")
        return None
