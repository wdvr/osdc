"""
Unit tests for gpu_dev_cli connect and show commands

Tests:
- SSH config generation
- Connection info parsing
- Display formatting
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestSSHConfigGeneration:
    """Tests for SSH config file generation"""

    def test_generate_ssh_config(self):
        """Should generate valid SSH config content"""
        from gpu_dev_cli.reservations import _generate_ssh_config

        config = _generate_ssh_config("myhost.devservers.io", "gpu-dev-abc123")

        assert "Host gpu-dev-abc123" in config
        assert "HostName myhost.devservers.io" in config
        assert "User dev" in config
        assert "ForwardAgent yes" in config
        assert "StrictHostKeyChecking no" in config

    def test_generate_ssh_config_with_port(self):
        """Should include port in SSH config"""
        from gpu_dev_cli.reservations import _generate_ssh_config

        config = _generate_ssh_config("myhost.devservers.io", "gpu-dev-abc123", port=30123)

        assert "Port 30123" in config

    def test_get_ssh_config_path(self):
        """Should return correct SSH config path"""
        from gpu_dev_cli.reservations import get_ssh_config_path

        path = get_ssh_config_path("abc12345-uuid-uuid-uuid")

        assert ".gpu-dev" in path
        assert "abc12345" in path
        assert "sshconfig" in path

    def test_get_ssh_config_path_with_name(self):
        """Should include name in SSH config path when provided"""
        from gpu_dev_cli.reservations import get_ssh_config_path

        path = get_ssh_config_path("abc12345-uuid", name="my-experiment")

        assert "abc12345" in path


class TestConnectionInfo:
    """Tests for connection info parsing and building"""

    def test_extract_ip_from_reservation_with_port(self):
        """Should extract IP:Port from reservation data"""
        from gpu_dev_cli.reservations import _extract_ip_from_reservation

        reservation = {
            "node_ip": "10.0.1.100",
            "node_port": 30123,
        }

        result = _extract_ip_from_reservation(reservation)
        assert result == "10.0.1.100:30123"

    def test_extract_ip_from_reservation_no_port(self):
        """Should extract IP without port when not available"""
        from gpu_dev_cli.reservations import _extract_ip_from_reservation

        reservation = {
            "node_ip": "10.0.1.100",
        }

        result = _extract_ip_from_reservation(reservation)
        assert result == "10.0.1.100"

    def test_extract_ip_missing_data(self):
        """Should return N/A when no IP data available"""
        from gpu_dev_cli.reservations import _extract_ip_from_reservation

        assert _extract_ip_from_reservation({}) == "N/A"
        assert _extract_ip_from_reservation({"status": "active"}) == "N/A"

    def test_build_ssh_command(self):
        """Should build correct SSH command from reservation"""
        from gpu_dev_cli.reservations import _build_ssh_command

        reservation = {
            "node_ip": "10.0.1.100",
            "node_port": 30123,
        }

        cmd = _build_ssh_command(reservation)

        assert "ssh" in cmd
        assert "10.0.1.100" in cmd
        assert "30123" in cmd
        assert "dev@" in cmd

    def test_add_agent_forwarding(self):
        """Should add -A flag to SSH command"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        result = _add_agent_forwarding_to_ssh("ssh dev@10.0.1.100 -p 30123")

        assert "-A" in result
        assert result == "ssh -A dev@10.0.1.100 -p 30123"

    def test_add_agent_forwarding_no_duplicate(self):
        """Should not add -A if already present"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        result = _add_agent_forwarding_to_ssh("ssh -A dev@10.0.1.100 -p 30123")
        assert result.count("-A") == 1


class TestIDEIntegration:
    """Tests for IDE link generation"""

    def test_make_vscode_link(self):
        """Should generate VS Code remote SSH link"""
        from gpu_dev_cli.reservations import _make_vscode_link

        link = _make_vscode_link("gpu-dev-abc123")

        assert link == "vscode://vscode-remote/ssh-remote+gpu-dev-abc123/home/dev"
        assert link.startswith("vscode://")

    def test_make_cursor_link(self):
        """Should generate Cursor IDE remote SSH link"""
        from gpu_dev_cli.reservations import _make_cursor_link

        link = _make_cursor_link("gpu-dev-abc123")

        assert link == "cursor://vscode-remote/ssh-remote+gpu-dev-abc123/home/dev"
        assert link.startswith("cursor://")

    def test_generate_vscode_command(self):
        """Should generate VS Code CLI command"""
        from gpu_dev_cli.reservations import _generate_vscode_command

        cmd = _generate_vscode_command("ssh dev@myhost.io -p 30001")

        assert cmd is not None
        assert "code --remote" in cmd
        assert "myhost.io" in cmd
        assert "ForwardAgent=yes" in cmd

    def test_generate_vscode_command_invalid(self):
        """Should return None for invalid SSH command"""
        from gpu_dev_cli.reservations import _generate_vscode_command

        assert _generate_vscode_command("") is None
        assert _generate_vscode_command("not-ssh") is None


class TestShowCommand:
    """Tests for show command functionality"""

    def test_get_connection_info_active(self):
        """Should get connection info for active reservation"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "abc12345-uuid-uuid",
            "user_id": "testuser",
            "status": "active",
            "gpu_type": "h100",
            "gpu_count": 4,
            "duration_hours": 8,
            "created_at": "2026-01-15T10:00:00Z",
            "expires_at": "2026-01-15T18:00:00Z",
            "pod_name": "gpu-dev-abc12345",
            "node_ip": "10.0.1.100",
            "node_port": 30123,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="abc12345-uuid-uuid",
                user_id="testuser",
            )

            assert result is not None
            assert result["status"] == "active"
            assert result["node_ip"] == "10.0.1.100"
            assert result["node_port"] == 30123

    def test_get_connection_info_by_short_id(self):
        """Should find reservation by short ID prefix"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.side_effect = RuntimeError("not found")
        mock_api_client.list_jobs.return_value = {
            "jobs": [
                {
                    "reservation_id": "abc12345-full-uuid",
                    "job_id": "abc12345-full-uuid",
                    "status": "active",
                    "node_ip": "10.0.1.100",
                    "node_port": 30123,
                }
            ],
            "total": 1,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="abc12345",
                user_id="testuser",
            )

            assert result is not None
            assert result["reservation_id"] == "abc12345-full-uuid"

    def test_get_connection_info_not_found(self):
        """Should return None when reservation not found"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.side_effect = RuntimeError("not found")
        mock_api_client.list_jobs.return_value = {"jobs": [], "total": 0}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="nonexistent",
                user_id="testuser",
            )

            assert result is None


