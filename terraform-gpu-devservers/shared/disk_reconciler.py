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
from datetime import UTC, datetime, timedelta

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
        "aws_duplicates": 0,
        "quarantined_volumes": 0,
        "skipped_duplicates": 0,
        "orphaned_db_active": 0,
        "orphaned_db_deleted": 0,
        "cleanup_quarantined_found": 0,
        "cleanup_deleted": 0,
        "cleanup_skipped_too_recent": 0,
        "skipped_concurrent_run": False,
    }
    
    # Acquire advisory lock to prevent concurrent reconciliation runs
    # Advisory lock key: 987654321 (arbitrary unique identifier for disk reconciliation)
    RECONCILIATION_LOCK_KEY = 987654321
    lock_acquired = False
    
    try:
        with get_db_cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s) AS locked", (RECONCILIATION_LOCK_KEY,))
            row = cur.fetchone()
            lock_acquired = row['locked'] if row else False
            
            if not lock_acquired:
                logger.warning(
                    "Another disk reconciliation is currently running. "
                    "Skipping this run to avoid conflicts and race conditions."
                )
                stats["skipped_concurrent_run"] = True
                return stats
        
        logger.info("Acquired reconciliation lock, proceeding...")

    except Exception as lock_error:
        logger.error(
            f"CRITICAL: Failed to acquire reconciliation lock: {lock_error}. "
            f"Aborting to prevent race conditions and data corruption.",
            exc_info=True
        )
        stats["errors"] += 1
        return stats  # Abort - do not proceed without lock

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

        # 3b. Detect duplicate volumes in AWS (multiple volumes with same user_id + disk_name)
        # This must be done BEFORE reconciliation to avoid cascading errors
        # When duplicates are found, use heuristics to determine current volume
        # and quarantine the others
        aws_by_user_disk = {}
        duplicate_groups = {}  # key -> list of volumes
        
        for vol in aws_volumes:
            key = (vol["user_id"], vol["disk_name"])
            if key in aws_by_user_disk:
                # Found duplicate - add to group
                if key not in duplicate_groups:
                    duplicate_groups[key] = [aws_by_user_disk[key]]
                duplicate_groups[key].append(vol)
            else:
                aws_by_user_disk[key] = vol
        
        # Process duplicate groups with quarantine logic
        stats["quarantined_volumes"] = 0
        if duplicate_groups:
            stats["aws_duplicates"] = len(duplicate_groups)
            logger.warning(
                f"Found {len(duplicate_groups)} duplicate disk names in AWS. "
                f"Resolving conflicts with quarantine logic."
            )
            
            for key, conflicting_volumes in duplicate_groups.items():
                user_id, disk_name = key
                db_record = db_by_user_disk.get(key)
                
                # Use heuristics to resolve conflict
                current_volume, quarantined_ids = resolve_volume_conflict_with_quarantine(
                    ec2_client, user_id, disk_name, conflicting_volumes, db_record
                )
                
                if current_volume:
                    # Quarantine succeeded, now update DB to point to current volume
                    # This must be atomic with quarantine to avoid inconsistent state
                    db_update_success = False
                    
                    try:
                        with get_db_transaction():
                            # If DB record exists, update it to point to current volume
                            if db_record:
                                db_update_success = update_disk(
                                    user_id,
                                    disk_name,
                                    {
                                        "ebs_volume_id": current_volume["volume_id"],
                                        "size_gb": current_volume["size_gb"],
                                        "in_use": current_volume["is_attached"],
                                    }
                                )
                            else:
                                # No DB record yet - will be created during normal reconciliation
                                db_update_success = True
                        
                        if not db_update_success:
                            raise Exception("DB update returned False")
                        
                        # Success - update index and stats
                        aws_by_user_disk[key] = current_volume
                        stats["quarantined_volumes"] += len(quarantined_ids)
                        logger.info(
                            f"Resolved conflict for disk '{disk_name}' (user {user_id}): "
                            f"current={current_volume['volume_id']}, "
                            f"quarantined={quarantined_ids}, DB updated"
                        )
                        
                    except Exception as db_error:
                        # DB update failed after quarantine succeeded
                        # CRITICAL: Rollback quarantine to maintain consistency
                        logger.error(
                            f"DB update failed after quarantine for disk '{disk_name}' "
                            f"(user {user_id}): {db_error}. "
                            f"Rolling back quarantine tags to maintain consistency.",
                            exc_info=True
                        )
                        
                        # Rollback quarantine tags
                        for qid in quarantined_ids:
                            try:
                                logger.info(
                                    f"Rolling back quarantine tag for {qid} "
                                    f"due to DB failure"
                                )
                                ec2_client.delete_tags(
                                    Resources=[qid],
                                    Tags=[
                                        {"Key": "gpu-dev-quarantined"},
                                        {"Key": "gpu-dev-quarantine-reason"}
                                    ]
                                )
                            except Exception as rollback_error:
                                logger.critical(
                                    f"CRITICAL: Failed to rollback quarantine for {qid} "
                                    f"after DB failure: {rollback_error}. "
                                    f"Manual cleanup required ASAP!",
                                    exc_info=True
                                )
                        
                        stats["errors"] += 1
                else:
                    # Failed to resolve (e.g., multiple attached, partial quarantine)
                    logger.error(
                        f"Failed to auto-resolve conflict for disk '{disk_name}' "
                        f"(user {user_id}). Manual intervention required."
                    )
                    stats["errors"] += 1
        else:
            stats["aws_duplicates"] = 0
            stats["quarantined_volumes"] = 0

        # 4. Reconcile AWS volumes into database
        # Each volume is reconciled in its own transaction for atomicity
        # Only process the "canonical" volume for each (user_id, disk_name)
        # Skip duplicates that were detected above
        for volume_id, aws_vol in aws_by_volume_id.items():
            try:
                # Skip this volume if it's a duplicate
                # (not the first one we saw for this user_id + disk_name)
                key = (aws_vol["user_id"], aws_vol["disk_name"])
                canonical_vol = aws_by_user_disk.get(key)
                if canonical_vol and canonical_vol["volume_id"] != volume_id:
                    # This is a duplicate, skip it
                    stats["skipped_duplicates"] = stats.get("skipped_duplicates", 0) + 1
                    continue

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
                                    # Note: This should be rare now that we handle
                                    # conflicts during duplicate detection. If this
                                    # happens, it means the old volume was quarantined
                                    # but is still showing up (tag filter issue?)
                                    # or this is a different edge case.
                                    logger.warning(
                                        f"DB record {disk_name} for user {user_id} "
                                        f"has volume_id {existing_vol_id} but AWS "
                                        f"volume {volume_id} has same disk_name. "
                                        f"Old volume should have been quarantined. "
                                        f"Updating DB to point to {volume_id}."
                                    )
                                    # Update to new volume (was determined as current)
                                    stats["volume_id_conflicts"] += 1
                                    # Fall through to update logic
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

        # 6. Cleanup old quarantined volumes (>30 days)
        logger.info("Starting cleanup of old quarantined volumes")
        cleanup_stats = cleanup_old_quarantined_volumes(ec2_client, max_age_days=30)
        
        # Add cleanup stats to overall stats
        stats["cleanup_quarantined_found"] = cleanup_stats["quarantined_found"]
        stats["cleanup_deleted"] = cleanup_stats["deleted"]
        stats["cleanup_skipped_too_recent"] = cleanup_stats["skipped_too_recent"]
        stats["errors"] += cleanup_stats["errors"]
        
        logger.info(f"Disk reconciliation complete: {stats}")
        return stats

    except Exception as e:
        logger.error(
            f"Error during disk reconciliation: {e}",
            exc_info=True
        )
        stats["errors"] += 1
        return stats
    
    finally:
        # Release advisory lock if we acquired it
        if lock_acquired:
            try:
                with get_db_cursor() as cur:
                    cur.execute("SELECT pg_advisory_unlock(%s) AS unlocked", (RECONCILIATION_LOCK_KEY,))
                    logger.info("Released reconciliation lock")
            except Exception as unlock_error:
                logger.error(
                    f"Failed to release reconciliation lock: {unlock_error}",
                    exc_info=True
                )


