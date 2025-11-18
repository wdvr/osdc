"""
Disk management for GPU Dev CLI
Handles named persistent disks with snapshot-first workflow
"""

import boto3
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from .config import Config


def get_ec2_client(config: Config):
    """Get boto3 EC2 client"""
    return config.session.client('ec2', region_name=config.aws_region)


def get_s3_client(config: Config):
    """Get boto3 S3 client"""
    return config.session.client('s3', region_name=config.aws_region)


def get_dynamodb_resource(config: Config):
    """Get boto3 DynamoDB resource"""
    return config.session.resource('dynamodb', region_name=config.aws_region)


def get_disk_in_use_status(disk_name: str, user_id: str, config: Config) -> Tuple[bool, Optional[str]]:
    """
    Check if a disk is currently in use by any reservation.
    Returns (is_in_use, reservation_id)

    In snapshot-first system, we check for active reservations with disk_name field,
    not for in-use volumes (volumes are ephemeral, created from snapshots on demand).
    """
    dynamodb = get_dynamodb_resource(config)

    try:
        reservations_table = dynamodb.Table(config.reservations_table)

        # Use UserIndex for efficient query (instead of scan with pagination)
        response = reservations_table.query(
            IndexName="UserIndex",
            KeyConditionExpression="user_id = :user_id",
            FilterExpression="disk_name = :disk_name AND #status IN (:active, :preparing)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":user_id": user_id,
                ":disk_name": disk_name,
                ":active": "active",
                ":preparing": "preparing"
            }
        )

        # Handle pagination
        items = response.get("Items", [])
        while "LastEvaluatedKey" in response:
            response = reservations_table.query(
                IndexName="UserIndex",
                KeyConditionExpression="user_id = :user_id",
                FilterExpression="disk_name = :disk_name AND #status IN (:active, :preparing)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":user_id": user_id,
                    ":disk_name": disk_name,
                    ":active": "active",
                    ":preparing": "preparing"
                },
                ExclusiveStartKey=response["LastEvaluatedKey"]
            )
            items.extend(response.get("Items", []))

        if items:
            reservation_id = items[0]["reservation_id"]
            return True, reservation_id

        # Special case: For "default" disk, also check for legacy reservations without disk_name field
        # (reservations created before named disk migration)
        # IMPORTANT: Only match legacy reservations that HAVE an ebs_volume_id
        # (reservations without disk_name AND without ebs_volume_id are non-persistent, not "default" disk)
        if disk_name == "default":
            legacy_response = reservations_table.query(
                IndexName="UserIndex",
                KeyConditionExpression="user_id = :user_id",
                FilterExpression="attribute_not_exists(disk_name) AND attribute_exists(ebs_volume_id) AND #status IN (:active, :preparing)",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":user_id": user_id,
                    ":active": "active",
                    ":preparing": "preparing"
                }
            )

            # Handle pagination for legacy query
            legacy_items = legacy_response.get("Items", [])
            while "LastEvaluatedKey" in legacy_response:
                legacy_response = reservations_table.query(
                    IndexName="UserIndex",
                    KeyConditionExpression="user_id = :user_id",
                    FilterExpression="attribute_not_exists(disk_name) AND attribute_exists(ebs_volume_id) AND #status IN (:active, :preparing)",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":user_id": user_id,
                        ":active": "active",
                        ":preparing": "preparing"
                    },
                    ExclusiveStartKey=legacy_response["LastEvaluatedKey"]
                )
                legacy_items.extend(legacy_response.get("Items", []))

            if legacy_items:
                reservation_id = legacy_items[0]["reservation_id"]
                return True, reservation_id

        return False, None

    except Exception as e:
        print(f"Warning: Could not query reservations: {e}")
        return False, None


