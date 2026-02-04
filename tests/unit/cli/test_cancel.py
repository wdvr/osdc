"""
Unit tests for gpu_dev_cli cancel command

Tests:
- Single reservation cancellation
- Cancel all reservations
- Validation and permissions
"""

import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws
import boto3


class TestCancelSingle:
    """Tests for cancelling a single reservation"""

    def test_cancel_builds_correct_message(self):
        """Should build correct cancellation message"""
        from gpu_dev_cli.reservations import build_cancel_request

        request = build_cancel_request(
            reservation_id="res-123",
            user_id="test-user",
        )

        assert request["type"] == "cancellation"
        assert request["reservation_id"] == "res-123"
        assert request["user_id"] == "test-user"

    def test_cancel_validates_ownership(self):
        """Should only allow owner to cancel"""
        from gpu_dev_cli.reservations import validate_cancel_permission

        reservation = {
            "reservation_id": "res-123",
            "user_id": "owner-user",
            "status": "active",
        }

        # Owner can cancel
        validate_cancel_permission(reservation, "owner-user")

        # Non-owner cannot cancel
        with pytest.raises(PermissionError):
            validate_cancel_permission(reservation, "other-user")

    def test_cancel_validates_cancellable_status(self):
        """Should only cancel reservations in cancellable state"""
        from gpu_dev_cli.reservations import validate_cancel_permission

        # Already cancelled
        cancelled_reservation = {
            "reservation_id": "res-123",
            "user_id": "test-user",
            "status": "cancelled",
        }

        with pytest.raises(ValueError, match="already"):
            validate_cancel_permission(cancelled_reservation, "test-user")

        # Expired
        expired_reservation = {
            "reservation_id": "res-123",
            "user_id": "test-user",
            "status": "expired",
        }

        with pytest.raises(ValueError, match="expired"):
            validate_cancel_permission(expired_reservation, "test-user")


class TestCancelAll:
    """Tests for cancelling all user reservations"""

    def test_cancel_all_finds_active_reservations(self):
        """Should find all active/queued reservations for user"""
        from gpu_dev_cli.reservations import get_cancellable_reservations

        reservations = [
            {"reservation_id": "res-1", "user_id": "test-user", "status": "active"},
            {"reservation_id": "res-2", "user_id": "test-user", "status": "queued"},
            {"reservation_id": "res-3", "user_id": "test-user", "status": "cancelled"},
            {"reservation_id": "res-4", "user_id": "test-user", "status": "expired"},
            {"reservation_id": "res-5", "user_id": "other-user", "status": "active"},
        ]

        cancellable = get_cancellable_reservations(reservations, "test-user")

        assert len(cancellable) == 2
        assert all(r["status"] in ["active", "queued"] for r in cancellable)
        assert all(r["user_id"] == "test-user" for r in cancellable)

    def test_cancel_all_returns_empty_when_none_active(self):
        """Should return empty list when no cancellable reservations"""
        from gpu_dev_cli.reservations import get_cancellable_reservations

        reservations = [
            {"reservation_id": "res-1", "user_id": "test-user", "status": "cancelled"},
            {"reservation_id": "res-2", "user_id": "test-user", "status": "expired"},
        ]

        cancellable = get_cancellable_reservations(reservations, "test-user")

        assert len(cancellable) == 0


class TestCancelConfirmation:
    """Tests for cancel confirmation handling"""

    def test_force_flag_skips_confirmation(self):
        """Should skip confirmation when --force is used"""
        from gpu_dev_cli.reservations import should_prompt_confirmation

        assert should_prompt_confirmation(force=True) is False
        assert should_prompt_confirmation(force=False) is True

    def test_cancel_message_includes_reservation_details(self):
        """Should include reservation details in confirmation message"""
        from gpu_dev_cli.reservations import format_cancel_confirmation

        reservation = {
            "reservation_id": "abc12345-uuid",
            "gpu_type": "h100",
            "gpu_count": 4,
            "status": "active",
        }

        message = format_cancel_confirmation(reservation)

        assert "abc12345" in message
        assert "h100" in message
        assert "4" in message


class TestCancelResultHandling:
    """Tests for handling cancel results"""

    def test_cancel_success_response(self):
        """Should handle successful cancel response"""
        from gpu_dev_cli.reservations import parse_cancel_response

        response = {
            "statusCode": 200,
            "body": json.dumps({
                "message": "Reservation cancelled",
                "reservation_id": "res-123",
                "status": "cancelling",
            }),
        }

        result = parse_cancel_response(response)

        assert result["success"] is True
        assert result["status"] == "cancelling"

    def test_cancel_failure_response(self):
        """Should handle failed cancel response"""
        from gpu_dev_cli.reservations import parse_cancel_response

        response = {
            "statusCode": 400,
            "body": json.dumps({
                "error": "Reservation not found",
            }),
        }

        result = parse_cancel_response(response)

        assert result["success"] is False
        assert "error" in result