def _notify_user_quarantine(user_id: str, disk_name: str, volume_id: str, 
                            current_volume_id: str, quarantine_timestamp: str) -> None:
    """
    Notify user that their volume has been quarantined.
    
    This is a placeholder for notification implementation. In production, this should:
    - Send email to user
    - Post to Slack channel
    - Create a notification in the web UI
    
    Args:
        user_id: User's email or ID
        disk_name: Name of the quarantined disk
        volume_id: ID of the quarantined volume
        current_volume_id: ID of the volume that was chosen as current
        quarantine_timestamp: When the volume was quarantined (ISO8601)
    """
    try:
        # TODO: Implement actual notification mechanism (email, Slack, etc.)
        # For now, log prominently so it can be monitored
        logger.warning(
            f"USER NOTIFICATION: Volume quarantined for user {user_id}. "
            f"Disk: {disk_name}, Quarantined volume: {volume_id}, "
            f"Current volume: {current_volume_id}, "
            f"Quarantine time: {quarantine_timestamp}. "
            f"Volume will be deleted after 30 days if not recovered. "
            f"Recovery instructions: Remove 'gpu-dev-quarantined' tag from volume."
        )
        
        # TODO: Example email notification (implement with SES/SMTP):
        # send_email(
        #     to=user_id,
        #     subject=f"[GPU Dev] Volume quarantined: {disk_name}",
        #     body=f"""
        #     Your disk '{disk_name}' had duplicate volumes in AWS.
        #     
        #     Quarantined volume: {volume_id}
        #     Current volume (in use): {current_volume_id}
        #     Quarantine date: {quarantine_timestamp}
        #     
        #     The quarantined volume will be automatically deleted after 30 days.
        #     
        #     To recover the quarantined volume:
        #     1. aws ec2 delete-tags --resources {volume_id} --tags Key=gpu-dev-quarantined
        #     2. Contact support if you need help
        #     
        #     To use the quarantined volume instead of current:
        #     1. Stop all reservations using this disk
        #     2. Remove quarantine from desired volume
        #     3. Tag current volume as quarantined
        #     4. Wait for next reconciliation cycle
        #     """
        # )
        
        # TODO: Example Slack notification (implement with Slack webhook):
        # send_slack_notification(
        #     channel="#gpu-dev-alerts",
        #     message=f"Volume quarantined for {user_id}: {disk_name} ({volume_id})"
        # )
        
    except Exception as notify_error:
        # Don't fail the entire process if notification fails
        logger.error(
            f"Failed to send notification to {user_id} about quarantined "
            f"volume {volume_id}: {notify_error}",
            exc_info=True
        )


