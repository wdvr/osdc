"""
Disk Reconciliation Module

Syncs EBS volume state from AWS into PostgreSQL database, ensuring
database records accurately reflect AWS reality.

Single source of truth: AWS EBS volumes

Reconciliation Rules:
1. Volume in AWS but not in DB → Create DB entry (is_deleted=False)
2. Volume in DB but deleted from AWS:
   - If is_deleted=False → Keep DB record, update in_use=False only
   - If is_deleted=True → Update all fields normally
3. Volume in both → Sync state from AWS to DB
"""

import logging
import random
import time
from datetime import UTC, datetime

from botocore.exceptions import ClientError

from .db_pool import get_db_cursor, get_db_transaction
from .disk_db import create_disk, update_disk

logger = logging.getLogger(__name__)


def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    Ensure a datetime is timezone-aware and in UTC.

    This is a defensive function to handle cases where datetimes might
    be naive (from AWS SDK or database). Per project timezone standard,
    all datetimes should be timezone-aware UTC.

    Args:
        dt: A datetime object (timezone-aware or naive) or None

    Returns:
        A timezone-aware datetime in UTC, or None if input was None
    """
    if dt is None:
        return None

    # If already timezone-aware, convert to UTC
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)

    # If naive, assume it's already in UTC and make it aware
    # This shouldn't happen with TIMESTAMP WITH TIME ZONE columns,
    # but we handle it defensively
    logger.warning(
        f"Encountered naive datetime {dt}, assuming UTC. "
        f"This should not happen - investigate data source."
    )
    return dt.replace(tzinfo=UTC)


def reconcile_all_disks(ec2_client) -> dict[str, int]:
    """
    Reconcile all disk records from AWS EBS volumes.

    Args:
        ec2_client: Boto3 EC2 client

    Returns:
        Dictionary with reconciliation statistics
    """
    stats = {
        "aws_volumes": 0,
        "db_records": 0,
        "synced": 0,
        "updated": 0,
        "created": 0,
        "errors": 0,
        "volume_id_conflicts": 0,
        "orphaned_db_active": 0,
        "orphaned_db_deleted": 0,
    }

    try:
        # 1. Get all gpu-dev volumes from AWS
        logger.info("Fetching all EBS volumes with gpu-dev-user tag")
        aws_volumes, aws_error = get_all_gpudev_volumes(ec2_client)

        if aws_error:
            # AWS fetch failed - abort reconciliation
            logger.error(
                f"Failed to fetch AWS volumes: {aws_error}. "
                f"Aborting reconciliation to prevent marking all "
                f"DB records as orphaned."
            )
            stats["errors"] += 1
            return stats

        stats["aws_volumes"] = len(aws_volumes)
        logger.info(f"Found {len(aws_volumes)} volumes in AWS")

        # 2. Get all disk records from database
        logger.info("Fetching all disk records from database")
        db_disks = get_all_disks_from_db()
        stats["db_records"] = len(db_disks)
        logger.info(f"Found {len(db_disks)} disk records in database")

        # 3. Build indexes for fast lookup
        aws_by_volume_id = {vol["volume_id"]: vol for vol in aws_volumes}
        db_by_volume_id = {
            disk["ebs_volume_id"]: disk
            for disk in db_disks
            if disk.get("ebs_volume_id")
        }

        # Also index DB by (user_id, disk_name) for orphaned AWS volumes
        # Detect and handle duplicates
        db_by_user_disk = {}
        for disk in db_disks:
            key = (disk["user_id"], disk["disk_name"])
            if key in db_by_user_disk:
                # Duplicate found - log critical error
                existing_disk = db_by_user_disk[key]
                logger.error(
                    f"DUPLICATE DISK DETECTED: disk_name='{disk['disk_name']}' "
                    f"user_id='{disk['user_id']}' appears multiple times. "
                    f"Disk IDs: {existing_disk['disk_id']} (kept), "
                    f"{disk['disk_id']} (skipped). "
                    f"Volume IDs: {existing_disk.get('ebs_volume_id')} vs "
                    f"{disk.get('ebs_volume_id')}. "
                    f"Manual cleanup required!"
                )
                stats["errors"] += 1
                # Keep the first occurrence (already in dict)
                continue
            db_by_user_disk[key] = disk

        # 4. Reconcile AWS volumes into database
        # Each volume is reconciled in its own transaction for atomicity
        for volume_id, aws_vol in aws_by_volume_id.items():
            try:
                # Wrap each volume reconciliation in a transaction
                # This ensures all DB operations for this volume are atomic
                # and prevents race conditions between concurrent runs
                with get_db_transaction():
                    if volume_id in db_by_volume_id:
                        # Volume exists in both - sync state
                        db_disk = db_by_volume_id[volume_id]
                        result = sync_volume_to_db(
                            aws_vol, db_disk, ec2_client
                        )
                        if result == "synced":
                            stats["synced"] += 1
                        elif result == "updated":
                            stats["updated"] += 1
                    else:
                        # Volume exists in AWS but not DB - import it
                        # Check if DB record by (user_id, disk_name) exists
                        # without volume_id
                        user_id = aws_vol.get("user_id")
                        disk_name = aws_vol.get("disk_name")

                        existing_record = db_by_user_disk.get(
                            (user_id, disk_name)
                        )

                        if existing_record:
                            # DB record exists - check for conflicts
                            existing_vol_id = existing_record.get(
                                "ebs_volume_id"
                            )

                            if existing_vol_id and existing_vol_id != volume_id:
                                # Different volume_id - check if it's
                                # a conflict or volume replacement
                                if existing_vol_id in aws_by_volume_id:
                                    # OLD volume still exists in AWS
                                    # This is a REAL conflict:
                                    # two volumes claiming same disk name
                                    logger.error(
                                        f"CONFLICT: DB record {disk_name} "
                                        f"for user {user_id} has volume_id "
                                        f"{existing_vol_id} (still in AWS) "
                                        f"but AWS volume {volume_id} has "
                                        f"same (user_id, disk_name). "
                                        f"Skipping - manual intervention "
                                        f"required."
                                    )
                                    stats["volume_id_conflicts"] += 1
                                    stats["errors"] += 1
                                    # Skip this volume, don't overwrite
                                    continue
                                else:
                                    # OLD volume deleted from AWS
                                    # This is volume replacement (OK)
                                    logger.info(
                                        f"Volume replacement detected: "
                                        f"{disk_name} for user {user_id} "
                                        f"was {existing_vol_id} (deleted), "
                                        f"now {volume_id}. Updating DB."
                                    )
                                    # Fall through to update logic

                            # Safe to link: volume_id is NULL, matches,
                            # or old volume was deleted (replacement)
                            logger.info(
                                f"Linking DB record {disk_name} to volume "
                                f"{volume_id}"
                            )
                            if update_volume_id_in_db(
                                existing_record, volume_id, aws_vol,
                                ec2_client
                            ):
                                stats["updated"] += 1
                            else:
                                stats["errors"] += 1
                        else:
                            # No DB record at all - create new one
                            if import_volume_to_db(aws_vol, ec2_client):
                                stats["created"] += 1
                                logger.info(
                                    f"Imported orphaned AWS volume "
                                    f"{volume_id} to database"
                                )
                            else:
                                stats["errors"] += 1
                # Transaction auto-commits on success, auto-rollbacks on error
            except Exception as vol_error:
                # Transaction automatically rolled back by context manager
                logger.error(
                    f"Error reconciling volume {volume_id}: {vol_error}",
                    exc_info=True
                )
                stats["errors"] += 1

        # 5. Check for orphaned database records (volume deleted in AWS)
        # Each orphaned record update is also done in a transaction
        for volume_id, db_disk in db_by_volume_id.items():
            if volume_id and volume_id not in aws_by_volume_id:
                # Database record exists but volume doesn't exist in AWS
                user_id = db_disk["user_id"]
                disk_name = db_disk["disk_name"]
                is_deleted = db_disk.get("is_deleted", False)

                if not is_deleted:
                    # Volume deleted in AWS but DB record is still active
                    # Rule: Keep DB record but update in_use=False
                    stats["orphaned_db_active"] += 1
                    logger.warning(
                        f"Orphaned active DB record: {disk_name} for "
                        f"user {user_id} (volume {volume_id} not in "
                        f"AWS) - marking in_use=False"
                    )

                    try:
                        # Wrap orphaned record update in transaction
                        with get_db_transaction():
                            updates = {
                                "in_use": False,
                                # Keep reservation_id for historical tracking
                                # Don't clear - allows audit of which
                                # reservation last used this disk
                            }
                            update_disk(user_id, disk_name, updates)
                            stats["updated"] += 1
                        # Transaction auto-commits on success
                    except Exception as update_error:
                        # Transaction auto-rollbacks on error
                        logger.error(
                            f"Error updating orphaned record "
                            f"{disk_name}: {update_error}"
                        )
                        stats["errors"] += 1
                else:
                    # Volume already marked as deleted in DB
                    stats["orphaned_db_deleted"] += 1
                    logger.debug(
                        f"DB record {disk_name} already marked deleted, "
                        f"volume {volume_id} not in AWS (expected)"
                    )

        logger.info(f"Disk reconciliation complete: {stats}")
        return stats

    except Exception as e:
        logger.error(
            f"Error during disk reconciliation: {e}",
            exc_info=True
        )
        stats["errors"] += 1
        return stats


def get_all_gpudev_volumes(
    ec2_client,
    max_retries: int = 5
) -> tuple[list[dict], str | None]:
    """
    Get all EBS volumes tagged with gpu-dev-user with retry logic.

    Handles AWS API rate limiting with exponential backoff.

    Args:
        ec2_client: Boto3 EC2 client
        max_retries: Maximum retry attempts (default: 5)

    Returns:
        Tuple of (volumes_list, error_message)
        - volumes_list: List of volume dictionaries with parsed metadata
        - error_message: None on success, error string on failure

        This allows caller to distinguish between:
        - ([], None): No volumes exist (legitimate empty state)
        - ([], "error"): AWS fetch failed (don't reconcile)
    """
    volumes = []

    for attempt in range(max_retries):
        try:
            # Use pagination to handle large number of volumes
            paginator = ec2_client.get_paginator('describe_volumes')
            page_iterator = paginator.paginate(
                Filters=[
                    {"Name": "tag-key", "Values": ["gpu-dev-user"]}
                ]
            )

            for page in page_iterator:
                for vol in page.get("Volumes", []):
                    # Parse volume into standardized format
                    volume_data = parse_volume_from_aws(vol)
                    if volume_data:
                        volumes.append(volume_data)

            # Success - return volumes with no error
            return volumes, None

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")

            # Check if it's a throttling error
            if error_code in [
                "RequestLimitExceeded",
                "Throttling",
                "TooManyRequestsException",
            ]:
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    base_wait = 2 ** attempt
                    jitter = random.uniform(0, 0.5 * base_wait)
                    wait_time = base_wait + jitter

                    logger.warning(
                        f"AWS API throttling fetching volumes "
                        f"(attempt {attempt + 1}/{max_retries}), "
                        f"waiting {wait_time:.2f}s before retry"
                    )
                    time.sleep(wait_time)
                    # Clear volumes list before retry
                    volumes = []
                    continue
                else:
                    # Max retries exhausted
                    error_msg = (
                        f"AWS API throttling: max retries "
                        f"({max_retries}) exhausted"
                    )
                    logger.error(error_msg)
                    return [], error_msg
            else:
                # Non-throttling error
                error_msg = f"AWS API error: {error_code} - {str(e)}"
                logger.error(
                    f"AWS API error fetching volumes: "
                    f"{error_code} - {e}",
                    exc_info=True
                )
                return [], error_msg

        except Exception as e:
            error_msg = f"Unexpected error: {str(e)}"
            logger.error(
                f"Error fetching volumes from AWS: {e}",
                exc_info=True
            )
            return [], error_msg

    # Should not reach here, but return error as fallback
    return [], "Max retries reached without success or error"


def parse_volume_from_aws(aws_volume: dict) -> dict | None:
    """
    Parse AWS volume response into standardized format.

    Extracts:
    - volume_id
    - size_gb
    - state (available, in-use, etc.)
    - attached_to (instance_id if attached)
    - created_at
    - tags (disk_name, user_id, reservation_id)
    """
    try:
        # Extract tags
        tags = {
            tag["Key"]: tag["Value"]
            for tag in aws_volume.get("Tags", [])
        }

        # Get attachment info
        # AWS allows multi-attach volumes in some configurations
        # A volume is "in use" if ANY attachment is in "attached" state
        attachments = aws_volume.get("Attachments", [])

        # Check all attachments, not just the first one
        attached_instances = [
            att.get("InstanceId")
            for att in attachments
            if att.get("State") == "attached"
        ]

        is_attached = len(attached_instances) > 0
        # Use first attached instance for backward compatibility
        # (most volumes have single attachment)
        attached_instance = (
            attached_instances[0] if attached_instances else None
        )

        # Log multi-attach volumes for observability
        if len(attached_instances) > 1:
            logger.info(
                f"Volume {aws_volume['VolumeId']} has multiple "
                f"attachments: {attached_instances}"
            )

        # Get disk_name from tags (try multiple keys)
        disk_name = (
            tags.get("disk-name") or
            tags.get("disk_name") or
            tags.get("Name")
        )

        # Get user_id from tags
        user_id = tags.get("gpu-dev-user")

        # Skip volumes without required tags
        if not disk_name or not user_id:
            logger.debug(
                f"Skipping volume {aws_volume['VolumeId']}: "
                f"missing disk_name={disk_name} or user_id={user_id}"
            )
            return None

        return {
            "volume_id": aws_volume["VolumeId"],
            "size_gb": aws_volume["Size"],
            "state": aws_volume["State"],
            "availability_zone": aws_volume["AvailabilityZone"],
            "created_at": aws_volume["CreateTime"],
            "is_attached": is_attached,
            "attached_instance": attached_instance,
            "disk_name": disk_name,
            "user_id": user_id,
            "reservation_id": (
                tags.get("reservation_id") or
                tags.get("reservation-id")
            ),
            "tags": tags,
        }
    except Exception as e:
        logger.error(
            f"Error parsing volume {aws_volume.get('VolumeId')}: {e}",
            exc_info=True
        )
        return None


def sync_volume_to_db(
    aws_vol: dict,
    db_disk: dict,
    ec2_client
) -> str:
    """
    Sync AWS volume state into existing database record.

    Returns:
        "synced" if no updates needed
        "updated" if updates were applied
        "error" if update failed
    """
    user_id = db_disk["user_id"]
    disk_name = db_disk["disk_name"]
    needs_update = False
    updates = {}

    # Check for state differences

    # 1. EBS Volume ID (in case it was missing before)
    if aws_vol["volume_id"] != db_disk.get("ebs_volume_id"):
        logger.info(
            f"Volume ID mismatch for {disk_name}: "
            f"AWS={aws_vol['volume_id']}, "
            f"DB={db_disk.get('ebs_volume_id')}"
        )
        updates["ebs_volume_id"] = aws_vol["volume_id"]
        needs_update = True

    # 2. Volume size
    if aws_vol["size_gb"] != db_disk.get("size_gb"):
        logger.info(
            f"Size mismatch for {disk_name}: "
            f"AWS={aws_vol['size_gb']}GB, DB={db_disk.get('size_gb')}GB"
        )
        updates["size_gb"] = aws_vol["size_gb"]
        needs_update = True

    # 3. In-use status
    aws_in_use = aws_vol["is_attached"]
    db_in_use = db_disk.get("in_use", False)

    if aws_in_use != db_in_use:
        logger.info(
            f"In-use mismatch for {disk_name}: "
            f"AWS={aws_in_use}, DB={db_in_use}"
        )
        updates["in_use"] = aws_in_use

        # Keep reservation_id even when detached
        # Don't clear - disk may be temporarily detached during:
        # - Migration between instances
        # - Backup operations
        # - Instance termination before reattachment
        # Preserving reservation_id allows tracking which reservation
        # last used this disk and aids in debugging/audit trails

        needs_update = True

    # 4. Snapshot count and backing up status
    try:
        snapshot_info = get_snapshot_info(
            ec2_client, aws_vol["volume_id"], user_id
        )

        if snapshot_info["count"] != db_disk.get("snapshot_count", 0):
            logger.info(
                f"Snapshot count mismatch for {disk_name}: "
                f"AWS={snapshot_info['count']}, "
                f"DB={db_disk.get('snapshot_count')}"
            )
            updates["snapshot_count"] = snapshot_info["count"]
            needs_update = True

        if (snapshot_info["is_backing_up"] !=
                db_disk.get("is_backing_up", False)):
            logger.info(
                f"Backup status mismatch for {disk_name}: "
                f"AWS={snapshot_info['is_backing_up']}, "
                f"DB={db_disk.get('is_backing_up')}"
            )
            updates["is_backing_up"] = snapshot_info["is_backing_up"]
            needs_update = True

        if snapshot_info["last_snapshot_at"]:
            # Only update if different (compare timestamps)
            # Normalize both to timezone-aware UTC for proper comparison
            db_last_snapshot = db_disk.get("last_snapshot_at")
            aws_snapshot_time = snapshot_info["last_snapshot_at"]

            # Ensure both are timezone-aware UTC datetimes
            db_last_snapshot_utc = ensure_utc(db_last_snapshot)
            aws_snapshot_time_utc = ensure_utc(aws_snapshot_time)

            if db_last_snapshot_utc != aws_snapshot_time_utc:
                logger.info(
                    f"Snapshot timestamp mismatch for {disk_name}: "
                    f"DB={db_last_snapshot_utc}, AWS={aws_snapshot_time_utc}"
                )
                updates["last_snapshot_at"] = aws_snapshot_time_utc
                needs_update = True

    except Exception as snapshot_error:
        logger.warning(
            f"Error getting snapshot info for {disk_name}: "
            f"{snapshot_error}"
        )
        # Continue without snapshot updates

    # Apply updates if needed
    if needs_update:
        logger.info(
            f"Syncing {disk_name} from AWS: {list(updates.keys())}"
        )
        success = update_disk(user_id, disk_name, updates)
        return "updated" if success else "error"

    return "synced"


def update_volume_id_in_db(
    db_disk: dict,
    volume_id: str,
    aws_vol: dict,
    ec2_client
) -> bool:
    """
    Update an existing DB record with volume_id from AWS and sync
    other fields.

    This handles the case where a DB record exists but is missing the
    ebs_volume_id.
    """
    user_id = db_disk["user_id"]
    disk_name = db_disk["disk_name"]

    try:
        updates = {
            "ebs_volume_id": volume_id,
            "size_gb": aws_vol["size_gb"],
            "in_use": aws_vol["is_attached"],
        }

        # Keep reservation_id for historical tracking
        # Don't clear even if not attached - preserves audit trail
        # of which reservation last used this disk

        # Get snapshot info
        snapshot_info = get_snapshot_info(ec2_client, volume_id, user_id)
        updates["snapshot_count"] = snapshot_info["count"]
        updates["is_backing_up"] = snapshot_info["is_backing_up"]
        if snapshot_info["last_snapshot_at"]:
            updates["last_snapshot_at"] = (
                snapshot_info["last_snapshot_at"]
            )

        logger.info(
            f"Linking DB record {disk_name} to volume {volume_id}"
        )
        return update_disk(user_id, disk_name, updates)

    except Exception as e:
        logger.error(
            f"Error updating volume_id for {disk_name}: {e}",
            exc_info=True
        )
        return False


def import_volume_to_db(aws_vol: dict, ec2_client) -> bool:
    """
    Import an AWS volume that doesn't exist in database.

    This handles "orphaned" volumes that exist in AWS but aren't
    tracked.
    Per user requirements: Create entry with is_deleted=False,
    operation_id=NULL, last_used=NULL
    """
    try:
        disk_name = aws_vol.get("disk_name")
        user_id = aws_vol.get("user_id")

        if not disk_name or not user_id:
            logger.warning(
                f"Volume {aws_vol['volume_id']} missing disk_name or "
                f"user_id tags, skipping"
            )
            return False

        # Get snapshot information
        snapshot_info = get_snapshot_info(
            ec2_client, aws_vol["volume_id"], user_id
        )

        # Create disk record per user requirements:
        # - is_deleted = False
        # - operation_id = NULL (not included)
        # - last_used = NULL (not included)
        disk_data = {
            "disk_name": disk_name,
            "user_id": user_id,
            "ebs_volume_id": aws_vol["volume_id"],
            "size_gb": aws_vol["size_gb"],
            "created_at": aws_vol["created_at"],
            "in_use": aws_vol["is_attached"],
            "reservation_id": aws_vol.get("reservation_id"),
            "is_backing_up": snapshot_info["is_backing_up"],
            "is_deleted": False,  # Per user requirements
            "snapshot_count": snapshot_info["count"],
            "last_snapshot_at": snapshot_info["last_snapshot_at"],
            # operation_id: NULL (not set)
            # last_used: NULL (not set)
        }

        logger.info(
            f"Importing orphaned volume {aws_vol['volume_id']} as "
            f"disk '{disk_name}' for user {user_id}"
        )
        return create_disk(disk_data)

    except Exception as e:
        logger.error(
            f"Error importing volume {aws_vol.get('volume_id')}: {e}",
            exc_info=True
        )
        return False


def get_snapshot_info(
    ec2_client,
    volume_id: str,
    user_id: str,
    max_retries: int = 5
) -> dict:
    """
    Get snapshot information for a volume with retry logic.

    Handles AWS API rate limiting with exponential backoff + jitter.

    Args:
        ec2_client: Boto3 EC2 client
        volume_id: EBS volume ID
        user_id: User ID (for logging)
        max_retries: Maximum retry attempts (default: 5)

    Returns:
        Dictionary with:
        - count: Total completed snapshots
        - is_backing_up: Whether a snapshot is in progress
        - last_snapshot_at: Timestamp of most recent completed snapshot
    """
    info = {
        "count": 0,
        "is_backing_up": False,
        "last_snapshot_at": None,
    }

    for attempt in range(max_retries):
        try:
            # Check for in-progress snapshots
            pending_response = ec2_client.describe_snapshots(
                OwnerIds=["self"],
                Filters=[
                    {"Name": "volume-id", "Values": [volume_id]},
                    {"Name": "status", "Values": ["pending"]},
                ]
            )

            info["is_backing_up"] = (
                len(pending_response.get("Snapshots", [])) > 0
            )

            # Get completed snapshots
            completed_response = ec2_client.describe_snapshots(
                OwnerIds=["self"],
                Filters=[
                    {"Name": "volume-id", "Values": [volume_id]},
                    {"Name": "status", "Values": ["completed"]},
                ]
            )

            snapshots = completed_response.get("Snapshots", [])
            info["count"] = len(snapshots)

            if snapshots:
                # Find most recent snapshot
                sorted_snapshots = sorted(
                    snapshots,
                    key=lambda s: s["StartTime"],
                    reverse=True
                )
                info["last_snapshot_at"] = (
                    sorted_snapshots[0]["StartTime"]
                )

            return info

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")

            # Check if it's a throttling error
            if error_code in [
                "RequestLimitExceeded",
                "Throttling",
                "TooManyRequestsException",
            ]:
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    base_wait = 2 ** attempt
                    jitter = random.uniform(0, 0.5 * base_wait)
                    wait_time = base_wait + jitter

                    logger.warning(
                        f"AWS API throttling on volume {volume_id} "
                        f"(attempt {attempt + 1}/{max_retries}), "
                        f"waiting {wait_time:.2f}s before retry"
                    )
                    time.sleep(wait_time)
                    continue
                else:
                    # Max retries exhausted
                    logger.error(
                        f"AWS API throttling on volume {volume_id}, "
                        f"max retries ({max_retries}) exhausted"
                    )
                    return info
            else:
                # Non-throttling error, log and return defaults
                logger.error(
                    f"AWS API error getting snapshots for volume "
                    f"{volume_id}: {error_code} - {e}",
                    exc_info=True
                )
                return info

        except Exception as e:
            # Unexpected error
            logger.error(
                f"Error getting snapshot info for volume "
                f"{volume_id}: {e}",
                exc_info=True
            )
            return info

    # Should not reach here, but return defaults as fallback
    return info


def get_all_disks_from_db() -> list[dict]:
    """
    Get all disk records from database (including deleted ones for
    reconciliation).
    """
    try:
        with get_db_cursor(readonly=True) as cur:
            cur.execute("""
                SELECT
                    disk_id, disk_name, user_id, ebs_volume_id, size_gb,
                    in_use, reservation_id, is_backing_up, is_deleted,
                    snapshot_count, last_snapshot_at, created_at,
                    operation_id, operation_status, last_used
                FROM disks
                ORDER BY created_at DESC
            """)

            results = cur.fetchall()
            return [dict(row) for row in results]

    except Exception as e:
        logger.error(
            f"Error fetching disks from database: {e}",
            exc_info=True
        )
        return []
