"""
Shared snapshot utilities for GPU development server lambdas
"""

import boto3
import time
import logging
import os
import subprocess
import json

logger = logging.getLogger(__name__)
ec2_client = boto3.client("ec2")
s3_client = boto3.client("s3")


def safe_create_snapshot(volume_id, user_id, snapshot_type="shutdown", disk_name=None, content_s3_path=None):
    """
    Safely create snapshot, avoiding duplicates if one is already in progress.
    Returns (snapshot_id, was_created)

    Args:
        volume_id: EBS volume ID
        user_id: User identifier (email or username)
        snapshot_type: Type of snapshot (shutdown, migration, etc.)
        disk_name: Named disk identifier (for tagged disks)
        content_s3_path: S3 path to disk contents listing
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

        snapshot_response = ec2_client.create_snapshot(
            VolumeId=volume_id,
            Description=f"gpu-dev {snapshot_type} snapshot for {user_id}" + (f" (disk: {disk_name})" if disk_name else ""),
            TagSpecifications=[{
                "ResourceType": "snapshot",
                "Tags": tags
            }]
        )

        snapshot_id = snapshot_response["SnapshotId"]
        logger.info(f"Created new snapshot {snapshot_id} for volume {volume_id}" + (f" (disk: {disk_name})" if disk_name else ""))
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


def cleanup_old_snapshots(user_id, keep_count=3, max_age_days=7, max_deletions_per_run=10):
    """
    Clean up old snapshots for a user, keeping only the most recent ones.
    Keeps 'keep_count' newest snapshots and deletes any older than max_age_days.
    Limited to max_deletions_per_run to prevent lambda timeouts.
    Returns number of snapshots deleted.
    """
    try:
        from datetime import datetime, timedelta

        logger.info(f"Cleaning up old snapshots for user {user_id}")

        # Get all snapshots for this user
        response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=[
                {"Name": "tag:gpu-dev-user", "Values": [user_id]},
                {"Name": "status", "Values": ["completed"]}
            ]
        )

        snapshots = response.get('Snapshots', [])
        if len(snapshots) <= keep_count:
            logger.debug(f"User {user_id} has {len(snapshots)} snapshots, no cleanup needed")
            return 0

        # Sort by creation time (newest first)
        snapshots.sort(key=lambda s: s['StartTime'], reverse=True)

        cutoff_date = datetime.now() - timedelta(days=max_age_days)
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

        response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=filters
        )

        snapshots = response.get('Snapshots', [])
        if not snapshots:
            status_desc = "completed or pending" if include_pending else "completed"
            logger.info(f"No {status_desc} snapshots found for user {user_id}")
            return None

        # Get most recent snapshot by start time
        latest_snapshot = max(snapshots, key=lambda s: s['StartTime'])
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

        # Get all gpu-dev snapshots grouped by user
        response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=[
                {"Name": "tag-key", "Values": ["gpu-dev-user"]},
            ]
        )

        # Group snapshots by user
        users_snapshots = {}
        for snapshot in response.get('Snapshots', []):
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


def capture_disk_contents(pod_name, namespace, user_id, disk_name, snapshot_id, mount_path="/workspace"):
    """
    Capture disk contents via kubectl exec and upload to S3.
    Returns S3 path or None if failed.

    Args:
        pod_name: Kubernetes pod name
        namespace: Kubernetes namespace
        user_id: User identifier
        disk_name: Named disk identifier
        snapshot_id: Snapshot ID for file naming
        mount_path: Mount point in pod (default: /workspace)
    """
    try:
        bucket_name = os.environ.get('DISK_CONTENTS_BUCKET')
        if not bucket_name:
            logger.error("DISK_CONTENTS_BUCKET environment variable not set")
            return None

        logger.info(f"Capturing disk contents for disk '{disk_name}' in pod {pod_name}")

        # Run ls -R command via kubectl exec to capture disk contents
        # Limit depth to 2 levels to avoid huge outputs
        kubectl_cmd = [
            "kubectl", "exec", "-n", namespace, pod_name, "--",
            "sh", "-c",
            f"ls -lah {mount_path} && echo '---' && find {mount_path} -maxdepth 2 -type f -o -type d 2>/dev/null | head -1000"
        ]

        logger.debug(f"Running kubectl command: {' '.join(kubectl_cmd)}")

        result = subprocess.run(
            kubectl_cmd,
            capture_output=True,
            text=True,
            timeout=60  # 1 minute timeout
        )

        if result.returncode != 0:
            logger.warning(f"kubectl exec failed with return code {result.returncode}: {result.stderr}")
            # Try without kubectl - might be running in different context
            contents = f"Failed to capture contents: {result.stderr}\n\nThis snapshot was created but contents could not be listed."
        else:
            contents = result.stdout
            logger.info(f"Successfully captured {len(contents)} bytes of disk contents")

        # Upload to S3
        s3_key = f"{user_id}/{disk_name}/{snapshot_id}-contents.txt"
        s3_path = f"s3://{bucket_name}/{s3_key}"

        logger.info(f"Uploading disk contents to {s3_path}")

        s3_client.put_object(
            Bucket=bucket_name,
            Key=s3_key,
            Body=contents.encode('utf-8'),
            ContentType='text/plain',
            Metadata={
                'user_id': user_id,
                'disk_name': disk_name,
                'snapshot_id': snapshot_id,
                'pod_name': pod_name,
                'capture_time': str(int(time.time()))
            }
        )

        logger.info(f"Successfully uploaded disk contents to {s3_path}")
        return s3_path

    except subprocess.TimeoutExpired:
        logger.error(f"Timeout while capturing disk contents for pod {pod_name}")
        return None
    except Exception as e:
        logger.error(f"Error capturing disk contents: {str(e)}")
        return None


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
