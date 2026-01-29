"""
GPU Availability Database Operations

Provides PostgreSQL operations for GPU availability tracking.
Replaces DynamoDB operations from availability_updater Lambda.
Updates gpu_types table with real-time availability from Kubernetes.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, UTC

from .db_pool import get_db_cursor

logger = logging.getLogger(__name__)


def get_gpu_availability(gpu_type: str) -> Optional[Dict[str, Any]]:
    """
    Get availability metrics for a specific GPU type from gpu_types table.
    
    Args:
        gpu_type: GPU type identifier (e.g., 'h100', 'a100')
    
    Returns:
        Dict with availability metrics, or None if not found
    """
    with get_db_cursor(readonly=True) as cur:
        cur.execute("""
            SELECT 
                gpu_type,
                total_cluster_gpus as total_gpus,
                available_gpus,
                max_reservable,
                full_nodes_available,
                running_instances,
                desired_capacity,
                max_per_node as gpus_per_instance,
                last_availability_update as last_updated_at,
                last_availability_updated_by as last_updated_by
            FROM gpu_types
            WHERE gpu_type = %s
        """, (gpu_type,))
        
        row = cur.fetchone()
        return dict(row) if row else None


def list_gpu_availability() -> List[Dict[str, Any]]:
    """
    List availability for all active GPU types from gpu_types table.
    
    Returns:
        List of dicts with availability metrics for all GPU types
    """
    with get_db_cursor(readonly=True) as cur:
        cur.execute("""
            SELECT 
                gpu_type,
                total_cluster_gpus as total_gpus,
                available_gpus,
                max_reservable,
                full_nodes_available,
                running_instances,
                desired_capacity,
                max_per_node as gpus_per_instance,
                last_availability_update as last_updated_at,
                last_availability_updated_by as last_updated_by
            FROM gpu_types
            WHERE is_active = true
            ORDER BY gpu_type
        """)
        
        return [dict(row) for row in cur.fetchall()]


def update_gpu_availability(
    gpu_type: str,
    total_gpus: int,
    available_gpus: int,
    max_reservable: int,
    full_nodes_available: int,
    running_instances: int,
    desired_capacity: int,
    gpus_per_instance: int,
    updated_by: str = "availability-updater"
) -> None:
    """
    Update availability metrics for a GPU type in gpu_types table.
    
    Updates the dynamic availability columns while preserving static config.
    
    Args:
        gpu_type: GPU type identifier
        total_gpus: Total GPUs across all instances (updates total_cluster_gpus)
        available_gpus: Schedulable GPUs (from K8s)
        max_reservable: Max GPUs for single reservation
        full_nodes_available: Count of nodes with all GPUs free
        running_instances: Running ASG instances
        desired_capacity: Total ASG desired capacity
        gpus_per_instance: GPUs per instance (updates max_per_node)
        updated_by: Identifier of updater (job name, pod name, etc.)
    """
    with get_db_cursor() as cur:
        # Update gpu_types table with real-time availability
        # Note: We update total_cluster_gpus with actual K8s count (replaces static config)
        cur.execute("""
            UPDATE gpu_types SET
                total_cluster_gpus = %s,
                available_gpus = %s,
                max_reservable = %s,
                full_nodes_available = %s,
                running_instances = %s,
                desired_capacity = %s,
                max_per_node = %s,
                last_availability_update = %s,
                last_availability_updated_by = %s
            WHERE gpu_type = %s
        """, (
            total_gpus,
            available_gpus,
            max_reservable,
            full_nodes_available,
            running_instances,
            desired_capacity,
            gpus_per_instance,
            datetime.now(UTC),
            updated_by,
            gpu_type
        ))
        
        if cur.rowcount == 0:
            logger.warning(f"GPU type {gpu_type} not found in gpu_types table - skipping update")
        else:
            logger.info(
                f"Updated availability for {gpu_type}: {available_gpus}/{total_gpus} GPUs "
                f"({full_nodes_available} full nodes, max reservable: {max_reservable})"
            )


def get_supported_gpu_types() -> Dict[str, Dict[str, Any]]:
    """
    Get all active GPU types from gpu_types table.
    
    Returns:
        Dict mapping gpu_type to configuration:
        {
            'h100': {'gpus_per_instance': 8, 'max_gpus': 32, ...},
            'a100': {'gpus_per_instance': 8, 'max_gpus': 32, ...},
            ...
        }
    """
    with get_db_cursor(readonly=True) as cur:
        cur.execute("""
            SELECT 
                gpu_type,
                instance_type,
                max_gpus,
                cpus,
                memory_gb,
                max_per_node
            FROM gpu_types
            WHERE is_active = true
            ORDER BY gpu_type
        """)
        
        result = {}
        for row in cur.fetchall():
            row_dict = dict(row)
            gpu_type = row_dict['gpu_type']
            
            # Calculate gpus_per_instance from max_per_node or max_gpus
            # CRITICAL: Use explicit None check to handle 0 correctly
            # CPU instances have max_per_node=0, using 'or' would incorrectly fall back to max_gpus
            max_per_node = row_dict.get('max_per_node')
            if max_per_node is not None:
                gpus_per_instance = max_per_node
            else:
                # Fallback if max_per_node column is NULL (shouldn't happen with current schema)
                gpus_per_instance = row_dict.get('max_gpus', 8)
            
            result[gpu_type] = {
                'gpus_per_instance': gpus_per_instance,
                'instance_type': row_dict['instance_type'],
                'max_gpus': row_dict['max_gpus'],
                'cpus': row_dict['cpus'],
                'memory_gb': row_dict['memory_gb'],
            }
        
        return result