def _get_snapshot_count_fast(ec2_client, volume_id: str) -> int:
    """
    Quick check to get snapshot count for a volume.
    Used during conflict resolution to prioritize volumes with more snapshots.
    
    Returns 0 on any error to avoid blocking conflict resolution.
    """
    try:
        response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=[
                {"Name": "volume-id", "Values": [volume_id]},
                {"Name": "status", "Values": ["completed"]},
            ],
            MaxResults=100  # Limit to avoid slow queries
        )
        return len(response.get("Snapshots", []))
    except Exception as e:
        logger.debug(f"Could not get snapshot count for {volume_id}: {e}")
        return 0


def _choose_best_volume(ec2_client, volumes: list[dict], disk_name: str) -> dict:
    """
    Choose the best volume from a list of conflicting volumes using smart heuristics.
    
    Prioritizes in this order:
    1. Larger volumes (more likely to contain important data)
    2. Volumes with more snapshots (indicates active use/importance)
    3. Newer volumes (more recent activity)
    4. Volume ID (deterministic tie-breaker)
    
    Args:
        ec2_client: Boto3 EC2 client
        volumes: List of volume dictionaries
        disk_name: Name of disk (for logging)
    
    Returns:
        The volume dict that should be considered "current"
    """
    if not volumes:
        raise ValueError("Cannot choose from empty volume list")
    
    if len(volumes) == 1:
        return volumes[0]
    
    # Enrich volumes with snapshot counts for better decision making
    for vol in volumes:
        if "snapshot_count" not in vol:
            vol["snapshot_count"] = _get_snapshot_count_fast(
                ec2_client, vol["volume_id"]
            )
    
    # Sort by: size (desc), snapshot_count (desc), created_at (desc), volume_id (asc)
    # Use timezone-aware minimum datetime per TIMEZONE_STANDARD.md
    MIN_DATETIME_UTC = datetime.min.replace(tzinfo=UTC)
    
    best_volume = max(
        volumes,
        key=lambda v: (
            v.get("size_gb", 0),                    # Larger = more data
            v.get("snapshot_count", 0),             # More snapshots = more important
            v.get("created_at", MIN_DATETIME_UTC),  # Newer = more recent
            v.get("volume_id", "")                  # Deterministic tie-breaker
        )
    )
    
    logger.info(
        f"Chose volume {best_volume['volume_id']} for disk '{disk_name}' using heuristics: "
        f"size={best_volume.get('size_gb', 0)}GB, "
        f"snapshots={best_volume.get('snapshot_count', 0)}, "
        f"created={best_volume.get('created_at', 'unknown')}"
    )
    
    return best_volume