def list_disks(user_id: str, config: Config) -> List[Dict]:
    """
    List all disks for a user.
    Returns list of disk info dicts with: name, size, last_used, created_at, snapshot_count, in_use, reservation_id
    """
    ec2_client = get_ec2_client(config)
    dynamodb = get_dynamodb_resource(config)

    # Get all snapshots for this user (both completed and pending)
    completed_response = ec2_client.describe_snapshots(
        OwnerIds=["self"],
        Filters=[
            {"Name": "tag:gpu-dev-user", "Values": [user_id]},
            {"Name": "status", "Values": ["completed"]},
        ]
    )

    pending_response = ec2_client.describe_snapshots(
        OwnerIds=["self"],
        Filters=[
            {"Name": "tag:gpu-dev-user", "Values": [user_id]},
            {"Name": "status", "Values": ["pending"]},
        ]
    )

    completed_snapshots = completed_response.get('Snapshots', [])
    pending_snapshots = pending_response.get('Snapshots', [])
    all_snapshots = completed_snapshots + pending_snapshots

    # ALSO check for any in-use volumes (to detect legacy volumes without disk_name)
    # This helps show "default" disk as in-use even if volume has no disk_name tag
    legacy_in_use_volumes = []
    try:
        vol_response = ec2_client.describe_volumes(
            Filters=[
                {"Name": "tag:gpu-dev-user", "Values": [user_id]},
                {"Name": "tag:ManagedBy", "Values": ["gpu-dev-cli"]},
                {"Name": "status", "Values": ["in-use"]},
            ]
        )
        # Find volumes without disk_name tag (pre-migration)
        for vol in vol_response.get("Volumes", []):
            tags = {tag['Key']: tag['Value'] for tag in vol.get('Tags', [])}
            if 'disk_name' not in tags:
                legacy_in_use_volumes.append(vol)
    except Exception as e:
        print(f"Warning: Could not check for legacy volumes: {e}")

    # Track which disks have pending snapshots
    disks_with_pending_snapshots = set()
    for snapshot in pending_snapshots:
        tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}
        if 'delete-date' not in tags:  # Exclude soft-deleted
            disk_name = tags.get('disk_name', 'default')
            disks_with_pending_snapshots.add(disk_name)

    # Group snapshots by disk_name (including soft-deleted snapshots)
    disks_map = {}
    for snapshot in all_snapshots:
        tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}
        disk_name = tags.get('disk_name', 'default')

        if disk_name not in disks_map:
            disks_map[disk_name] = {
                'name': disk_name,
                'snapshots': [],
                'pending_snapshots': [],
                'size_gb': None,
                'created_at': None,
                'last_used': None,
                'is_deleted': False,
                'delete_date': None,
            }

        # Track if this disk is soft-deleted (check if ANY snapshot has delete-date)
        if 'delete-date' in tags:
            disks_map[disk_name]['is_deleted'] = True
            disks_map[disk_name]['delete_date'] = tags.get('delete-date')

        # Separate completed and pending snapshots
        if snapshot['State'] == 'pending':
            disks_map[disk_name]['pending_snapshots'].append(snapshot)
        else:
            disks_map[disk_name]['snapshots'].append(snapshot)

    # Process each disk
    disks = []
    for disk_name, disk_data in disks_map.items():
        snapshots_list = disk_data['snapshots']
        pending_snapshots_list = disk_data['pending_snapshots']

        # Check if disk has any pending snapshots (backing up)
        is_backing_up = len(pending_snapshots_list) > 0

        # Get latest snapshot for metadata (prefer completed, fallback to pending)
        all_disk_snapshots = snapshots_list + pending_snapshots_list
        if all_disk_snapshots:
            latest_snapshot = max(all_disk_snapshots, key=lambda s: s['StartTime'])
            size_gb = latest_snapshot.get('VolumeSize', 0)

            # Extract disk_size tag from latest snapshot (e.g., "1.2G")
            latest_tags = {tag['Key']: tag['Value'] for tag in latest_snapshot.get('Tags', [])}
            disk_size = latest_tags.get('disk_size', None)

            # Get created_at from oldest snapshot (completed or pending)
            oldest_snapshot = min(all_disk_snapshots, key=lambda s: s['StartTime'])
            created_at = oldest_snapshot['StartTime']

            # Get last_used from latest completed snapshot (or pending if no completed)
            if snapshots_list:
                last_used = max(snapshots_list, key=lambda s: s['StartTime'])['StartTime']
            elif pending_snapshots_list:
                # New disk, first snapshot still pending
                last_used = max(pending_snapshots_list, key=lambda s: s['StartTime'])['StartTime']
            else:
                last_used = None
        else:
            size_gb = 0
            disk_size = None
            created_at = None
            last_used = None

        # Check if disk is in use
        is_in_use, reservation_id = get_disk_in_use_status(disk_name, user_id, config)

        # Special case: If this is "default" disk and we found legacy in-use volumes,
        # mark as in-use (helps detect pre-migration volumes)
        if not is_in_use and disk_name == "default" and legacy_in_use_volumes:
            # Find the reservation using this legacy volume
            legacy_vol_id = legacy_in_use_volumes[0]["VolumeId"]
            try:
                reservations_table = dynamodb.Table(config.reservations_table)
                response = reservations_table.scan(
                    FilterExpression="ebs_volume_id = :vol_id AND #status IN (:active, :preparing)",
                    ExpressionAttributeNames={"#status": "status"},
                    ExpressionAttributeValues={
                        ":vol_id": legacy_vol_id,
                        ":active": "active",
                        ":preparing": "preparing"
                    }
                )
                if response.get("Items"):
                    is_in_use = True
                    reservation_id = response["Items"][0]["reservation_id"]
            except Exception:
                # If query fails, still mark as in-use to be safe
                is_in_use = True
                reservation_id = None

        disks.append({
            'name': disk_name,
            'size_gb': size_gb,
            'disk_size': disk_size,
            'created_at': created_at,
            'last_used': last_used,
            'snapshot_count': len(snapshots_list),
            'pending_snapshot_count': len(pending_snapshots_list),
            'in_use': is_in_use,
            'is_backing_up': is_backing_up,
            'reservation_id': reservation_id,
            'is_deleted': disk_data['is_deleted'],
            'delete_date': disk_data['delete_date'],
        })

    # Sort by last_used (most recent first)
    disks.sort(key=lambda d: d['last_used'] or datetime.min, reverse=True)

    return disks


