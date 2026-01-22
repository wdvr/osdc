"""
GPU Availability Updater - Kubernetes CronJob
Updates GPU availability table by querying ASG and Kubernetes API

Migrated from Lambda function to Kubernetes CronJob
"""

import sys
import os
import logging
from datetime import datetime, UTC
from typing import Dict, Any

# Add parent directory to path for shared imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3
from kubernetes import client

from shared.db_pool import init_connection_pool, close_connection_pool
from shared.availability_db import update_gpu_availability, get_supported_gpu_types
from shared.k8s_client import setup_kubernetes_client

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

# AWS clients
autoscaling = boto3.client("autoscaling")

# Environment variables
AWS_REGION = os.environ.get("AWS_REGION", "us-east-2")
EKS_CLUSTER_NAME = os.environ.get("EKS_CLUSTER_NAME", "pytorch-gpu-dev-cluster")

# Kubernetes client singleton
_k8s_client = None


def get_k8s_client():
    """Get or create Kubernetes client (singleton pattern)"""
    global _k8s_client
    if _k8s_client is None:
        logger.info("Setting up Kubernetes client")
        _k8s_client = setup_kubernetes_client()
        logger.info("Kubernetes client ready")
    return _k8s_client


def update_gpu_availability_for_type(gpu_type: str, gpu_config: Dict[str, Any], k8s_client) -> None:
    """Update availability information for a specific GPU type"""
    try:
        logger.info(f"Starting availability update for GPU type: {gpu_type}")

        # Get current ASG capacity - handle multiple ASGs per GPU type (e.g., capacity reservations)
        # Get GPU configuration to check if this is a CPU type
        gpus_per_instance = gpu_config.get("gpus_per_instance", 8)
        
        # Validate configuration
        if gpus_per_instance < 0:
            logger.error(f"Invalid gpus_per_instance for {gpu_type}: {gpus_per_instance} (must be >= 0)")
            return
        
        if gpus_per_instance == 0:
            logger.info(f"GPU type {gpu_type} has gpus_per_instance=0, treating as CPU-only instance type")
        
        is_cpu_type = gpus_per_instance == 0
        
        # Build ASG name patterns to try
        # CPU types may use different naming conventions
        asg_patterns = []
        if is_cpu_type:
            # Try multiple patterns for CPU types
            asg_patterns = [
                f"pytorch-gpu-dev-gpu-nodes-{gpu_type}",  # Standard pattern
                f"pytorch-gpu-dev-cpu-nodes-{gpu_type}",  # CPU-specific pattern
                "pytorch-gpu-dev-cpu-nodes",               # Generic CPU pattern
            ]
            logger.info(f"CPU type detected, trying multiple ASG patterns: {asg_patterns}")
        else:
            # GPU types use standard pattern
            asg_patterns = [f"pytorch-gpu-dev-gpu-nodes-{gpu_type}"]
            logger.info(f"Checking ASGs matching pattern: {asg_patterns[0]}*")

        # Get all ASGs and filter by name pattern
        all_asgs_response = autoscaling.describe_auto_scaling_groups()
        
        # Try each pattern until we find matching ASGs
        matching_asgs = []
        matched_pattern = None
        for pattern in asg_patterns:
            matching_asgs = [
                asg for asg in all_asgs_response["AutoScalingGroups"]
                if asg["AutoScalingGroupName"].startswith(pattern)
            ]
            if matching_asgs:
                matched_pattern = pattern
                logger.info(f"Found {len(matching_asgs)} ASGs using pattern: {pattern}*")
                break

        if not matching_asgs:
            logger.warning(f"No ASGs found for {gpu_type}. Tried patterns: {asg_patterns}")
            # For CPU types, this might be expected if no CPU ASGs exist yet
            if is_cpu_type:
                logger.info(f"No CPU ASGs found - this may be normal if CPU nodes not yet deployed")
            return

        asg_names = [asg["AutoScalingGroupName"] for asg in matching_asgs]
        logger.info(f"Found {len(matching_asgs)} ASGs: {asg_names}")

        # Calculate total availability metrics across all matching ASGs
        desired_capacity = sum(asg["DesiredCapacity"] for asg in matching_asgs)
        running_instances = sum(
            len([
                instance for instance in asg["Instances"]
                if instance["LifecycleState"] == "InService"
            ]) for asg in matching_asgs
        )

        # gpus_per_instance and is_cpu_type already determined above

        if is_cpu_type:
            # For CPU nodes, report instance slots (assuming 3 users per node)
            max_users_per_node = 3
            total_gpus = running_instances * max_users_per_node
            logger.info(
                f"CPU ASG calculation: {running_instances} instances * {max_users_per_node} slots = {total_gpus} total slots")

            # Check actual pod usage on CPU nodes
            if k8s_client is not None:
                try:
                    logger.info(f"Checking CPU node availability for {gpu_type}")
                    # Count available slots by checking pod count on each node
                    v1 = client.CoreV1Api(k8s_client)
                    nodes = v1.list_node(label_selector=f"GpuType={gpu_type}")

                    total_available_slots = 0
                    for node in nodes.items:
                        if is_node_ready_and_schedulable(node):
                            # Count gpu-dev pods on this node
                            pods = v1.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node.metadata.name}")
                            gpu_dev_pods = [p for p in pods.items if p.metadata.name.startswith('gpu-dev-')]
                            used_slots = len(gpu_dev_pods)
                            available_slots = max(0, max_users_per_node - used_slots)
                            total_available_slots += available_slots

                    available_gpus = total_available_slots
                    logger.info(f"Found {available_gpus} available CPU slots across {len(nodes.items)} nodes")
                except Exception as k8s_error:
                    logger.warning(f"Failed to query Kubernetes for {gpu_type} CPU availability: {k8s_error}")
                    available_gpus = total_gpus
            else:
                available_gpus = total_gpus
        else:
            # GPU nodes - use existing logic
            total_gpus = running_instances * gpus_per_instance
            logger.info(
                f"ASG calculation: {running_instances} instances * {gpus_per_instance} GPUs = {total_gpus} total GPUs")

            # Query Kubernetes API for actual GPU allocations
            if k8s_client is not None:
                try:
                    logger.info(f"Starting Kubernetes query for {gpu_type} GPU availability")
                    available_gpus = check_schedulable_gpus_for_type(k8s_client, gpu_type)
                    logger.info(f"Kubernetes reports {available_gpus} schedulable {gpu_type.upper()} GPUs")

                except Exception as k8s_error:
                    logger.warning(f"Failed to query Kubernetes for {gpu_type} availability: {k8s_error}")
                    # Fallback to ASG-based calculation (assume all GPUs available)
                    available_gpus = total_gpus
            else:
                logger.warning(f"No Kubernetes client available for {gpu_type}, using ASG-based calculation")
                # Fallback to ASG-based calculation (assume all GPUs available)
                available_gpus = total_gpus

        # Calculate full nodes available (nodes with all GPUs free) and max reservable
        full_nodes_available = 0
        max_reservable = 0  # Maximum GPUs reservable (considering multinode for high-end GPUs)
        if k8s_client is not None and not is_cpu_type:
            try:
                v1 = client.CoreV1Api(k8s_client)
                nodes = v1.list_node(label_selector=f"GpuType={gpu_type}")

                single_node_max = 0  # Max available on any single node
                for node in nodes.items:
                    if is_node_ready_and_schedulable(node):
                        available_on_node = get_available_gpus_on_node(v1, node)
                        total_on_node = 0
                        if node.status.allocatable:
                            gpu_allocatable = node.status.allocatable.get("nvidia.com/gpu", "0")
                            try:
                                total_on_node = int(gpu_allocatable)
                            except (ValueError, TypeError):
                                pass

                        # Track max available on any single node
                        single_node_max = max(single_node_max, available_on_node)

                        # Count as full node if all GPUs are available
                        if total_on_node > 0 and available_on_node == total_on_node:
                            full_nodes_available += 1

                # Calculate max reservable considering multinode scenarios
                # Only high-end GPU types support multinode (up to 4 nodes = 32 GPUs)
                multinode_gpu_types = ['h100', 'h200', 'b200', 'a100']
                if gpu_type in multinode_gpu_types and gpus_per_instance == 8:
                    max_nodes = min(4, full_nodes_available)  # Up to 4 nodes
                    max_reservable = max_nodes * gpus_per_instance  # e.g., 4 * 8 = 32 GPUs

                    # If no full nodes available, fall back to single node max
                    if max_reservable == 0:
                        max_reservable = single_node_max
                else:
                    # For all other GPU types (T4, L4, T4-small, etc.), only single node
                    max_reservable = single_node_max

                logger.info(f"Found {full_nodes_available} full nodes available for {gpu_type}, max reservable: {max_reservable} (single node max: {single_node_max})")
            except Exception as e:
                logger.warning(f"Could not calculate full nodes available for {gpu_type}: {str(e)}")
                full_nodes_available = 0
                max_reservable = 0
        elif is_cpu_type:
            # For CPU nodes, each node supports 1 reservation
            full_nodes_available = available_gpus  # Each "GPU" represents one CPU node slot
            max_reservable = 1 if available_gpus > 0 else 0  # Max 1 CPU node per reservation

        # Get pod name for tracking (Kubernetes sets HOSTNAME to pod name)
        # Fallback chain: HOSTNAME -> POD_NAME -> generic name
        pod_name = os.environ.get("HOSTNAME") or os.environ.get("POD_NAME") or "availability-updater-unknown"

        # Update PostgreSQL table
        update_gpu_availability(
            gpu_type=gpu_type,
            total_gpus=total_gpus,
            available_gpus=available_gpus,
            max_reservable=max_reservable,
            full_nodes_available=full_nodes_available,
            running_instances=running_instances,
            desired_capacity=desired_capacity,
            gpus_per_instance=gpus_per_instance,
            updated_by=pod_name
        )

        logger.info(
            f"Updated {gpu_type}: {available_gpus}/{total_gpus} GPUs available "
            f"({running_instances} instances, {full_nodes_available} full nodes, max reservable: {max_reservable})"
        )

    except Exception as e:
        logger.error(f"Error updating availability for {gpu_type}: {str(e)}", exc_info=True)
        raise


