"""
Shared pytest fixtures for ODC test suite

Provides:
- AWS mocking (DynamoDB, SQS, EC2, S3)
- Test data factories
- Configuration fixtures
"""

import json
import os
import sys
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, Any, Optional
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

# Add CLI and Lambda source to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "cli-tools", "gpu-dev-cli"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "terraform-gpu-devservers", "lambda", "reservation_processor"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "terraform-gpu-devservers", "lambda", "shared"))


# Test configuration
TEST_AWS_REGION = "us-west-1"
TEST_PREFIX = "pytorch-gpu-dev-test"


@pytest.fixture(scope="session")
def aws_credentials():
    """Mock AWS credentials for moto"""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = TEST_AWS_REGION


@pytest.fixture
def mock_aws_env(aws_credentials):
    """Set up mock AWS environment variables"""
    env_vars = {
        "AWS_DEFAULT_REGION": TEST_AWS_REGION,
        "AWS_REGION": TEST_AWS_REGION,
        "RESERVATIONS_TABLE": f"{TEST_PREFIX}-reservations",
        "EKS_CLUSTER_NAME": f"{TEST_PREFIX}-cluster",
        "REGION": TEST_AWS_REGION,
        "MAX_RESERVATION_HOURS": "48",
        "DEFAULT_TIMEOUT_HOURS": "8",
        "QUEUE_URL": f"https://sqs.{TEST_AWS_REGION}.amazonaws.com/123456789012/{TEST_PREFIX}-reservation-queue",
        "PRIMARY_AVAILABILITY_ZONE": f"{TEST_AWS_REGION}a",
        "GPU_DEV_CONTAINER_IMAGE": "pytorch/pytorch:2.8.0-cuda12.9-cudnn9-devel",
        "LAMBDA_VERSION": "0.3.5",
        "MIN_CLI_VERSION": "0.3.0",
    }
    with patch.dict(os.environ, env_vars):
        yield env_vars