def create_disk(disk_name: str, user_id: str, config: Config) -> bool:
    """
    Create a new named disk by creating an initial empty snapshot.
    Returns True on success, False on failure.
    """
    ec2_client = get_ec2_client(config)

    # Check if disk already exists
    existing_disks = list_disks(user_id, config)
    if any(d['name'] == disk_name for d in existing_disks):
        print(f"Error: Disk '{disk_name}' already exists")
        return False

    # Validate disk name (alphanumeric + hyphens + underscores)
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', disk_name):
        print(f"Error: Disk name must contain only letters, numbers, hyphens, and underscores")
        return False

    print(f"Creating new disk '{disk_name}'...")
    print(f"(Creating initial snapshot - volumes are created from snapshots on first use)")

    try:
        # Create a bootstrap volume for snapshot (AWS requires a volume to create a snapshot)
        # We need to get an availability zone from the config
        azs_response = ec2_client.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )
        az = azs_response['AvailabilityZones'][0]['ZoneName']

        create_vol_response = ec2_client.create_volume(
            AvailabilityZone=az,
            Size=1,  # 1GB bootstrap volume (just for creating snapshot)
            VolumeType="gp3",
            TagSpecifications=[{
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "gpu-dev-user", "Value": user_id},
                    {"Key": "disk_name", "Value": disk_name},
                    {"Key": "Name", "Value": f"gpu-dev-disk-{user_id.split('@')[0]}-{disk_name}-bootstrap"},
                ],
            }]
        )

        volume_id = create_vol_response["VolumeId"]
        print(f"Creating bootstrap volume {volume_id}...")

        # Wait for volume to be available
        waiter = ec2_client.get_waiter("volume_available")
        waiter.wait(VolumeIds=[volume_id], WaiterConfig={"Delay": 2, "MaxAttempts": 30})

        # Create initial snapshot
        print(f"Creating snapshot...")
        snapshot_response = ec2_client.create_snapshot(
            VolumeId=volume_id,
            Description=f"Initial snapshot for gpu-dev disk '{disk_name}'",
            TagSpecifications=[{
                "ResourceType": "snapshot",
                "Tags": [
                    {"Key": "gpu-dev-user", "Value": user_id},
                    {"Key": "disk_name", "Value": disk_name},
                    {"Key": "Name", "Value": f"gpu-dev-disk-{user_id.split('@')[0]}-{disk_name}-initial"},
                    {"Key": "SnapshotType", "Value": "initial"},
                    {"Key": "created_at", "Value": str(int(datetime.now().timestamp()))},
                ],
            }]
        )

        snapshot_id = snapshot_response["SnapshotId"]
        print(f"Snapshot {snapshot_id} created, waiting for completion...")

        # Wait for snapshot to complete
        waiter = ec2_client.get_waiter("snapshot_completed")
        waiter.wait(SnapshotIds=[snapshot_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})

        # Delete bootstrap volume (no longer needed)
        ec2_client.delete_volume(VolumeId=volume_id)

        print(f"✓ Successfully created disk '{disk_name}'")
        print(f"   On first use, a 1TB volume will be created from this snapshot")
        return True

    except Exception as e:
        print(f"Error creating disk: {e}")
        # Try to clean up volume if it was created
        try:
            ec2_client.delete_volume(VolumeId=volume_id)
        except:
            pass
        return False


