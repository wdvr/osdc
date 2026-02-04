"""
Shared pytest fixtures for service unit tests.
"""
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ============================================================================
# Mock PostgreSQL Connection Fixtures
# ============================================================================

@pytest.fixture
def mock_asyncpg_pool():
    """Mock asyncpg connection pool for API tests."""
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.acquire.return_value.__aexit__.return_value = None
    return pool, conn


@pytest.fixture
def mock_db_cursor():
    """Mock psycopg2 cursor with RealDictCursor behavior."""
    cursor = MagicMock()
    cursor.fetchone.return_value = None
    cursor.fetchall.return_value = []
    cursor.__enter__ = MagicMock(return_value=cursor)
    cursor.__exit__ = MagicMock(return_value=None)
    return cursor


@pytest.fixture
def mock_db_connection(mock_db_cursor):
    """Mock psycopg2 connection."""
    conn = MagicMock()
    conn.cursor.return_value = mock_db_cursor
    conn.__enter__ = MagicMock(return_value=conn)
    conn.__exit__ = MagicMock(return_value=None)
    return conn


@pytest.fixture
def mock_connection_pool(mock_db_connection):
    """Mock psycopg2 ThreadedConnectionPool."""
    pool = MagicMock()
    pool.getconn.return_value = mock_db_connection
    pool.putconn.return_value = None
    pool.minconn = 1
    pool.maxconn = 10
    pool.closed = False
    return pool


# ============================================================================
# Mock Kubernetes Client Fixtures
# ============================================================================

@pytest.fixture
def mock_k8s_batch_api():
    """Mock Kubernetes BatchV1Api."""
    api = MagicMock()
    api.create_namespaced_job.return_value = None
    api.delete_namespaced_job.return_value = None
    api.read_namespaced_job_status.return_value = MagicMock(
        status=MagicMock(
            active=0,
            succeeded=1,
            failed=0,
            start_time=datetime.now(UTC),
            completion_time=datetime.now(UTC)
        )
    )
    api.list_namespaced_job.return_value = MagicMock(items=[])
    return api


@pytest.fixture
def mock_k8s_core_api():
    """Mock Kubernetes CoreV1Api."""
    api = MagicMock()
    api.list_namespaced_pod.return_value = MagicMock(items=[])
    api.read_namespaced_pod_log.return_value = "pod logs here"
    api.create_namespaced_pod.return_value = None
    api.delete_namespaced_pod.return_value = None
    api.list_node.return_value = MagicMock(items=[])
    return api


# ============================================================================
# Mock AWS Client Fixtures
# ============================================================================

@pytest.fixture
def mock_ec2_client():
    """Mock boto3 EC2 client."""
    client = MagicMock()
    # describe_volumes mock
    client.describe_volumes.return_value = {"Volumes": []}
    # describe_snapshots mock
    client.describe_snapshots.return_value = {"Snapshots": []}
    # create_volume mock
    client.create_volume.return_value = {"VolumeId": "vol-12345678"}
    # delete_volume mock
    client.delete_volume.return_value = {}
    # create_snapshot mock
    client.create_snapshot.return_value = {"SnapshotId": "snap-12345678"}
    # create_tags mock
    client.create_tags.return_value = {}
    # delete_tags mock
    client.delete_tags.return_value = {}
    # Paginator mock
    paginator = MagicMock()
    paginator.paginate.return_value = [{"Volumes": []}]
    client.get_paginator.return_value = paginator
    return client


@pytest.fixture
def mock_sts_client():
    """Mock boto3 STS client."""
    client = MagicMock()
    client.get_caller_identity.return_value = {
        "Account": "123456789012",
        "UserId": "AIDAEXAMPLE",
        "Arn": "arn:aws:sts::123456789012:assumed-role/SSOCloudDevGpuReservation/testuser"
    }
    return client


# ============================================================================
# Test Data Factory Fixtures
# ============================================================================

@pytest.fixture
def sample_reservation():
    """Factory for sample reservation data."""
    def _create(
        reservation_id: str | None = None,
        user_id: str = "testuser",
        status: str = "active",
        gpu_type: str = "a100",
        gpu_count: int = 4,
        duration_hours: int = 4,
        **kwargs
    ) -> dict[str, Any]:
        return {
            "reservation_id": reservation_id or str(uuid.uuid4()),
            "user_id": user_id,
            "status": status,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "instance_type": "p4d.24xlarge",
            "duration_hours": duration_hours,
            "created_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=duration_hours),
            "name": kwargs.get("name", "test-pod"),
            "pod_name": kwargs.get("pod_name", "gpu-dev-test"),
            "node_ip": kwargs.get("node_ip", "10.0.0.1"),
            "node_port": kwargs.get("node_port", 30001),
            "jupyter_enabled": kwargs.get("jupyter_enabled", False),
            "jupyter_url": kwargs.get("jupyter_url"),
            "jupyter_token": kwargs.get("jupyter_token"),
            "github_user": kwargs.get("github_user", "testghuser"),
            **kwargs
        }
    return _create