def resolve_volume_conflict_with_quarantine(
    ec2_client,
    user_id: str,
    disk_name: str,
    conflicting_volumes: list[dict],
    db_record: dict | None
) -> tuple[dict | None, list[str]]:
    """
    Resolve conflict when multiple AWS volumes have same (user_id, disk_name).
    
    Uses heuristics to determine the "current" volume and quarantines others.
    
    Heuristics (in order):
    1. If one is attached → that's current
    2. If multiple attached → FAIL (impossible state)
    3. If all detached → use most recently used (check last_used in DB)
    4. If no usage history → use newest by CreateTime
    
    Args:
        ec2_client: Boto3 EC2 client
        user_id: User ID
        disk_name: Disk name
        conflicting_volumes: List of AWS volume dicts with same (user_id, disk_name)
        db_record: Existing DB record (if any)
    
    Returns:
        Tuple of (current_volume, quarantined_volume_ids)
        - current_volume: The volume dict that should be kept active (or None if failed)
        - quarantined_volume_ids: List of volume IDs that were quarantined
    """
    if not conflicting_volumes:
        return None, []
    
    logger.info(
        f"Resolving conflict for disk '{disk_name}' (user {user_id}): "
        f"{len(conflicting_volumes)} volumes found"
    )
    
    # Heuristic 1 & 2: Check attachment status
    attached_volumes = [v for v in conflicting_volumes if v["is_attached"]]
    
    if len(attached_volumes) > 1:
        # Multiple attached - impossible state, FAIL
        attached_ids = [v["volume_id"] for v in attached_volumes]
        logger.error(
            f"IMPOSSIBLE STATE: Multiple volumes attached for disk '{disk_name}' "
            f"(user {user_id}): {attached_ids}. Manual intervention required."
        )
        return None, []
    
    if len(attached_volumes) == 1:
        # One attached - that's definitely the current one
        current_volume = attached_volumes[0]
        logger.info(
            f"Using attached volume {current_volume['volume_id']} as current "
            f"for disk '{disk_name}'"
        )
    else:
        # Heuristic 3 & 4: All detached, use DB preference or smart heuristics
        if db_record and db_record.get("ebs_volume_id"):
            # DB points to a specific volume - prefer that
            db_vol_id = db_record["ebs_volume_id"]
            db_volume = next(
                (v for v in conflicting_volumes if v["volume_id"] == db_vol_id),
                None
            )
            if db_volume:
                logger.info(
                    f"Using DB-referenced volume {db_vol_id} as current "
                    f"for disk '{disk_name}'"
                )
                current_volume = db_volume
            else:
                # DB points to a volume not in conflict set - use smart heuristics
                logger.warning(
                    f"DB references {db_vol_id} but not in conflict set. "
                    f"Using smart heuristics (size, snapshots, age)."
                )
                current_volume = _choose_best_volume(ec2_client, conflicting_volumes, disk_name)
        else:
            # No DB record or no volume_id - use smart heuristics
            # Prefer: larger volumes (more likely to have data) >
            #         more snapshots (more important) >
            #         newer volumes (more recent activity) >
            #         volume_id (deterministic tie-breaking)
            current_volume = _choose_best_volume(ec2_client, conflicting_volumes, disk_name)
            logger.info(
                f"No attachment or DB hint, using smart heuristics: "
                f"volume {current_volume['volume_id']} "
                f"(size={current_volume['size_gb']}GB, "
                f"created={current_volume['created_at']}) "
                f"as current for disk '{disk_name}'"
            )
    
    # Quarantine all other volumes
    quarantined_ids = []
    # Use ISO8601 format with Z suffix for consistency
    quarantine_timestamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    expected_quarantine_count = len(conflicting_volumes) - 1
    
    for volume in conflicting_volumes:
        if volume["volume_id"] == current_volume["volume_id"]:
            continue
        
        vol_id = volume["volume_id"]
        
        # SAFETY CHECK: Re-verify volume is not attached before quarantining
        # This prevents race condition where volume becomes attached
        # between initial check and quarantine action
        try:
            vol_detail = ec2_client.describe_volumes(VolumeIds=[vol_id])
            current_attachments = vol_detail['Volumes'][0].get('Attachments', [])
            attached_now = any(
                att.get('State') == 'attached' 
                for att in current_attachments
            )
            
            if attached_now:
                logger.error(
                    f"RACE CONDITION: Volume {vol_id} is now attached! "
                    f"Skipping quarantine to avoid breaking active reservation. "
                    f"Manual intervention required for disk '{disk_name}' (user {user_id})."
                )
                continue  # Skip this volume, don't quarantine
        except Exception as verify_error:
            logger.error(
                f"Failed to verify attachment status for {vol_id}: {verify_error}. "
                f"Skipping quarantine as safety precaution.",
                exc_info=True
            )
            continue  # Skip on verification failure
        
        # Attempt to quarantine with retry logic for transient errors
        max_retries = 3
        quarantined = False
        
        for retry_attempt in range(max_retries):
            try:
                # Tag volume as quarantined in AWS
                ec2_client.create_tags(
                    Resources=[vol_id],
                    Tags=[
                        {
                            "Key": "gpu-dev-quarantined",
                            "Value": quarantine_timestamp
                        },
                        {
                            "Key": "gpu-dev-quarantine-reason",
                            "Value": f"Duplicate disk_name: {disk_name} for user {user_id}. Current volume: {current_volume['volume_id']}"
                        }
                    ]
                )
                quarantined_ids.append(vol_id)
                quarantined = True
                logger.warning(
                    f"QUARANTINED volume {vol_id} for disk '{disk_name}' (user {user_id}). "
                    f"Will be deleted after 30 days if not manually recovered."
                )
                
                # Notify user about quarantine
                _notify_user_quarantine(
                    user_id=user_id,
                    disk_name=disk_name,
                    volume_id=vol_id,
                    current_volume_id=current_volume["volume_id"],
                    quarantine_timestamp=quarantine_timestamp
                )
                
                break  # Success, exit retry loop
                
            except ClientError as tag_error:
                error_code = tag_error.response.get("Error", {}).get("Code", "")
                
                # Retry on throttling errors
                if error_code in ["RequestLimitExceeded", "Throttling", "TooManyRequestsException"]:
                    if retry_attempt < max_retries - 1:
                        wait_time = 2 ** retry_attempt + random.uniform(0, 1)
                        logger.warning(
                            f"Tagging throttled for {vol_id}, "
                            f"retry {retry_attempt + 1}/{max_retries} "
                            f"after {wait_time:.2f}s"
                        )
                        time.sleep(wait_time)
                        continue
                
                # Non-retryable error or max retries exhausted
                logger.error(
                    f"Failed to quarantine volume {vol_id} after {retry_attempt + 1} "
                    f"attempts: {error_code} - {tag_error}",
                    exc_info=True
                )
                break  # Give up on this volume
                
            except Exception as tag_error:
                logger.error(
                    f"Failed to quarantine volume {vol_id}: {tag_error}. "
                    f"Manual intervention required.",
                    exc_info=True
                )
                break  # Give up on this volume
    
    # CRITICAL: Check if all expected volumes were quarantined
    # If any failed, we cannot safely proceed with DB update
    if len(quarantined_ids) < expected_quarantine_count:
        logger.error(
            f"PARTIAL QUARANTINE FAILURE for disk '{disk_name}' (user {user_id}): "
            f"Expected to quarantine {expected_quarantine_count} volumes, "
            f"but only {len(quarantined_ids)} succeeded. "
            f"NOT returning current volume to prevent DB update with unresolved conflict. "
            f"Manual intervention required."
        )
        
        # Attempt to rollback successful quarantines to maintain consistency
        for qid in quarantined_ids:
            try:
                logger.info(f"Rolling back quarantine for {qid}")
                ec2_client.delete_tags(
                    Resources=[qid],
                    Tags=[
                        {"Key": "gpu-dev-quarantined"},
                        {"Key": "gpu-dev-quarantine-reason"}
                    ]
                )
            except Exception as rollback_error:
                logger.error(
                    f"Failed to rollback quarantine for {qid}: {rollback_error}. "
                    f"Manual cleanup required.",
                    exc_info=True
                )
        
        return None, []  # Return None to indicate resolution failure
    
    return current_volume, quarantined_ids


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

        # Skip quarantined volumes - they are being phased out
        if tags.get("gpu-dev-quarantined"):
            logger.debug(
                f"Skipping quarantined volume {aws_volume['VolumeId']} "
                f"(quarantined at {tags.get('gpu-dev-quarantined')})"
            )
            return None
        
        # Skip volumes in transient states (creating, deleting, error)
        # Only process stable volumes (available, in-use)
        volume_state = aws_volume.get("State", "")
        if volume_state not in ["available", "in-use"]:
            logger.debug(
                f"Skipping volume {aws_volume['VolumeId']} "
                f"in transient state: {volume_state}"
            )
            return None

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