def list_disk_content(disk_name: str, user_id: str, config: Config) -> Optional[str]:
    """
    Fetch and return the contents of the latest snapshot for a disk.
    Returns contents string or None if not found.
    """
    ec2_client = get_ec2_client(config)
    s3_client = get_s3_client(config)

    # Find latest snapshot for this disk
    response = ec2_client.describe_snapshots(
        OwnerIds=["self"],
        Filters=[
            {"Name": "tag:gpu-dev-user", "Values": [user_id]},
            {"Name": "tag:disk_name", "Values": [disk_name]},
            {"Name": "status", "Values": ["completed"]},
        ]
    )

    snapshots = response.get('Snapshots', [])
    if not snapshots:
        print(f"No snapshots found for disk '{disk_name}'")
        return None

    # Get latest snapshot
    latest_snapshot = max(snapshots, key=lambda s: s['StartTime'])
    snapshot_id = latest_snapshot['SnapshotId']

    # Get S3 path from snapshot tags
    tags = {tag['Key']: tag['Value'] for tag in latest_snapshot.get('Tags', [])}
    s3_path = tags.get('snapshot_content_s3')

    if not s3_path:
        print(f"Snapshot {snapshot_id} does not have content metadata")
        print(f"This may be an older snapshot created before content tracking was added.")
        return None

    # Parse S3 path (s3://bucket/key)
    if not s3_path.startswith('s3://'):
        print(f"Invalid S3 path format: {s3_path}")
        return None

    path_parts = s3_path[5:].split('/', 1)
    if len(path_parts) != 2:
        print(f"Invalid S3 path format: {s3_path}")
        return None

    bucket_name, s3_key = path_parts

    try:
        # Fetch contents from S3
        response = s3_client.get_object(Bucket=bucket_name, Key=s3_key)
        contents = response['Body'].read().decode('utf-8')
        return contents
    except s3_client.exceptions.NoSuchKey:
        print(f"Contents file not found in S3: {s3_path}")
        return None
    except Exception as e:
        print(f"Error fetching contents from S3: {e}")
        return None


