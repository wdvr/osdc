#!/usr/bin/env python3
"""
Migrate disk metadata from DynamoDB to PostgreSQL

This script reads all disk records from DynamoDB and inserts them into PostgreSQL.
Run this once when migrating from DynamoDB to PostgreSQL.

Usage:
    python migrate_disks_dynamodb_to_postgres.py --dry-run  # Preview migration
    python migrate_disks_dynamodb_to_postgres.py            # Perform migration

Environment variables required:
    - AWS_REGION or use default
    - DATABASE_URL or individual POSTGRES_* variables
    - DYNAMODB_DISKS_TABLE (default: pytorch-gpu-dev-disks)
"""

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

import asyncpg
import boto3
from botocore.exceptions import ClientError


# Configuration
AWS_REGION = os.getenv("AWS_REGION", "us-east-2")
DYNAMODB_DISKS_TABLE = os.getenv("DYNAMODB_DISKS_TABLE", "pytorch-gpu-dev-disks")

# PostgreSQL connection
if os.getenv("DATABASE_URL"):
    DATABASE_URL = os.getenv("DATABASE_URL")
else:
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres-primary.gpu-controlplane.svc.cluster.local")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "gpudev")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "CHANGEME")
    POSTGRES_DB = os.getenv("POSTGRES_DB", "gpudev")
    
    DATABASE_URL = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )


