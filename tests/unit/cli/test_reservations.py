"""
Unit tests for gpu_dev_cli.reservations module

Tests:
- Reservation creation (SQS messaging)
- Reservation listing
- Reservation cancellation
- Connection info retrieval
- SSH config generation
- VS Code link generation
"""

import json
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws
import boto3


class TestVSCodeLinkGeneration:
    """Tests for VS Code and Cursor link generation"""

    def test_make_vscode_link(self):
        """Should generate correct vscode:// URL"""
        from gpu_dev_cli.reservations import _make_vscode_link

        result = _make_vscode_link("gpu-dev-abc123")

        assert result == "vscode://vscode-remote/ssh-remote+gpu-dev-abc123/home/dev"

    def test_make_cursor_link(self):
        """Should generate correct cursor:// URL"""
        from gpu_dev_cli.reservations import _make_cursor_link

        result = _make_cursor_link("gpu-dev-abc123")

        assert result == "cursor://vscode-remote/ssh-remote+gpu-dev-abc123/home/dev"


class TestAgentForwardingSSH:
    """Tests for SSH command modifications"""

    def test_add_agent_forwarding_to_ssh(self):
        """Should add -A flag to SSH command"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        result = _add_agent_forwarding_to_ssh("ssh -p 30001 dev@1.2.3.4")

        assert "-A" in result
        assert "ssh" in result

    def test_add_agent_forwarding_preserves_existing_options(self):
        """Should preserve existing SSH options"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        result = _add_agent_forwarding_to_ssh("ssh -o StrictHostKeyChecking=no -p 30001 dev@1.2.3.4")

        assert "-A" in result
        assert "StrictHostKeyChecking=no" in result


class TestReservationManager:
    """Tests for ReservationManager class"""

    @mock_aws
    def test_list_reservations_returns_user_reservations(self, aws_credentials):
        """Should return all reservations for a user"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create table
        table = dynamodb.create_table(
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
        table.wait_until_exists()

        now = datetime.now(timezone.utc)

        # Add reservations
        table.put_item(Item={
            "reservation_id": "res-001",
            "user_id": "test-user",
            "status": "active",
            "gpu_count": 2,
            "gpu_type": "t4",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=4)).isoformat(),
        })
        table.put_item(Item={
            "reservation_id": "res-002",
            "user_id": "test-user",
            "status": "completed",
            "gpu_count": 1,
            "gpu_type": "h100",
            "created_at": (now - timedelta(days=1)).isoformat(),
            "expires_at": (now - timedelta(hours=20)).isoformat(),
        })
        # Different user's reservation
        table.put_item(Item={
            "reservation_id": "res-003",
            "user_id": "other-user",
            "status": "active",
            "gpu_count": 4,
            "gpu_type": "a100",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=8)).isoformat(),
        })

        mock_config = MagicMock()
        mock_config.dynamodb = dynamodb
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)
        reservations = manager.list_reservations("test-user")

        assert len(reservations) == 2
        assert all(r["user_id"] == "test-user" for r in reservations)

    @mock_aws
    def test_list_reservations_filters_by_status_in_client(self, aws_credentials):
        """Should be able to filter reservations by status after fetching"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        table = dynamodb.create_table(
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
        table.wait_until_exists()

        now = datetime.now(timezone.utc)

        table.put_item(Item={
            "reservation_id": "res-001",
            "user_id": "test-user",
            "status": "active",
            "created_at": now.isoformat(),
        })
        table.put_item(Item={
            "reservation_id": "res-002",
            "user_id": "test-user",
            "status": "cancelled",
            "created_at": now.isoformat(),
        })

        mock_config = MagicMock()
        mock_config.dynamodb = dynamodb
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)
        all_reservations = manager.list_reservations("test-user")

        # Filter on client side (as CLI does)
        active_reservations = [r for r in all_reservations if r["status"] == "active"]

        assert len(active_reservations) == 1
        assert active_reservations[0]["status"] == "active"