def delete_disk(disk_name: str, user_id: str, config: Config) -> bool:
    """
    Soft delete a disk by tagging snapshots for deletion in 30 days.
    Returns True on success, False on failure.
    """
    from datetime import datetime, timedelta
    ec2_client = get_ec2_client(config)

    # Check if disk exists
    disks = list_disks(user_id, config)
    disk = next((d for d in disks if d['name'] == disk_name), None)

    if not disk:
        print(f"Error: Disk '{disk_name}' not found")
        return False

    # Check if disk is in use
    if disk['in_use']:
        print(f"Error: Cannot delete disk '{disk_name}' - it is currently in use")
        print(f"Reservation ID: {disk['reservation_id']}")
        return False

    print(f"Marking disk '{disk_name}' for deletion...")
    print(f"Snapshots will be deleted in 30 days ({disk['snapshot_count']} snapshot(s))")

    # Calculate deletion date (30 days from now)
    delete_date = datetime.now() + timedelta(days=30)
    delete_date_str = delete_date.strftime('%Y-%m-%d')

    # Find all snapshots for this disk
    try:
        response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=[
                {"Name": "tag:gpu-dev-user", "Values": [user_id]},
                {"Name": "tag:disk_name", "Values": [disk_name]},
            ]
        )

        snapshots = response.get('Snapshots', [])

        if not snapshots:
            print(f"Warning: No snapshots found for disk '{disk_name}'")
            return True

        # Tag each snapshot with delete-date
        tagged_count = 0
        for snapshot in snapshots:
            snapshot_id = snapshot['SnapshotId']
            try:
                ec2_client.create_tags(
                    Resources=[snapshot_id],
                    Tags=[
                        {"Key": "delete-date", "Value": delete_date_str},
                        {"Key": "marked-deleted-at", "Value": str(int(datetime.now().timestamp()))},
                    ]
                )
                print(f"  ✓ Marked snapshot {snapshot_id} for deletion on {delete_date_str}")
                tagged_count += 1
            except Exception as e:
                print(f"  ✗ Error tagging snapshot {snapshot_id}: {e}")

        print(f"✓ Successfully marked disk '{disk_name}' for deletion ({tagged_count} snapshots)")
        print(f"   Snapshots will be permanently deleted on {delete_date_str}")
        return True

    except Exception as e:
        print(f"Error marking disk for deletion: {e}")
        return False


def rename_disk(old_name: str, new_name: str, user_id: str, config: Config) -> bool:
    """
    Rename a disk by updating disk_name tags on all its snapshots.
    Returns True on success, False on failure.
    """
    ec2_client = get_ec2_client(config)

    # Validate new disk name
    import re
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name):
        print(f"Error: Disk name must contain only letters, numbers, hyphens, and underscores")
        return False

    # Check if old disk exists
    disks = list_disks(user_id, config)
    old_disk = next((d for d in disks if d['name'] == old_name), None)

    if not old_disk:
        print(f"Error: Disk '{old_name}' not found")
        return False

    # Check if new name already exists
    if any(d['name'] == new_name for d in disks):
        print(f"Error: Disk '{new_name}' already exists")
        return False

    # Check if disk is in use
    if old_disk['in_use']:
        print(f"Error: Cannot rename disk '{old_name}' - it is currently in use")
        print(f"Reservation ID: {old_disk['reservation_id']}")
        return False

    print(f"Renaming disk '{old_name}' to '{new_name}'...")

    try:
        # Find all snapshots for this disk
        response = ec2_client.describe_snapshots(
            OwnerIds=["self"],
            Filters=[
                {"Name": "tag:gpu-dev-user", "Values": [user_id]},
                {"Name": "tag:disk_name", "Values": [old_name]},
            ]
        )

        snapshots = response.get('Snapshots', [])

        if not snapshots:
            print(f"Warning: No snapshots found for disk '{old_name}'")
            return False

        # Update disk_name tag on each snapshot
        renamed_count = 0
        for snapshot in snapshots:
            snapshot_id = snapshot['SnapshotId']
            try:
                ec2_client.create_tags(
                    Resources=[snapshot_id],
                    Tags=[{"Key": "disk_name", "Value": new_name}]
                )
                print(f"  ✓ Updated snapshot {snapshot_id}")
                renamed_count += 1
            except Exception as e:
                print(f"  ✗ Error updating snapshot {snapshot_id}: {e}")

        print(f"✓ Successfully renamed disk to '{new_name}' ({renamed_count} snapshots updated)")
        return True

    except Exception as e:
        print(f"Error renaming disk: {e}")
        return False
