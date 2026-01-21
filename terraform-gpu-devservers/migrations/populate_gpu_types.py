#!/usr/bin/env python3
"""
Migration Script: Populate GPU Types Table

This script populates the gpu_types table with GPU configuration data
that was previously hardcoded in multiple places (API service, Lambda).

Usage:
    # From local machine (with kubectl port-forward)
    python populate_gpu_types.py

    # With custom database URL
    DATABASE_URL="postgresql://..." python populate_gpu_types.py

    # Dry run (show what would be inserted without making changes)
    python populate_gpu_types.py --dry-run
"""

import argparse
import asyncio
import os
import sys
from typing import Dict, Any

import asyncpg


# GPU Configuration - single source of truth
# This matches the configuration from lambda/reservation_processor/index.py
GPU_TYPES_CONFIG = {
    "t4": {
        "instance_type": "g4dn.12xlarge",
        "max_gpus": 4,
        "cpus": 48,
        "memory_gb": 192,
        "total_cluster_gpus": 8,  # 2 instances × 4 GPUs
        "max_per_node": 4,
        "description": "NVIDIA T4 - Entry-level GPU for inference and light training"
    },
    "t4-small": {
        "instance_type": "g4dn.2xlarge",
        "max_gpus": 1,
        "cpus": 8,
        "memory_gb": 32,
        "total_cluster_gpus": 1,
        "max_per_node": 1,
        "description": "NVIDIA T4 - Small instance for testing"
    },
    "l4": {
        "instance_type": "g6.12xlarge",
        "max_gpus": 4,
        "cpus": 48,
        "memory_gb": 192,
        "total_cluster_gpus": 4,
        "max_per_node": 4,
        "description": "NVIDIA L4 - Efficient GPU for inference and training"
    },
    "a10g": {
        "instance_type": "g5.12xlarge",
        "max_gpus": 4,
        "cpus": 48,
        "memory_gb": 192,
        "total_cluster_gpus": 4,
        "max_per_node": 4,
        "description": "NVIDIA A10G - Mid-range GPU for training and inference"
    },
    "a100": {
        "instance_type": "p4d.24xlarge",
        "max_gpus": 8,
        "cpus": 96,
        "memory_gb": 1152,
        "total_cluster_gpus": 16,  # 2 instances × 8 GPUs
        "max_per_node": 8,
        "description": "NVIDIA A100 - High-performance GPU for large-scale training"
    },
    "h100": {
        "instance_type": "p5.48xlarge",
        "max_gpus": 8,
        "cpus": 192,
        "memory_gb": 2048,
        "total_cluster_gpus": 16,  # 2 instances × 8 GPUs
        "max_per_node": 8,
        "description": "NVIDIA H100 - Top-tier GPU for AI training and HPC"
    },
    "h200": {
        "instance_type": "p5e.48xlarge",
        "max_gpus": 8,
        "cpus": 192,
        "memory_gb": 2048,
        "total_cluster_gpus": 16,  # 2 instances × 8 GPUs
        "max_per_node": 8,
        "description": "NVIDIA H200 - Latest generation with increased memory"
    },
    "b200": {
        "instance_type": "p6-b200.48xlarge",
        "max_gpus": 8,
        "cpus": 192,
        "memory_gb": 2048,
        "total_cluster_gpus": 16,  # 2 instances × 8 GPUs
        "max_per_node": 8,
        "description": "NVIDIA B200 - Next-generation Blackwell architecture"
    },
    "cpu-arm": {
        "instance_type": "c7g.8xlarge",
        "max_gpus": 0,
        "cpus": 32,
        "memory_gb": 64,
        "total_cluster_gpus": 0,
        "max_per_node": 0,
        "description": "ARM-based CPU instance (Graviton)"
    },
    "cpu-x86": {
        "instance_type": "c7i.8xlarge",
        "max_gpus": 0,
        "cpus": 32,
        "memory_gb": 64,
        "total_cluster_gpus": 0,
        "max_per_node": 0,
        "description": "x86-based CPU instance (Intel)"
    },
}


def get_database_url() -> str:
    """Get database URL from environment or construct from components"""
    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")
    
    # Build from individual components
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    user = os.getenv("POSTGRES_USER", "gpudev")
    password = os.getenv("POSTGRES_PASSWORD")
    database = os.getenv("POSTGRES_DB", "gpudev")
    
    if not password:
        print("Error: POSTGRES_PASSWORD environment variable is required")
        print("\nTo get the password from Kubernetes:")
        print("  kubectl get secret -n gpu-controlplane postgres-credentials \\")
        print("    -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d")
        print("\nThen set it:")
        print("  export POSTGRES_PASSWORD='<password>'")
        sys.exit(1)
    
    return f"postgresql://{user}:{password}@{host}:{port}/{database}"


