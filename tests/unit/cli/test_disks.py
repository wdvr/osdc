"""
Unit tests for gpu_dev_cli.disks module

Tests:
- Disk listing
- Disk creation
- Disk deletion
- Disk renaming
- In-use status checking
"""

import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws
import boto3


class TestDiskInUseStatus:
    """Tests for get_disk_in_use_status function"""

    @mock_aws
    def test_disk_not_in_use_returns_false(self, aws_credentials):
        """Should return (False, None) when disk is not in use"""
        # Setup
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add disk that's not in use
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "mydata",
            "in_use": False,
        })

        # Create mock config
        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"

        from gpu_dev_cli.disks import get_disk_in_use_status
        is_in_use, res_id = get_disk_in_use_status("mydata", "test-user", mock_config)

        assert is_in_use is False
        assert res_id is None

    @mock_aws
    def test_disk_in_use_via_disks_table(self, aws_credentials):
        """Should detect disk in use from disks table in_use field"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add disk that's in use
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "mydata",
            "in_use": True,
            "attached_to_reservation": "res-123",
        })

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"

        from gpu_dev_cli.disks import get_disk_in_use_status
        is_in_use, res_id = get_disk_in_use_status("mydata", "test-user", mock_config)

        assert is_in_use is True
        assert res_id == "res-123"

    @mock_aws
    def test_disk_in_use_via_active_reservation(self, aws_credentials):
        """Should detect disk in use from active reservation"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add disk not marked in_use in disks table
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "mydata",
            "in_use": False,
        })

        # But add active reservation using that disk
        reservations_table.put_item(Item={
            "reservation_id": "res-456",
            "user_id": "test-user",
            "disk_name": "mydata",
            "status": "active",
        })

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"

        from gpu_dev_cli.disks import get_disk_in_use_status
        is_in_use, res_id = get_disk_in_use_status("mydata", "test-user", mock_config)

        assert is_in_use is True
        assert res_id == "res-456"


class TestListDisks:
    """Tests for list_disks function"""

    @mock_aws
    def test_list_disks_returns_all_user_disks(self, aws_credentials):
        """Should return all disks for user sorted by last_used"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        now = datetime.now(timezone.utc)

        # Add disks
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "data1",
            "size_gb": 100,
            "created_at": (now - timedelta(days=5)).isoformat(),
            "last_used": (now - timedelta(days=2)).isoformat(),
            "snapshot_count": 3,
            "in_use": False,
        })
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "data2",
            "size_gb": 200,
            "created_at": (now - timedelta(days=10)).isoformat(),
            "last_used": (now - timedelta(hours=1)).isoformat(),  # Most recent
            "snapshot_count": 1,
            "in_use": False,
        })

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"

        from gpu_dev_cli.disks import list_disks
        disks = list_disks("test-user", mock_config)

        assert len(disks) == 2
        # Should be sorted by last_used descending
        assert disks[0]["name"] == "data2"  # Most recent
        assert disks[1]["name"] == "data1"

    @mock_aws
    def test_list_disks_empty_for_new_user(self, aws_credentials):
        """Should return empty list for user with no disks"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"

        from gpu_dev_cli.disks import list_disks
        disks = list_disks("new-user", mock_config)

        assert disks == []


class TestCreateDisk:
    """Tests for create_disk function"""

    @mock_aws
    def test_create_disk_sends_sqs_message(self, aws_credentials):
        """Should send create_disk action to SQS queue"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")
        sqs = boto3.client("sqs", region_name="us-west-1")

        # Create queue
        queue = sqs.create_queue(QueueName="pytorch-gpu-dev-test-reservation-queue")
        queue_url = queue["QueueUrl"]

        # Create disks table
        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.session.client.return_value = sqs
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"
        mock_config.get_queue_url.return_value = queue_url

        from gpu_dev_cli.disks import create_disk
        operation_id = create_disk("newdisk", "test-user", mock_config)

        # Should return operation_id
        assert operation_id is not None

        # Check SQS message
        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1)
        assert "Messages" in messages
        body = json.loads(messages["Messages"][0]["Body"])
        assert body["action"] == "create_disk"
        assert body["disk_name"] == "newdisk"
        assert body["user_id"] == "test-user"

    @mock_aws
    def test_create_disk_rejects_existing_name(self, aws_credentials):
        """Should reject creating disk with existing name"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add existing disk
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "existingdisk",
            "size_gb": 100,
        })

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"

        from gpu_dev_cli.disks import create_disk
        result = create_disk("existingdisk", "test-user", mock_config)

        assert result is None

    def test_create_disk_validates_name_format(self, aws_credentials):
        """Should reject invalid disk names"""
        mock_config = MagicMock()
        mock_config.session.resource.return_value.Table.return_value.query.return_value = {"Items": []}

        from gpu_dev_cli.disks import create_disk

        # Invalid names
        result = create_disk("disk with spaces", "test-user", mock_config)
        assert result is None

        result = create_disk("disk@special#chars", "test-user", mock_config)
        assert result is None