def decimal_to_python(obj):
    """Convert DynamoDB Decimal types to Python types"""
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        else:
            return float(obj)
    elif isinstance(obj, dict):
        return {k: decimal_to_python(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [decimal_to_python(v) for v in obj]
    return obj


def parse_dynamodb_timestamp(ts_str):
    """Parse DynamoDB timestamp string to Python datetime"""
    if not ts_str:
        return None
    try:
        # Try parsing with timezone
        return datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
    except:
        try:
            # Try parsing without timezone
            dt = datetime.fromisoformat(ts_str)
            # Assume UTC if no timezone
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except:
            return None


async def fetch_all_disks_from_dynamodb():
    """Fetch all disk records from DynamoDB"""
    print(f"📥 Fetching all disks from DynamoDB table: {DYNAMODB_DISKS_TABLE}")
    
    session = boto3.Session(region_name=AWS_REGION)
    dynamodb = session.resource('dynamodb')
    table = dynamodb.Table(DYNAMODB_DISKS_TABLE)
    
    disks = []
    try:
        # Scan entire table (paginated)
        response = table.scan()
        disks.extend(response.get('Items', []))
        
        # Handle pagination
        while 'LastEvaluatedKey' in response:
            response = table.scan(ExclusiveStartKey=response['LastEvaluatedKey'])
            disks.extend(response.get('Items', []))
        
        print(f"✅ Found {len(disks)} disks in DynamoDB")
        return disks
    
    except ClientError as e:
        print(f"❌ Error fetching from DynamoDB: {e}")
        sys.exit(1)


async def insert_disk_to_postgres(conn, disk_data, dry_run=False):
    """Insert a single disk record into PostgreSQL"""
    # Convert DynamoDB Decimal types
    disk = decimal_to_python(disk_data)
    
    # Extract fields
    disk_name = disk.get('disk_name')
    user_id = disk.get('user_id')
    size_gb = disk.get('size_gb')
    created_at = parse_dynamodb_timestamp(disk.get('created_at'))
    last_used = parse_dynamodb_timestamp(disk.get('last_used'))
    in_use = disk.get('in_use', False)
    is_backing_up = disk.get('is_backing_up', False)
    is_deleted = disk.get('is_deleted', False)
    snapshot_count = disk.get('snapshot_count', 0)
    pending_snapshot_count = disk.get('pending_snapshot_count', 0)
    ebs_volume_id = disk.get('ebs_volume_id')
    last_snapshot_at = parse_dynamodb_timestamp(disk.get('last_snapshot_at'))
    
    # Parse delete_date if exists
    delete_date = None
    if disk.get('delete_date'):
        try:
            delete_date = datetime.strptime(disk['delete_date'], '%Y-%m-%d').date()
        except:
            pass
    
    if dry_run:
        print(f"  [DRY RUN] Would insert: {user_id}/{disk_name} ({size_gb}GB)")
        return True
    
    try:
        # Insert into PostgreSQL (ON CONFLICT DO UPDATE to handle duplicates)
        await conn.execute("""
            INSERT INTO disks (
                disk_name, user_id, size_gb, created_at, last_used,
                in_use, is_backing_up, is_deleted, delete_date,
                snapshot_count, pending_snapshot_count, ebs_volume_id,
                last_snapshot_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13
            )
            ON CONFLICT (user_id, disk_name) DO UPDATE SET
                size_gb = EXCLUDED.size_gb,
                created_at = EXCLUDED.created_at,
                last_used = EXCLUDED.last_used,
                in_use = EXCLUDED.in_use,
                is_backing_up = EXCLUDED.is_backing_up,
                is_deleted = EXCLUDED.is_deleted,
                delete_date = EXCLUDED.delete_date,
                snapshot_count = EXCLUDED.snapshot_count,
                pending_snapshot_count = EXCLUDED.pending_snapshot_count,
                ebs_volume_id = EXCLUDED.ebs_volume_id,
                last_snapshot_at = EXCLUDED.last_snapshot_at,
                last_updated = NOW()
        """, disk_name, user_id, size_gb, created_at, last_used,
            in_use, is_backing_up, is_deleted, delete_date,
            snapshot_count, pending_snapshot_count, ebs_volume_id,
            last_snapshot_at)
        
        return True
    
    except Exception as e:
        print(f"  ❌ Error inserting {user_id}/{disk_name}: {e}")
        return False


async def migrate_disks(dry_run=False):
    """Main migration function"""
    print("=" * 70)
    print("  Disk Migration: DynamoDB → PostgreSQL")
    print("=" * 70)
    
    if dry_run:
        print("\n🔍 DRY RUN MODE - No changes will be made\n")
    
    # Fetch all disks from DynamoDB
    disks = await fetch_all_disks_from_dynamodb()
    
    if not disks:
        print("\n✅ No disks to migrate!")
        return
    
    # Connect to PostgreSQL
    print(f"\n📤 Connecting to PostgreSQL...")
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        print("✅ Connected to PostgreSQL")
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        sys.exit(1)
    
    # Migrate each disk
    print(f"\n🔄 Migrating {len(disks)} disks...")
    success_count = 0
    error_count = 0
    
    for i, disk in enumerate(disks, 1):
        disk_name = disk.get('disk_name', 'unknown')
        user_id = disk.get('user_id', 'unknown')
        
        if (i-1) % 10 == 0:
            print(f"  Progress: {i}/{len(disks)}")
        
        if await insert_disk_to_postgres(conn, disk, dry_run):
            success_count += 1
        else:
            error_count += 1
    
    await conn.close()
    
    # Summary
    print("\n" + "=" * 70)
    print("  Migration Summary")
    print("=" * 70)
    print(f"  Total disks:     {len(disks)}")
    print(f"  ✅ Successful:   {success_count}")
    print(f"  ❌ Errors:       {error_count}")
    
    if dry_run:
        print("\n🔍 This was a DRY RUN. Run without --dry-run to perform migration.")
    else:
        print("\n✅ Migration complete!")
    
    return error_count == 0


async def verify_migration():
    """Verify migration by comparing counts"""
    print("\n" + "=" * 70)
    print("  Verification")
    print("=" * 70)
    
    # Count in DynamoDB
    session = boto3.Session(region_name=AWS_REGION)
    dynamodb = session.resource('dynamodb')
    table = dynamodb.Table(DYNAMODB_DISKS_TABLE)
    
    response = table.scan(Select='COUNT')
    dynamodb_count = response['Count']
    print(f"  DynamoDB disks:  {dynamodb_count}")
    
    # Count in PostgreSQL
    conn = await asyncpg.connect(DATABASE_URL)
    postgres_count = await conn.fetchval("SELECT COUNT(*) FROM disks")
    await conn.close()
    print(f"  PostgreSQL disks: {postgres_count}")
    
    if dynamodb_count == postgres_count:
        print("\n  ✅ Counts match!")
    else:
        print(f"\n  ⚠️  Count mismatch! Difference: {abs(dynamodb_count - postgres_count)}")


def main():
    parser = argparse.ArgumentParser(description='Migrate disk metadata from DynamoDB to PostgreSQL')
    parser.add_argument('--dry-run', action='store_true', help='Preview migration without making changes')
    parser.add_argument('--verify', action='store_true', help='Verify migration by comparing counts')
    args = parser.parse_args()
    
    if args.verify:
        asyncio.run(verify_migration())
    else:
        success = asyncio.run(migrate_disks(dry_run=args.dry_run))
        if success and not args.dry_run:
            print("\n🔍 Running verification...")
            asyncio.run(verify_migration())
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()