async def populate_gpu_types(dry_run: bool = False) -> None:
    """Populate the gpu_types table with configuration data"""
    database_url = get_database_url()
    
    print(f"Connecting to database...")
    if dry_run:
        print("DRY RUN MODE - No changes will be made\n")
    
    conn = await asyncpg.connect(database_url)
    
    try:
        # Check if table exists
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_name = 'gpu_types'
            )
        """)
        
        if not table_exists:
            print("Error: gpu_types table does not exist!")
            print("Please ensure the API service has been deployed and initialized the schema.")
            sys.exit(1)
        
        # Get existing GPU types
        existing_types = await conn.fetch("SELECT gpu_type FROM gpu_types")
        existing_set = {row["gpu_type"] for row in existing_types}
        
        print(f"Found {len(existing_set)} existing GPU types in database")
        if existing_set:
            print(f"  Existing: {', '.join(sorted(existing_set))}")
        print()
        
        # Process each GPU type
        inserted = 0
        updated = 0
        skipped = 0
        
        for gpu_type, config in GPU_TYPES_CONFIG.items():
            if gpu_type in existing_set:
                # Update existing entry
                print(f"Updating: {gpu_type}")
                if not dry_run:
                    await conn.execute("""
                        UPDATE gpu_types
                        SET 
                            instance_type = $2,
                            max_gpus = $3,
                            cpus = $4,
                            memory_gb = $5,
                            total_cluster_gpus = $6,
                            max_per_node = $7,
                            description = $8,
                            is_active = true,
                            updated_at = NOW()
                        WHERE gpu_type = $1
                    """,
                        gpu_type,
                        config["instance_type"],
                        config["max_gpus"],
                        config["cpus"],
                        config["memory_gb"],
                        config["total_cluster_gpus"],
                        config["max_per_node"],
                        config.get("description")
                    )
                updated += 1
            else:
                # Insert new entry
                print(f"Inserting: {gpu_type}")
                if not dry_run:
                    await conn.execute("""
                        INSERT INTO gpu_types (
                            gpu_type,
                            instance_type,
                            max_gpus,
                            cpus,
                            memory_gb,
                            total_cluster_gpus,
                            max_per_node,
                            description,
                            is_active
                        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, true)
                    """,
                        gpu_type,
                        config["instance_type"],
                        config["max_gpus"],
                        config["cpus"],
                        config["memory_gb"],
                        config["total_cluster_gpus"],
                        config["max_per_node"],
                        config.get("description")
                    )
                inserted += 1
            
            # Show configuration
            print(f"  Instance: {config['instance_type']}")
            print(f"  Max GPUs per node: {config['max_gpus']}")
            print(f"  Total cluster GPUs: {config['total_cluster_gpus']}")
            print(f"  CPUs: {config['cpus']}, Memory: {config['memory_gb']}GB")
            if config.get("description"):
                print(f"  Description: {config['description']}")
            print()
        
        # Summary
        print("=" * 60)
        if dry_run:
            print("DRY RUN SUMMARY (no changes made):")
        else:
            print("MIGRATION SUMMARY:")
        print(f"  Inserted: {inserted}")
        print(f"  Updated:  {updated}")
        print(f"  Total:    {inserted + updated}")
        print("=" * 60)
        
        if not dry_run:
            # Show final state
            print("\nFinal GPU Types Configuration:")
            all_types = await conn.fetch("""
                SELECT 
                    gpu_type,
                    instance_type,
                    max_gpus,
                    total_cluster_gpus,
                    max_per_node,
                    is_active
                FROM gpu_types
                ORDER BY gpu_type
            """)
            
            for row in all_types:
                status = "✓" if row["is_active"] else "✗"
                print(f"  {status} {row['gpu_type']:12} → {row['instance_type']:20} "
                      f"({row['total_cluster_gpus']:2} GPUs, {row['max_per_node']} per node)")
    
    finally:
        await conn.close()


async def verify_migration() -> None:
    """Verify the migration was successful"""
    database_url = get_database_url()
    conn = await asyncpg.connect(database_url)
    
    try:
        # Count active GPU types
        count = await conn.fetchval("""
            SELECT COUNT(*) FROM gpu_types WHERE is_active = true
        """)
        
        print(f"\n✓ Migration verified: {count} active GPU types in database")
        
        # Check for any missing types
        all_types = await conn.fetch("SELECT gpu_type FROM gpu_types WHERE is_active = true")
        db_types = {row["gpu_type"] for row in all_types}
        config_types = set(GPU_TYPES_CONFIG.keys())
        
        missing = config_types - db_types
        if missing:
            print(f"⚠ Warning: Missing GPU types: {', '.join(missing)}")
        else:
            print("✓ All GPU types from config are present in database")
    
    finally:
        await conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Populate gpu_types table with GPU configuration data"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the migration was successful"
    )
    
    args = parser.parse_args()
    
    if args.verify:
        asyncio.run(verify_migration())
    else:
        asyncio.run(populate_gpu_types(dry_run=args.dry_run))


if __name__ == "__main__":
    main()