def cleanup_old_quarantined_volumes(
    ec2_client,
    max_age_days: int = 30
) -> dict[str, int]:
    """
    Delete quarantined volumes that are older than max_age_days.
    
    Quarantined volumes are tagged with 'gpu-dev-quarantined' and a timestamp.
    This function finds all such volumes, checks if they're old enough, and
    deletes them to free up storage costs.
    
    Args:
        ec2_client: Boto3 EC2 client
        max_age_days: Maximum age in days before deletion (default: 30)
    
    Returns:
        Dictionary with cleanup statistics
    """
    stats = {
        "quarantined_found": 0,
        "deleted": 0,
        "errors": 0,
        "skipped_too_recent": 0,
    }
    
    try:
        logger.info(
            f"Starting cleanup of quarantined volumes older than {max_age_days} days"
        )
        
        # Find all volumes with quarantine tag
        # Use pagination to handle >500 quarantined volumes
        try:
            paginator = ec2_client.get_paginator('describe_volumes')
            page_iterator = paginator.paginate(
                Filters=[
                    {"Name": "tag-key", "Values": ["gpu-dev-quarantined"]}
                ]
            )
            
            quarantined_volumes = []
            for page in page_iterator:
                quarantined_volumes.extend(page.get("Volumes", []))
                
        except Exception as describe_error:
            logger.error(
                f"Failed to describe quarantined volumes: {describe_error}",
                exc_info=True
            )
            stats["errors"] += 1
            return stats
        stats["quarantined_found"] = len(quarantined_volumes)
        
        if not quarantined_volumes:
            logger.info("No quarantined volumes found")
            return stats
        
        logger.info(f"Found {len(quarantined_volumes)} quarantined volumes")
        
        # Calculate cutoff time
        cutoff_time = datetime.now(UTC) - timedelta(days=max_age_days)
        
        for volume in quarantined_volumes:
            volume_id = volume["VolumeId"]
            
            # Extract quarantine timestamp from tags
            tags = {tag["Key"]: tag["Value"] for tag in volume.get("Tags", [])}
            quarantine_timestamp_str = tags.get("gpu-dev-quarantined")
            
            if not quarantine_timestamp_str:
                logger.warning(
                    f"Volume {volume_id} has quarantine tag but no timestamp. Skipping."
                )
                stats["errors"] += 1
                continue
            
            try:
                # Parse ISO timestamp
                quarantine_time = datetime.fromisoformat(
                    quarantine_timestamp_str.replace("Z", "+00:00")
                )
                quarantine_time = ensure_utc(quarantine_time)
            except Exception as parse_error:
                logger.error(
                    f"Failed to parse quarantine timestamp for {volume_id}: "
                    f"{quarantine_timestamp_str}. Error: {parse_error}"
                )
                stats["errors"] += 1
                continue
            
            # Check if old enough to delete
            age_days = (datetime.now(UTC) - quarantine_time).days
            if quarantine_time > cutoff_time:
                logger.debug(
                    f"Volume {volume_id} quarantined {age_days} days ago, "
                    f"not old enough to delete (need {max_age_days} days)"
                )
                
                # Send reminder notifications at key intervals
                disk_name = tags.get("disk-name") or tags.get("disk_name") or "unknown"
                user_id = tags.get("gpu-dev-user") or "unknown"
                days_until_deletion = max_age_days - age_days
                
                # Warn users at 7, 3, and 1 day before deletion
                if days_until_deletion in [7, 3, 1]:
                    logger.warning(
                        f"DELETION REMINDER: Volume {volume_id} (disk: {disk_name}, "
                        f"user: {user_id}) will be deleted in {days_until_deletion} day(s). "
                        f"Quarantined {age_days} days ago. "
                        f"Remove 'gpu-dev-quarantined' tag to recover."
                    )
                    # TODO: Send actual notification to user
                    # _notify_user_deletion_reminder(user_id, disk_name, volume_id, days_until_deletion)
                
                stats["skipped_too_recent"] += 1
                continue
            
            # Check if volume is attached (safety check)
            if volume.get("Attachments"):
                logger.error(
                    f"SAFETY: Quarantined volume {volume_id} is attached! "
                    f"This should never happen. Skipping deletion."
                )
                stats["errors"] += 1
                continue
            
            # Delete the volume (with safety snapshot first)
            age_days = (datetime.now(UTC) - quarantine_time).days
            disk_name = tags.get("disk-name") or tags.get("disk_name") or "unknown"
            user_id = tags.get("gpu-dev-user") or "unknown"
            size_gb = volume.get("Size", 0)
            
            try:
                # CRITICAL SAFETY: Create snapshot before deletion
                # This allows recovery if wrong volume was quarantined
                logger.info(
                    f"Creating safety snapshot for quarantined volume {volume_id} "
                    f"(disk: {disk_name}, user: {user_id}, size: {size_gb}GB, "
                    f"quarantined {age_days} days ago)"
                )
                
                snapshot_response = ec2_client.create_snapshot(
                    VolumeId=volume_id,
                    Description=f"Pre-deletion safety snapshot of quarantined disk '{disk_name}' for user {user_id}",
                    TagSpecifications=[{
                        'ResourceType': 'snapshot',
                        'Tags': [
                            {'Key': 'gpu-dev-quarantine-backup', 'Value': 'true'},
                            {'Key': 'original-volume-id', 'Value': volume_id},
                            {'Key': 'disk-name', 'Value': disk_name},
                            {'Key': 'gpu-dev-user', 'Value': user_id},
                            {'Key': 'quarantine-deletion-date', 'Value': datetime.now(UTC).isoformat()},
                            {'Key': 'retention-days', 'Value': '90'},  # Keep snapshot for 90 days
                            {'Key': 'quarantine-timestamp', 'Value': quarantine_timestamp_str}
                        ]
                    }]
                )
                
                snapshot_id = snapshot_response['SnapshotId']
                logger.info(
                    f"Created safety snapshot {snapshot_id} for volume {volume_id}. "
                    f"Proceeding with deletion."
                )
                
                # Now safe to delete the volume
                ec2_client.delete_volume(VolumeId=volume_id)
                stats["deleted"] += 1
                
                logger.info(
                    f"Successfully deleted quarantined volume {volume_id}. "
                    f"Safety snapshot {snapshot_id} retained for 90 days."
                )
            except ClientError as delete_error:
                error_code = delete_error.response.get("Error", {}).get("Code", "")
                if error_code == "InvalidVolume.NotFound":
                    logger.info(
                        f"Volume {volume_id} already deleted (not found)"
                    )
                    stats["deleted"] += 1
                elif error_code == "InvalidSnapshot.InProgress":
                    logger.warning(
                        f"Snapshot creation in progress for {volume_id}, "
                        f"will retry deletion on next run"
                    )
                    stats["skipped_too_recent"] += 1
                else:
                    logger.error(
                        f"Failed to snapshot or delete quarantined volume {volume_id}: "
                        f"{error_code} - {delete_error}",
                        exc_info=True
                    )
                    stats["errors"] += 1
            except Exception as delete_error:
                logger.error(
                    f"Failed to snapshot or delete quarantined volume {volume_id}: {delete_error}",
                    exc_info=True
                )
                stats["errors"] += 1
        
        logger.info(
            f"Quarantine cleanup complete: {stats['deleted']} deleted, "
            f"{stats['skipped_too_recent']} too recent, "
            f"{stats['errors']} errors"
        )
        return stats
    
    except Exception as e:
        logger.error(
            f"Error during quarantine cleanup: {e}",
            exc_info=True
        )
        stats["errors"] += 1
        return stats
