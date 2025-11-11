#!/usr/bin/env python3
"""
Migration script to add disk_name tags to existing EBS volumes and snapshots.

This script:
1. Finds all EBS volumes with ManagedBy=gpu-dev-cli tag
2. Groups volumes by user (gpu-dev-user tag)
3. Auto-assigns disk_name tags (disk1, disk2, etc.) to volumes without disk_name
4. Finds snapshots for each volume and tags them with matching disk_name

Usage:
    python migrate_disks_to_named.py [--dry-run] [--region us-east-2]
"""

import boto3
import argparse
from datetime import datetime
from collections import defaultdict


def migrate_disks(region='us-east-2', dry_run=True):
    """
    Migrate existing volumes and snapshots to named disk system.

    Args:
        region: AWS region
        dry_run: If True, only print what would be done without making changes
    """
    ec2_client = boto3.client('ec2', region_name=region)

    print(f"🔍 Scanning for gpu-dev volumes in {region}...")
    print(f"Mode: {'DRY RUN (no changes)' if dry_run else 'LIVE (will modify tags)'}\n")

    # Find all gpu-dev managed volumes
    response = ec2_client.describe_volumes(
        Filters=[
            {"Name": "tag:ManagedBy", "Values": ["gpu-dev-cli"]},
        ]
    )

    volumes = response.get('Volumes', [])
    print(f"Found {len(volumes)} gpu-dev managed volumes\n")

    if not volumes:
        print("✅ No volumes to migrate")
        return

    # Group volumes by user
    user_volumes = defaultdict(list)
    for volume in volumes:
        tags = {tag['Key']: tag['Value'] for tag in volume.get('Tags', [])}
        user_id = tags.get('gpu-dev-user')

        if not user_id:
            print(f"⚠️  Volume {volume['VolumeId']} has no gpu-dev-user tag, skipping")
            continue

        # Check if already has disk_name
        if 'disk_name' in tags:
            print(f"✓ Volume {volume['VolumeId']} already has disk_name='{tags['disk_name']}' (user: {user_id})")
            continue

        user_volumes[user_id].append(volume)

    if not user_volumes:
        print("✅ All volumes already have disk_name tags")
        return

    print(f"\n📋 Found volumes needing migration for {len(user_volumes)} users:\n")

    # Process each user's volumes
    total_volumes_migrated = 0
    total_snapshots_migrated = 0

    for user_id, user_vol_list in user_volumes.items():
        print(f"👤 User: {user_id}")
        print(f"   Volumes to migrate: {len(user_vol_list)}")

        # Sort volumes by creation time (oldest first)
        user_vol_list.sort(key=lambda v: v.get('CreateTime', datetime.min))

        # Assign disk names
        for idx, volume in enumerate(user_vol_list, start=1):
            volume_id = volume['VolumeId']
            disk_name = f"disk{idx}"
            state = volume['State']
            size_gb = volume['Size']
            created = volume.get('CreateTime', 'unknown')

            print(f"   • {volume_id} → disk_name='{disk_name}' ({size_gb}GB, {state}, created: {created})")

            if not dry_run:
                # Tag the volume
                try:
                    ec2_client.create_tags(
                        Resources=[volume_id],
                        Tags=[
                            {"Key": "disk_name", "Value": disk_name},
                            {"Key": "migrated_at", "Value": str(int(datetime.now().timestamp()))},
                        ]
                    )
                    print(f"      ✓ Tagged volume")
                except Exception as e:
                    print(f"      ✗ Error tagging volume: {e}")
                    continue

            total_volumes_migrated += 1

            # Find snapshots for this volume
            try:
                snap_response = ec2_client.describe_snapshots(
                    OwnerIds=["self"],
                    Filters=[
                        {"Name": "volume-id", "Values": [volume_id]},
                    ]
                )

                snapshots = snap_response.get('Snapshots', [])

                if snapshots:
                    print(f"      Found {len(snapshots)} snapshots for this volume")

                    for snapshot in snapshots:
                        snapshot_id = snapshot['SnapshotId']
                        snap_tags = {tag['Key']: tag['Value'] for tag in snapshot.get('Tags', [])}

                        # Skip if already has disk_name
                        if 'disk_name' in snap_tags:
                            print(f"         {snapshot_id} already has disk_name")
                            continue

                        if not dry_run:
                            try:
                                ec2_client.create_tags(
                                    Resources=[snapshot_id],
                                    Tags=[
                                        {"Key": "disk_name", "Value": disk_name},
                                        {"Key": "migrated_at", "Value": str(int(datetime.now().timestamp()))},
                                    ]
                                )
                                print(f"         ✓ Tagged snapshot {snapshot_id}")
                            except Exception as e:
                                print(f"         ✗ Error tagging snapshot: {e}")
                        else:
                            print(f"         {snapshot_id} → disk_name='{disk_name}'")

                        total_snapshots_migrated += 1

            except Exception as e:
                print(f"      ⚠️  Error finding snapshots: {e}")

        print()

    # Summary
    print("=" * 60)
    print(f"📊 Migration Summary")
    print("=" * 60)
    print(f"Users processed: {len(user_volumes)}")
    print(f"Volumes {'that would be migrated' if dry_run else 'migrated'}: {total_volumes_migrated}")
    print(f"Snapshots {'that would be migrated' if dry_run else 'migrated'}: {total_snapshots_migrated}")

    if dry_run:
        print("\n⚠️  This was a DRY RUN. No changes were made.")
        print("   Run with --no-dry-run to apply changes.")
    else:
        print("\n✅ Migration complete!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate existing gpu-dev volumes to named disk system"
    )
    parser.add_argument(
        "--region",
        default="us-east-2",
        help="AWS region (default: us-east-2)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Dry run mode - show what would be done without making changes (default)"
    )
    parser.add_argument(
        "--no-dry-run",
        action="store_false",
        dest="dry_run",
        help="Actually apply the migration (no dry run)"
    )

    args = parser.parse_args()

    migrate_disks(region=args.region, dry_run=args.dry_run)