@pytest.fixture
def sample_disk():
    """Factory for sample disk data."""
    def _create(
        disk_name: str = "test-disk",
        user_id: str = "testuser",
        size_gb: int = 100,
        **kwargs
    ) -> dict[str, Any]:
        return {
            "disk_id": kwargs.get("disk_id", 1),
            "disk_name": disk_name,
            "user_id": user_id,
            "ebs_volume_id": kwargs.get("ebs_volume_id", "vol-12345678"),
            "size_gb": size_gb,
            "created_at": kwargs.get("created_at", datetime.now(UTC)),
            "last_used": kwargs.get("last_used"),
            "in_use": kwargs.get("in_use", False),
            "reservation_id": kwargs.get("reservation_id"),
            "is_backing_up": kwargs.get("is_backing_up", False),
            "is_deleted": kwargs.get("is_deleted", False),
            "snapshot_count": kwargs.get("snapshot_count", 0),
            "last_snapshot_at": kwargs.get("last_snapshot_at"),
            **kwargs
        }
    return _create


@pytest.fixture
def sample_aws_volume():
    """Factory for sample AWS EBS volume data."""
    def _create(
        volume_id: str = "vol-12345678",
        user_id: str = "testuser",
        disk_name: str = "test-disk",
        size_gb: int = 100,
        **kwargs
    ) -> dict[str, Any]:
        return {
            "VolumeId": volume_id,
            "Size": size_gb,
            "State": kwargs.get("state", "available"),
            "AvailabilityZone": kwargs.get("availability_zone", "us-east-1a"),
            "CreateTime": kwargs.get("created_at", datetime.now(UTC)),
            "Attachments": kwargs.get("attachments", []),
            "Tags": [
                {"Key": "gpu-dev-user", "Value": user_id},
                {"Key": "disk-name", "Value": disk_name},
                *kwargs.get("extra_tags", [])
            ]
        }
    return _create


@pytest.fixture
def sample_pgmq_message():
    """Factory for sample PGMQ message data."""
    def _create(
        msg_id: int = 1,
        action: str = "create_reservation",
        user_id: str = "testuser",
        **kwargs
    ) -> dict[str, Any]:
        message_body = {
            "action": action,
            "user_id": user_id,
            "reservation_id": kwargs.get("reservation_id", str(uuid.uuid4())),
            "gpu_type": kwargs.get("gpu_type", "a100"),
            "gpu_count": kwargs.get("gpu_count", 4),
            "github_user": kwargs.get("github_user", "testghuser"),
            "duration_hours": kwargs.get("duration_hours", 4),
            "version": kwargs.get("version", "0.4.0"),
            "_metadata": {
                "retry_count": kwargs.get("retry_count", 0),
                "max_retries": kwargs.get("max_retries", 3),
                "created_at": datetime.now(UTC).isoformat()
            },
            **{k: v for k, v in kwargs.items() if k not in [
                "reservation_id", "gpu_type", "gpu_count", "github_user",
                "duration_hours", "version", "retry_count", "max_retries"
            ]}
        }
        return {
            "msg_id": msg_id,
            "read_ct": kwargs.get("read_ct", 1),
            "enqueued_at": datetime.now(UTC),
            "vt": datetime.now(UTC) + timedelta(seconds=300),
            "message": message_body
        }
    return _create


@pytest.fixture
def sample_user_info():
    """Factory for sample authenticated user info."""
    def _create(
        user_id: int = 1,
        username: str = "testuser",
        email: str = "testuser@example.com",
        **kwargs
    ) -> dict[str, Any]:
        return {
            "user_id": user_id,
            "username": username,
            "email": email,
            **kwargs
        }
    return _create


# ============================================================================
# API Test Client Fixtures
# ============================================================================

@pytest.fixture
def mock_verify_api_key(sample_user_info):
    """Mock API key verification."""
    async def _verify(*args, **kwargs):
        return sample_user_info()
    return _verify


# ============================================================================
# Environment Variable Fixtures
# ============================================================================

@pytest.fixture
def mock_env_vars():
    """Mock environment variables for services."""
    env = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5432",
        "POSTGRES_USER": "testuser",
        "POSTGRES_PASSWORD": "testpass",
        "POSTGRES_DB": "testdb",
        "QUEUE_NAME": "test_queue",
        "DISK_QUEUE_NAME": "test_disk_queue",
        "KUBE_NAMESPACE": "test-namespace",
        "WORKER_IMAGE": "test-image:latest",
        "SERVICE_ACCOUNT": "test-sa",
        "REGION": "us-east-1",
        "EKS_CLUSTER_NAME": "test-cluster",
        "PRIMARY_AVAILABILITY_ZONE": "us-east-1a",
        "MAX_RESERVATION_HOURS": "48",
        "DEFAULT_TIMEOUT_HOURS": "4",
        "API_KEY_TTL_HOURS": "2",
        "ALLOWED_AWS_ROLE": "SSOCloudDevGpuReservation"
    }
    with patch.dict("os.environ", env, clear=False):
        yield env