def check_schedulable_gpus_for_type(k8s_client, gpu_type: str) -> int:
    """Check how many GPUs of a specific type are schedulable (available for new pods)"""
    try:
        logger.info(f"Starting schedulable GPU check for type: {gpu_type}")
        v1 = client.CoreV1Api(k8s_client)
        logger.info(f"Created CoreV1Api client for {gpu_type}")

        # Get all nodes with the specified GPU type
        gpu_type_selector = f"GpuType={gpu_type}"
        logger.info(f"Querying nodes with label selector: {gpu_type_selector}")

        nodes = v1.list_node(label_selector=gpu_type_selector)
        logger.info(f"Retrieved {len(nodes.items) if nodes.items else 0} nodes for {gpu_type}")

        if not nodes.items:
            logger.warning(f"No nodes found for GPU type {gpu_type}")
            return 0

        total_schedulable = 0

        for i, node in enumerate(nodes.items):
            logger.info(f"Processing node {i + 1}/{len(nodes.items)}: {node.metadata.name}")

            if not is_node_ready_and_schedulable(node):
                logger.info(f"Node {node.metadata.name} is not ready/schedulable, skipping")
                continue

            logger.info(f"Node {node.metadata.name} is ready, checking GPU availability")
            # Get available GPUs on this node
            available_on_node = get_available_gpus_on_node(v1, node)
            total_schedulable += available_on_node
            logger.info(f"Node {node.metadata.name}: {available_on_node} GPUs available")

        logger.info(f"Found {total_schedulable} schedulable {gpu_type.upper()} GPUs across {len(nodes.items)} nodes")
        return total_schedulable

    except Exception as e:
        logger.error(f"Error checking schedulable GPUs for type {gpu_type}: {str(e)}", exc_info=True)
        return 0