@pytest.fixture
def dynamodb_mock(aws_credentials):
    """Create mock DynamoDB tables"""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=TEST_AWS_REGION)

        # Create reservations table
        reservations_table = dynamodb.create_table(
            TableName=f"{TEST_PREFIX}-reservations",
            KeySchema=[
                {"AttributeName": "reservation_id", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "status", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "UserIndex",
                    "KeySchema": [
                        {"AttributeName": "user_id", "KeyType": "HASH"},
                        {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "StatusIndex",
                    "KeySchema": [
                        {"AttributeName": "status", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        # Create disks table
        disks_table = dynamodb.create_table(
            TableName=f"{TEST_PREFIX}-disks",
            KeySchema=[
                {"AttributeName": "user_id", "KeyType": "HASH"},
                {"AttributeName": "disk_name", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "user_id", "AttributeType": "S"},
                {"AttributeName": "disk_name", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        disks_table.wait_until_exists()

        # Create availability table
        availability_table = dynamodb.create_table(
            TableName=f"{TEST_PREFIX}-gpu-availability",
            KeySchema=[
                {"AttributeName": "gpu_type", "KeyType": "HASH"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "gpu_type", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        availability_table.wait_until_exists()

        yield dynamodb


@pytest.fixture
def sqs_mock(aws_credentials):
    """Create mock SQS queue"""
    with mock_aws():
        sqs = boto3.client("sqs", region_name=TEST_AWS_REGION)
        queue = sqs.create_queue(QueueName=f"{TEST_PREFIX}-reservation-queue")
        yield sqs, queue["QueueUrl"]


@pytest.fixture
def ec2_mock(aws_credentials):
    """Create mock EC2 with test instances"""
    with mock_aws():
        ec2 = boto3.client("ec2", region_name=TEST_AWS_REGION)
        yield ec2


@pytest.fixture
def s3_mock(aws_credentials):
    """Create mock S3 bucket"""
    with mock_aws():
        s3 = boto3.client("s3", region_name=TEST_AWS_REGION)
        s3.create_bucket(
            Bucket=f"{TEST_PREFIX}-snapshots",
            CreateBucketConfiguration={"LocationConstraint": TEST_AWS_REGION},
        )
        yield s3


# Data factories

class ReservationFactory:
    """Factory for creating test reservation data"""

    @staticmethod
    def create(
        reservation_id: Optional[str] = None,
        user_id: str = "test-user",
        status: str = "active",
        gpu_count: int = 1,
        gpu_type: str = "t4",
        duration_hours: int = 4,
        pod_name: Optional[str] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a reservation dict with defaults"""
        rid = reservation_id or f"res-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": rid,
            "user_id": user_id,
            "status": status,
            "gpu_count": Decimal(gpu_count),
            "gpu_type": gpu_type,
            "duration_hours": Decimal(duration_hours),
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=duration_hours)).isoformat(),
            "pod_name": pod_name or f"gpu-dev-{rid[:8]}",
            "namespace": "gpu-dev",
            "node_port": Decimal(30000 + hash(rid) % 2767),
        }
        reservation.update(kwargs)
        return reservation


class DiskFactory:
    """Factory for creating test disk data"""

    @staticmethod
    def create(
        user_id: str = "test-user",
        disk_name: str = "default",
        size_gb: int = 100,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a disk dict with defaults"""
        now = datetime.now(timezone.utc)

        disk = {
            "user_id": user_id,
            "disk_name": disk_name,
            "size_gb": Decimal(size_gb),
            "created_at": now.isoformat(),
            "last_used": now.isoformat(),
            "snapshot_count": Decimal(0),
            "in_use": False,
            "is_deleted": False,
        }
        disk.update(kwargs)
        return disk


class GPUAvailabilityFactory:
    """Factory for creating GPU availability data"""

    @staticmethod
    def create(
        gpu_type: str = "t4",
        available_gpus: int = 4,
        total_gpus: int = 8,
        queue_length: int = 0,
        **kwargs
    ) -> Dict[str, Any]:
        """Create GPU availability dict"""
        return {
            "gpu_type": gpu_type,
            "available_gpus": Decimal(available_gpus),
            "total_gpus": Decimal(total_gpus),
            "queue_length": Decimal(queue_length),
            "estimated_wait_minutes": Decimal(0),
            "last_updated": datetime.now(timezone.utc).isoformat(),
            **kwargs
        }


@pytest.fixture
def reservation_factory():
    """Fixture for creating test reservations"""
    return ReservationFactory()


@pytest.fixture
def disk_factory():
    """Fixture for creating test disks"""
    return DiskFactory()


@pytest.fixture
def availability_factory():
    """Fixture for creating GPU availability data"""
    return GPUAvailabilityFactory()


# Mock Kubernetes client

@pytest.fixture
def mock_k8s_client():
    """Mock Kubernetes client for unit tests"""
    mock_client = MagicMock()

    # Mock CoreV1Api
    mock_v1 = MagicMock()
    mock_client.CoreV1Api.return_value = mock_v1

    # Mock pod operations
    mock_v1.list_namespaced_pod.return_value = MagicMock(items=[])
    mock_v1.create_namespaced_pod.return_value = MagicMock()
    mock_v1.delete_namespaced_pod.return_value = MagicMock()
    mock_v1.read_namespaced_pod.return_value = MagicMock(
        status=MagicMock(phase="Running"),
        spec=MagicMock(node_name="test-node"),
    )

    # Mock node operations
    mock_node = MagicMock()
    mock_node.metadata.name = "test-gpu-node"
    mock_node.metadata.labels = {"GpuType": "t4"}
    mock_node.status.allocatable = {"nvidia.com/gpu": "4", "cpu": "48", "memory": "192Gi"}
    mock_node.status.addresses = [MagicMock(type="ExternalIP", address="1.2.3.4")]
    mock_v1.list_node.return_value = MagicMock(items=[mock_node])

    # Mock service operations
    mock_v1.create_namespaced_service.return_value = MagicMock()
    mock_v1.delete_namespaced_service.return_value = MagicMock()

    return mock_client


# CLI Config fixture

@pytest.fixture
def mock_cli_config(tmp_path, dynamodb_mock, sqs_mock):
    """Create mock CLI Config object"""
    config_dir = tmp_path / ".config" / "gpu-dev"
    config_dir.mkdir(parents=True)
    config_file = config_dir / "config.json"
    config_file.write_text(json.dumps({
        "github_user": "testuser",
        "environment": "test",
        "region": TEST_AWS_REGION,
        "workspace": "default",
    }))

    # Patch Config class to use temp path
    with patch("gpu_dev_cli.config.Config.CONFIG_FILE", config_file):
        with patch("gpu_dev_cli.config.Config.LEGACY_CONFIG_FILE", tmp_path / ".gpu-dev-config"):
            with patch("gpu_dev_cli.config.Config.LEGACY_ENVIRONMENT_FILE", tmp_path / ".gpu-dev-environment.json"):
                from gpu_dev_cli.config import Config

                # Create config with mocked AWS
                config = Config()
                config.prefix = TEST_PREFIX
                config.queue_name = f"{TEST_PREFIX}-reservation-queue"
                config.reservations_table = f"{TEST_PREFIX}-reservations"
                config.disks_table = f"{TEST_PREFIX}-disks"
                config.availability_table = f"{TEST_PREFIX}-gpu-availability"
                config.aws_region = TEST_AWS_REGION

                yield config


# E2E specific fixtures

@pytest.fixture(scope="session")
def e2e_config():
    """
    Configuration for E2E tests against real dev cluster.
    Requires AWS credentials and kubectl configured.
    """
    # Skip if not running E2E tests
    if not os.environ.get("RUN_E2E_TESTS"):
        pytest.skip("E2E tests require RUN_E2E_TESTS=1 environment variable")

    return {
        "region": os.environ.get("E2E_AWS_REGION", "us-west-1"),
        "cluster_name": os.environ.get("E2E_CLUSTER_NAME", "pytorch-gpu-dev-cluster"),
        "namespace": "gpu-dev",
        "github_user": os.environ.get("E2E_GITHUB_USER", "testuser"),
    }


@pytest.fixture
def e2e_cleanup(e2e_config):
    """
    Fixture that tracks resources created during E2E tests for cleanup.
    Yields a tracker dict, cleans up after test.
    """
    created_resources = {
        "reservations": [],
        "disks": [],
    }

    yield created_resources

    # Cleanup after test
    if created_resources["reservations"]:
        from gpu_dev_cli.config import Config
        from gpu_dev_cli.reservations import ReservationManager

        config = Config()
        manager = ReservationManager(config)

        for res_id in created_resources["reservations"]:
            try:
                manager.cancel_reservation(res_id, force=True)
            except Exception as e:
                print(f"Warning: Failed to cleanup reservation {res_id}: {e}")