class TestDeleteDisk:
    """Tests for delete_disk function"""

    @mock_aws
    def test_delete_disk_sends_sqs_message(self, aws_credentials):
        """Should send delete_disk action to SQS queue"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")
        sqs = boto3.client("sqs", region_name="us-west-1")

        queue = sqs.create_queue(QueueName="pytorch-gpu-dev-test-reservation-queue")
        queue_url = queue["QueueUrl"]

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add disk to delete
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "deleteme",
            "size_gb": 100,
            "in_use": False,
        })

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.session.client.return_value = sqs
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"
        mock_config.get_queue_url.return_value = queue_url

        from gpu_dev_cli.disks import delete_disk
        operation_id = delete_disk("deleteme", "test-user", mock_config)

        assert operation_id is not None

        # Check SQS message
        messages = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=1)
        body = json.loads(messages["Messages"][0]["Body"])
        assert body["action"] == "delete_disk"
        assert body["disk_name"] == "deleteme"

    @mock_aws
    def test_delete_disk_rejects_in_use_disk(self, aws_credentials):
        """Should not allow deleting disk that's in use"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add disk that's in use
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "inuse",
            "size_gb": 100,
            "in_use": True,
            "attached_to_reservation": "res-123",
        })

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"

        from gpu_dev_cli.disks import delete_disk
        result = delete_disk("inuse", "test-user", mock_config)

        assert result is None


class TestRenameDisk:
    """Tests for rename_disk function"""

    @mock_aws
    def test_rename_disk_updates_snapshot_tags(self, aws_credentials):
        """Should update disk_name tag on all snapshots"""
        ec2 = boto3.client("ec2", region_name="us-west-1")
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create tables
        reservations_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-reservations",
            KeySchema=[{"AttributeName": "reservation_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "reservation_id", "AttributeType": "S"},
                {"AttributeName": "user_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "UserIndex",
                "KeySchema": [
                    {"AttributeName": "user_id", "KeyType": "HASH"},
                    {"AttributeName": "reservation_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )
        reservations_table.wait_until_exists()

        disks_table = dynamodb.create_table(
            TableName="pytorch-gpu-dev-test-disks",
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

        # Add disk
        disks_table.put_item(Item={
            "user_id": "test-user",
            "disk_name": "oldname",
            "size_gb": 100,
            "in_use": False,
        })

        # Create a volume and snapshot (moto doesn't have full snapshot support)
        # So we'll mock the EC2 client instead
        mock_ec2 = MagicMock()
        mock_ec2.describe_snapshots.return_value = {
            "Snapshots": [
                {"SnapshotId": "snap-123"},
                {"SnapshotId": "snap-456"},
            ]
        }
        mock_ec2.create_tags.return_value = {}

        mock_config = MagicMock()
        mock_config.session.resource.return_value = dynamodb
        mock_config.session.client.return_value = mock_ec2
        mock_config.aws_region = "us-west-1"
        mock_config.disks_table = "pytorch-gpu-dev-test-disks"
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"
        mock_config.queue_name = "pytorch-gpu-dev-test-reservation-queue"

        from gpu_dev_cli.disks import rename_disk
        result = rename_disk("oldname", "newname", "test-user", mock_config)

        assert result is True
        # Verify create_tags was called for both snapshots
        assert mock_ec2.create_tags.call_count == 2

    def test_rename_disk_validates_new_name(self, aws_credentials):
        """Should reject invalid new disk names"""
        mock_config = MagicMock()
        mock_config.session.resource.return_value.Table.return_value.query.return_value = {"Items": []}

        from gpu_dev_cli.disks import rename_disk

        result = rename_disk("oldname", "invalid name!", "test-user", mock_config)
        assert result is False