class TestConnectCommand:
    """Tests for connect command functionality"""

    def test_get_active_reservation_for_connect(self):
        """Should get active reservation details for connecting"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "abc12345",
            "status": "active",
            "pod_name": "gpu-dev-abc12345",
            "node_ip": "10.0.1.100",
            "node_port": 30123,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="abc12345",
                user_id="testuser",
            )

            assert result["status"] == "active"
            assert result["node_ip"] == "10.0.1.100"

    def test_connect_to_pending_reservation(self):
        """Should handle connecting to pending reservation"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "abc12345",
            "status": "pending",
            "pod_name": None,
            "node_ip": None,
            "node_port": None,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="abc12345",
                user_id="testuser",
            )

            # Connection info is returned but node_ip is None
            assert result["status"] == "pending"
            assert result["node_ip"] is None


class TestStatusColors:
    """Tests for status color coding"""

    def test_status_to_color_mapping(self):
        """Should map status to correct color"""
        from gpu_dev_cli.reservations import _get_status_color

        assert _get_status_color("active") == "green"
        assert _get_status_color("queued") == "yellow"
        assert _get_status_color("pending") == "yellow"
        assert _get_status_color("preparing") == "yellow"
        assert _get_status_color("cancelled") == "red"
        assert _get_status_color("expired") == "red"
        assert _get_status_color("failed") == "red"
        assert _get_status_color("unknown") == "white"


class TestDisplayFormatting:
    """Tests for output formatting"""

    def test_format_time_remaining(self):
        """Should format time remaining correctly"""
        from gpu_dev_cli.reservations import _format_time_remaining

        # Test hours
        assert "7h" in _format_time_remaining(7 * 60)
        assert "1h" in _format_time_remaining(75)

        # Test minutes
        assert "30m" in _format_time_remaining(30)
        assert "5m" in _format_time_remaining(5)

        # Test expired
        result = _format_time_remaining(-10)
        assert "expired" in result.lower() or "-" in result

    def test_format_reservation_id_short(self):
        """Should display short reservation ID"""
        from gpu_dev_cli.reservations import _format_short_id

        full_id = "abc12345-1234-5678-9012-345678901234"
        short_id = _format_short_id(full_id)

        assert short_id == "abc12345"
        assert len(short_id) == 8