def is_node_ready_and_schedulable(node) -> bool:
    """Check if a node is ready and schedulable"""
    try:
        # Check node conditions
        conditions = node.status.conditions or []
        is_ready = False

        for condition in conditions:
            if condition.type == "Ready":
                is_ready = condition.status == "True"
                break

        if not is_ready:
            return False

        # Check if node is schedulable (not cordoned)
        return not node.spec.unschedulable

    except Exception as e:
        logger.error(f"Error checking node readiness: {str(e)}")
        return False


def get_available_gpus_on_node(v1_api, node) -> int:
    """Get number of available GPUs on a specific node"""
    try:
        node_name = node.metadata.name
        logger.debug(f"Checking GPU availability on node: {node_name}")

        # Get all pods on this node
        logger.debug(f"Querying pods on node {node_name}")
        pods = v1_api.list_pod_for_all_namespaces(field_selector=f"spec.nodeName={node_name}")
        logger.debug(f"Found {len(pods.items)} pods on node {node_name}")

        # Calculate GPU usage
        used_gpus = 0
        for pod in pods.items:
            if pod.status.phase in ["Running", "Pending"]:
                for container in pod.spec.containers:
                    if container.resources and container.resources.requests:
                        gpu_request = container.resources.requests.get(
                            "nvidia.com/gpu", "0"
                        )
                        try:
                            used_gpus += int(gpu_request)
                        except (ValueError, TypeError):
                            pass

        # Get total GPUs on this node
        total_gpus = 0
        if node.status.allocatable:
            gpu_allocatable = node.status.allocatable.get("nvidia.com/gpu", "0")
            try:
                total_gpus = int(gpu_allocatable)
            except (ValueError, TypeError):
                pass

        available_gpus = max(0, total_gpus - used_gpus)
        logger.debug(f"Node {node_name}: {available_gpus}/{total_gpus} GPUs available")

        return available_gpus

    except Exception as e:
        logger.error(
            f"Error getting available GPUs on node {node.metadata.name}: {str(e)}"
        )
        return 0


