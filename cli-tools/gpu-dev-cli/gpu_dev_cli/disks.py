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
    """
    ec2_client = get_ec2_client(config)
    dynamodb = get_dynamodb_resource(config)

    # Step 1: Check for volumes with matching disk_name
    filters = [
        {"Name": "tag:gpu-dev-user", "Values": [user_id]},
        {"Name": "tag:disk_name", "Values": [disk_name]},
        {"Name": "status", "Values": ["in-use", "available"]},
    ]

    response = ec2_client.describe_volumes(Filters=filters)
    in_use_volumes = [v for v in response.get("Volumes", []) if v["State"] == "in-use"]

    if not in_use_volumes:
        return False, None

    # Step 2: Find reservation using this volume
    volume_id = in_use_volumes[0]["VolumeId"]

    try:
        reservations_table = dynamodb.Table(config.reservations_table)
        response = reservations_table.scan(
            FilterExpression="ebs_volume_id = :vol_id AND #status IN (:active, :preparing)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":vol_id": volume_id,
                ":active": "active",
                ":preparing": "preparing"
            }
        )

        if response.get("Items"):
            reservation_id = response["Items"][0]["reservation_id"]
            return True, reservation_id

    except Exception as e:
        # If DynamoDB query fails, still return that disk is in use
        print(f"Warning: Could not query reservations: {e}")
        return True, None

    return False, None


def list_disks(user_id: str, config: Config) -> List[Dict]:
    """
    List all disks for a user.
    Returns list of disk info dicts with: name, size, last_used, created_at, snapshot_count, in_use, reservation_id
    """
    ec2_client = get_ec2_client(config)

    # Get all snapshots for this user
    response = ec2_client.describe_snapshots(
        OwnerIds=["self"],
        Filters=[
            {"Name": "tag:gpu-dev-user", "Values": [user_id]},
            {"Name": "status", "Values": ["completed"]},
        ]
    )

    snapshots = response.get('Snapshots', [])

    # Group snapshots by disk_name
    disks_map = {}
    for snapshot in snapshots:
        tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}
        disk_name = tags.get('disk_name', 'default')

        if disk_name not in disks_map:
            disks_map[disk_name] = {
                'name': disk_name,
                'snapshots': [],
                'size_gb': None,
                'created_at': None,
                'last_used': None,
            }

        disks_map[disk_name]['snapshots'].append(snapshot)

    # Process each disk
    disks = []
    for disk_name, disk_data in disks_map.items():
        snapshots_list = disk_data['snapshots']

        # Get latest snapshot for metadata
        if snapshots_list:
            latest_snapshot = max(snapshots_list, key=lambda s: s['StartTime'])
            size_gb = latest_snapshot.get('VolumeSize', 0)

            # Get created_at from oldest snapshot
            oldest_snapshot = min(snapshots_list, key=lambda s: s['StartTime'])
            created_at = oldest_snapshot['StartTime']

            # Get last_used from latest snapshot
            last_used = latest_snapshot['StartTime']
        else:
            size_gb = 0
            created_at = None
            last_used = None

        # Check if disk is in use
        is_in_use, reservation_id = get_disk_in_use_status(disk_name, user_id, config)

        disks.append({
            'name': disk_name,
            'size_gb': size_gb,
            'created_at': created_at,
            'last_used': last_used,
            'snapshot_count': len(snapshots_list),
            'in_use': is_in_use,
            'reservation_id': reservation_id,
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

    try:
        # Create a temporary 1GB volume
        # We need to get an availability zone from the config
        azs_response = ec2_client.describe_availability_zones(
            Filters=[{"Name": "state", "Values": ["available"]}]
        )
        az = azs_response['AvailabilityZones'][0]['ZoneName']

        create_vol_response = ec2_client.create_volume(
            AvailabilityZone=az,
            Size=1,  # 1GB temporary volume
            VolumeType="gp3",
            TagSpecifications=[{
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "gpu-dev-user", "Value": user_id},
                    {"Key": "disk_name", "Value": disk_name},
                    {"Key": "Name", "Value": f"gpu-dev-disk-{user_id.split('@')[0]}-{disk_name}-temp"},
                    {"Key": "temporary", "Value": "true"},
                ],
            }]
        )

        volume_id = create_vol_response["VolumeId"]
        print(f"Created temporary volume {volume_id}, waiting for availability...")

        # Wait for volume to be available
        waiter = ec2_client.get_waiter("volume_available")
        waiter.wait(VolumeIds=[volume_id], WaiterConfig={"Delay": 2, "MaxAttempts": 30})

        # Create initial snapshot
        print(f"Creating initial snapshot for disk '{disk_name}'...")
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
        print(f"Created snapshot {snapshot_id}, waiting for completion...")

        # Wait for snapshot to complete
        waiter = ec2_client.get_waiter("snapshot_completed")
        waiter.wait(SnapshotIds=[snapshot_id], WaiterConfig={"Delay": 5, "MaxAttempts": 60})

        # Delete temporary volume
        print(f"Cleaning up temporary volume...")
        ec2_client.delete_volume(VolumeId=volume_id)

        print(f"✓ Successfully created disk '{disk_name}'")
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
