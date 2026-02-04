"""
Unit tests for gpu_dev_cli edit command

Tests:
- Extend reservation (duration extension)
- Add user (collaborator)
- Enable Jupyter
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestExtendReservation:
    """Tests for extending reservation duration"""

    def test_build_extend_request(self):
        """Should build correct extend request"""
        from gpu_dev_cli.reservations import build_extend_request

        request = build_extend_request(
            reservation_id="abc12345",
            additional_hours=4,
        )

        assert request["reservation_id"] == "abc12345"
        assert request["extend_hours"] == 4
        assert request["action"] == "extend"

    def test_validate_extend_hours_valid(self):
        """Should accept valid extension hours"""
        from gpu_dev_cli.reservations import validate_extend_hours

        assert validate_extend_hours(1) == 1
        assert validate_extend_hours(12) == 12
        assert validate_extend_hours(24) == 24

    def test_extend_permission_check(self):
        """Should verify user can extend their reservation"""
        from gpu_dev_cli.reservations import can_extend_reservation

        reservation = {
            "user_id": "owner-user",
            "status": "active",
            "duration_hours": 8,
            "extended_hours": 0,
        }

        assert can_extend_reservation(reservation, "owner-user") is True
        assert can_extend_reservation(reservation, "other-user") is False


class TestAddUser:
    """Tests for adding collaborators"""

    def test_build_add_user_request(self):
        """Should build correct add-user request"""
        from gpu_dev_cli.reservations import build_add_user_request

        request = build_add_user_request(
            reservation_id="abc12345",
            github_username="collaborator",
        )

        assert request["reservation_id"] == "abc12345"
        assert request["add_github_user"] == "collaborator"
        assert request["action"] == "add_user"

    def test_validate_github_username_valid(self):
        """Should accept valid GitHub usernames"""
        from gpu_dev_cli.reservations import validate_github_username

        valid_names = ["user", "user-name", "user123", "User-Name-123"]

        for name in valid_names:
            assert validate_github_username(name) == name


class TestEnableJupyter:
    """Tests for enabling Jupyter on existing reservation"""

    def test_build_enable_jupyter_request(self):
        """Should build correct enable-jupyter request"""
        from gpu_dev_cli.reservations import build_enable_jupyter_request

        request = build_enable_jupyter_request(
            reservation_id="abc12345",
        )

        assert request["reservation_id"] == "abc12345"
        assert request["enable_jupyter"] is True
        assert request["action"] == "enable_jupyter"
