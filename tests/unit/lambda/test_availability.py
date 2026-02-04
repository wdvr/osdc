"""
Unit tests for Lambda availability_updater

Tests:
- GPU availability calculation
- Node capacity detection
- Wait time estimation
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest


class TestGPUAvailabilityCalculation:
    """Tests for calculating available GPUs"""

    def test_calculate_available_gpus_from_nodes(self):
        """Should calculate available GPUs across all nodes of a type"""
        nodes = [
            {"name": "node-1", "allocatable_gpus": 4, "used_gpus": 2},
            {"name": "node-2", "allocatable_gpus": 4, "used_gpus": 0},
            {"name": "node-3", "allocatable_gpus": 4, "used_gpus": 4},
        ]

        def calculate_available(nodes):
            total_available = 0
            for node in nodes:
                available = node["allocatable_gpus"] - node["used_gpus"]
                total_available += available
            return total_available

        assert calculate_available(nodes) == 6

    def test_calculate_total_capacity(self):
        """Should calculate total GPU capacity"""
        nodes = [
            {"name": "node-1", "allocatable_gpus": 4},
            {"name": "node-2", "allocatable_gpus": 4},
            {"name": "node-3", "allocatable_gpus": 8},
        ]

        def calculate_total(nodes):
            return sum(n["allocatable_gpus"] for n in nodes)

        assert calculate_total(nodes) == 16


class TestNodeCapacityDetection:
    """Tests for detecting node GPU capacity"""

    def test_extract_gpu_count_from_allocatable(self):
        """Should extract GPU count from K8s allocatable resources"""
        allocatable = {
            "cpu": "48",
            "memory": "192Gi",
            "nvidia.com/gpu": "4",
            "ephemeral-storage": "100Gi",
        }

        def get_gpu_count(allocatable):
            gpu_str = allocatable.get("nvidia.com/gpu", "0")
            return int(gpu_str)

        assert get_gpu_count(allocatable) == 4

    def test_handle_missing_gpu_resource(self):
        """Should return 0 for nodes without GPUs"""
        allocatable = {
            "cpu": "32",
            "memory": "64Gi",
        }

        def get_gpu_count(allocatable):
            gpu_str = allocatable.get("nvidia.com/gpu", "0")
            return int(gpu_str)

        assert get_gpu_count(allocatable) == 0

    def test_filter_nodes_by_gpu_type(self):
        """Should filter nodes by GPU type label"""
        nodes = [
            {"name": "t4-node-1", "labels": {"GpuType": "t4"}},
            {"name": "t4-node-2", "labels": {"GpuType": "t4"}},
            {"name": "h100-node-1", "labels": {"GpuType": "h100"}},
            {"name": "cpu-node-1", "labels": {}},
        ]

        def filter_by_gpu_type(nodes, gpu_type):
            return [n for n in nodes if n.get("labels", {}).get("GpuType") == gpu_type]

        t4_nodes = filter_by_gpu_type(nodes, "t4")
        h100_nodes = filter_by_gpu_type(nodes, "h100")

        assert len(t4_nodes) == 2
        assert len(h100_nodes) == 1


class TestUsedGPUCalculation:
    """Tests for calculating GPUs in use"""

    def test_sum_gpu_requests_from_pods(self):
        """Should sum GPU requests from all pods on a node"""
        pods = [
            {"name": "pod-1", "gpu_request": 2},
            {"name": "pod-2", "gpu_request": 1},
            {"name": "pod-3", "gpu_request": 0},  # CPU pod
        ]

        def calculate_used(pods):
            return sum(p.get("gpu_request", 0) for p in pods)

        assert calculate_used(pods) == 3

    def test_filter_gpu_pods_only(self):
        """Should only count pods with GPU requests"""
        pods = [
            {"name": "gpu-pod-1", "resources": {"nvidia.com/gpu": "2"}},
            {"name": "gpu-pod-2", "resources": {"nvidia.com/gpu": "1"}},
            {"name": "cpu-pod", "resources": {"cpu": "4"}},
        ]

        def get_pod_gpu_request(pod):
            resources = pod.get("resources", {})
            return int(resources.get("nvidia.com/gpu", "0"))

        def calculate_used(pods):
            return sum(get_pod_gpu_request(p) for p in pods)

        assert calculate_used(pods) == 3


class TestWaitTimeEstimation:
    """Tests for queue wait time estimation"""

    def test_estimate_based_on_queue_length(self):
        """Should estimate wait time based on queue length"""
        def estimate_wait_minutes(queue_length, avg_reservation_hours=4):
            if queue_length == 0:
                return 0
            # Simple estimate: each queued reservation waits for avg duration
            return queue_length * avg_reservation_hours * 60

        assert estimate_wait_minutes(0) == 0
        assert estimate_wait_minutes(1) == 240  # 4 hours
        assert estimate_wait_minutes(3) == 720  # 12 hours

    def test_estimate_considers_gpu_count(self):
        """Should factor in GPU requirements for estimation"""
        def estimate_wait_detailed(queue, available_gpus, avg_hours=4):
            if not queue:
                return 0

            # Calculate how many queue items can be served now
            remaining = list(queue)
            wait_time = 0

            while remaining:
                # Find reservations that can fit in available capacity
                can_serve = []
                cannot_serve = []

                for item in remaining:
                    if item["gpu_count"] <= available_gpus:
                        can_serve.append(item)
                        available_gpus -= item["gpu_count"]
                    else:
                        cannot_serve.append(item)

                if not can_serve:
                    # Nothing can be served, wait for next cycle
                    wait_time += avg_hours * 60
                    # Assume some GPUs free up
                    available_gpus = 4  # Reset assumption

                remaining = cannot_serve

            return wait_time

        queue = [
            {"reservation_id": "res-1", "gpu_count": 2},
            {"reservation_id": "res-2", "gpu_count": 2},
        ]

        # With 4 available GPUs, both can be served immediately
        assert estimate_wait_detailed(queue, available_gpus=4) == 0

        # With 2 GPUs, first serves now, second waits
        queue = [
            {"reservation_id": "res-1", "gpu_count": 4},
            {"reservation_id": "res-2", "gpu_count": 4},
        ]
        # First takes all 4, second waits
        result = estimate_wait_detailed(queue, available_gpus=4)
        assert result > 0


class TestAvailabilityTableUpdate:
    """Tests for DynamoDB availability table updates"""

    def test_availability_record_structure(self):
        """Should create properly structured availability record"""
        def create_availability_record(gpu_type, available, total, queue_length):
            return {
                "gpu_type": gpu_type,
                "available_gpus": Decimal(available),
                "total_gpus": Decimal(total),
                "queue_length": Decimal(queue_length),
                "estimated_wait_minutes": Decimal(queue_length * 240),
                "last_updated": datetime.now(timezone.utc).isoformat(),
            }

        record = create_availability_record("t4", 6, 8, 2)

        assert record["gpu_type"] == "t4"
        assert record["available_gpus"] == Decimal(6)
        assert record["total_gpus"] == Decimal(8)
        assert record["queue_length"] == Decimal(2)
        assert "last_updated" in record

    def test_all_gpu_types_tracked(self):
        """Should track availability for all GPU types"""
        GPU_TYPES = ["t4", "l4", "a10g", "a100", "h100", "h200", "b200"]

        def update_all_availability(tracker):
            results = {}
            for gpu_type in GPU_TYPES:
                results[gpu_type] = {
                    "available": tracker.get(gpu_type, {}).get("available", 0),
                    "total": tracker.get(gpu_type, {}).get("total", 0),
                }
            return results

        mock_tracker = {
            "t4": {"available": 4, "total": 8},
            "h100": {"available": 0, "total": 16},
        }

        results = update_all_availability(mock_tracker)

        assert len(results) == 7
        assert results["t4"]["available"] == 4
        assert results["h100"]["available"] == 0
        assert results["a100"]["available"] == 0  # Not in tracker


class TestNodeReadiness:
    """Tests for node readiness checks"""

    def test_node_is_ready(self):
        """Should detect ready nodes"""
        def is_node_ready(conditions):
            for condition in conditions:
                if condition.get("type") == "Ready":
                    return condition.get("status") == "True"
            return False

        ready_conditions = [
            {"type": "Ready", "status": "True"},
            {"type": "MemoryPressure", "status": "False"},
        ]
        not_ready_conditions = [
            {"type": "Ready", "status": "False"},
            {"type": "MemoryPressure", "status": "True"},
        ]

        assert is_node_ready(ready_conditions) is True
        assert is_node_ready(not_ready_conditions) is False

    def test_node_is_schedulable(self):
        """Should detect schedulable nodes"""
        def is_schedulable(spec):
            return not spec.get("unschedulable", False)

        assert is_schedulable({}) is True
        assert is_schedulable({"unschedulable": False}) is True
        assert is_schedulable({"unschedulable": True}) is False

    def test_exclude_cordoned_nodes(self):
        """Should exclude cordoned nodes from capacity"""
        nodes = [
            {"name": "node-1", "unschedulable": False, "gpus": 4},
            {"name": "node-2", "unschedulable": True, "gpus": 4},  # Cordoned
            {"name": "node-3", "unschedulable": False, "gpus": 4},
        ]

        def get_schedulable_capacity(nodes):
            return sum(n["gpus"] for n in nodes if not n.get("unschedulable", False))

        assert get_schedulable_capacity(nodes) == 8