def run_availability_update():
    """Main availability update logic"""
    logger.info("=== Starting GPU Availability Update ===")
    
    # Set up Kubernetes client once for all GPU types
    k8s_client = None
    try:
        logger.info("Setting up shared Kubernetes client for all GPU types")
        k8s_client = get_k8s_client()
        logger.info("Shared Kubernetes client ready")
    except Exception as k8s_setup_error:
        logger.error(f"Failed to setup Kubernetes client: {k8s_setup_error}", exc_info=True)
        k8s_client = None

    # Get supported GPU types from database
    logger.info("Fetching supported GPU types from database")
    gpu_types = get_supported_gpu_types()
    logger.info(f"Found {len(gpu_types)} GPU types to update: {list(gpu_types.keys())}")

    # Update availability for ALL GPU types
    updated_types = []
    failed_types = []
    
    for gpu_type, gpu_config in gpu_types.items():
        try:
            logger.info(f"=== Starting update for GPU type: {gpu_type} ===")
            update_gpu_availability_for_type(gpu_type, gpu_config, k8s_client)
            updated_types.append(gpu_type)
            logger.info(f"=== Successfully updated availability for GPU type: {gpu_type} ===")
        except Exception as gpu_error:
            logger.error(f"=== Failed to update availability for {gpu_type}: {gpu_error} ===", exc_info=True)
            failed_types.append(gpu_type)
            # Continue with other GPU types

    logger.info(f"=== Availability Update Complete ===")
    logger.info(f"Successfully updated: {len(updated_types)} GPU types: {updated_types}")
    if failed_types:
        logger.warning(f"Failed to update: {len(failed_types)} GPU types: {failed_types}")
    
    # Return success if at least one GPU type was updated
    return len(updated_types) > 0


def main():
    """Main entry point for CronJob execution"""
    start_time = datetime.now(UTC)
    logger.info(f"Availability updater starting at {start_time.isoformat()}")
    
    try:
        # Initialize database connection pool
        logger.info("Initializing database connection pool")
        init_connection_pool()
        logger.info("Database connection pool initialized")
        
        # Run availability update
        success = run_availability_update()
        
        end_time = datetime.now(UTC)
        duration = (end_time - start_time).total_seconds()
        logger.info(f"Availability update completed in {duration:.2f} seconds")
        
        if success:
            logger.info("Availability update completed successfully")
            return 0
        else:
            logger.error("Availability update failed - no GPU types were updated")
            return 1
            
    except Exception as e:
        logger.error(f"Availability update failed with exception: {e}", exc_info=True)
        return 1
    finally:
        # Close database connection pool
        try:
            logger.info("Closing database connection pool")
            close_connection_pool()
            logger.info("Database connection pool closed")
        except Exception as cleanup_error:
            logger.error(f"Error closing connection pool: {cleanup_error}")


if __name__ == "__main__":
    sys.exit(main())