class TestReservationCreation:
    """Tests for reservation creation via SQS"""

    def test_create_reservation_returns_id(self):
        """Should return a reservation ID when creating a reservation"""
        # Create mocks
        mock_table = MagicMock()
        mock_sqs = MagicMock()
        mock_config = MagicMock()
        mock_config.dynamodb.Table.return_value = mock_table
        mock_config.sqs_client = mock_sqs
        mock_config.get_queue_url.return_value = "https://sqs.test/queue"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)

        reservation_id = manager.create_reservation(
            user_id="test-user",
            gpu_count=2,
            gpu_type="t4",
            duration_hours=4,
            github_user="testgithub",
        )

        # Should return a UUID string
        assert reservation_id is not None
        assert len(reservation_id) == 36  # UUID format

    def test_create_reservation_sends_sqs_message(self):
        """Should send reservation request to SQS"""
        mock_table = MagicMock()
        mock_sqs = MagicMock()
        mock_config = MagicMock()
        mock_config.dynamodb.Table.return_value = mock_table
        mock_config.sqs_client = mock_sqs
        mock_config.get_queue_url.return_value = "https://sqs.test/queue"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)

        manager.create_reservation(
            user_id="test-user",
            gpu_count=2,
            gpu_type="t4",
            duration_hours=4,
            github_user="testgithub",
        )

        # Verify SQS send_message was called
        mock_sqs.send_message.assert_called_once()
        call_args = mock_sqs.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        # Create reservation uses status=pending instead of action field
        assert body["status"] == "pending"
        assert body["gpu_count"] == 2
        assert body["gpu_type"] == "t4"
        assert body["github_user"] == "testgithub"

    def test_create_reservation_includes_disk_name(self):
        """Should include disk_name in SQS message when specified"""
        mock_table = MagicMock()
        mock_sqs = MagicMock()
        mock_config = MagicMock()
        mock_config.dynamodb.Table.return_value = mock_table
        mock_config.sqs_client = mock_sqs
        mock_config.get_queue_url.return_value = "https://sqs.test/queue"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)

        manager.create_reservation(
            user_id="test-user",
            gpu_count=1,
            gpu_type="t4",
            duration_hours=2,
            github_user="testgithub",
            disk_name="mydata",
        )

        call_args = mock_sqs.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        assert body.get("disk_name") == "mydata"


class TestCancellation:
    """Tests for reservation cancellation"""

    def test_cancel_sends_sqs_message(self):
        """Should send cancel request to SQS"""
        mock_table = MagicMock()
        mock_sqs = MagicMock()
        mock_config = MagicMock()
        mock_config.dynamodb.Table.return_value = mock_table
        mock_config.sqs_client = mock_sqs
        mock_config.get_queue_url.return_value = "https://sqs.test/queue"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)

        manager.cancel_reservation("res-to-cancel", "test-user")

        # Verify SQS send_message was called
        mock_sqs.send_message.assert_called_once()
        call_args = mock_sqs.send_message.call_args
        body = json.loads(call_args.kwargs["MessageBody"])

        # Cancellation uses "type": "cancellation" field
        assert body["type"] == "cancellation"
        assert body["reservation_id"] == "res-to-cancel"


class TestConnectionInfo:
    """Tests for connection info retrieval"""

    @mock_aws
    def test_get_connection_info_returns_ssh_command(self, aws_credentials):
        """Should return SSH command for active reservation"""
        dynamodb = boto3.resource("dynamodb", region_name="us-west-1")

        # Create table with UserIndex
        table = dynamodb.create_table(
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
        table.wait_until_exists()

        # Add all required fields that get_connection_info expects
        table.put_item(Item={
            "reservation_id": "res-123",
            "user_id": "test-user",
            "status": "active",
            "pod_name": "gpu-dev-abc123",
            "node_port": 30001,
            "ssh_command": "ssh -p 30001 dev@1.2.3.4",
            "gpu_count": 2,
            "gpu_type": "t4",
            "duration_hours": 4,
        })

        mock_config = MagicMock()
        mock_config.dynamodb = dynamodb
        mock_config.reservations_table = "pytorch-gpu-dev-test-reservations"

        from gpu_dev_cli.reservations import ReservationManager
        manager = ReservationManager(mock_config)

        info = manager.get_connection_info("res-123", "test-user")

        assert info is not None
        assert "ssh_command" in info
        assert "dev@" in info["ssh_command"]


class TestSSHConfigGeneration:
    """Tests for SSH config file generation"""

    def test_generate_ssh_config_function(self):
        """Should generate valid SSH config content"""
        from gpu_dev_cli.reservations import _generate_ssh_config

        config = _generate_ssh_config("test.devservers.io", "gpu-dev-abc123")

        assert "Host gpu-dev-abc123" in config
        assert "HostName test.devservers.io" in config
        assert "User dev" in config
        assert "ForwardAgent yes" in config
