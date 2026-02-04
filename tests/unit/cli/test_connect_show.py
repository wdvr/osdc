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
        """Should generate valid SSH config"""
        from gpu_dev_cli.ssh import generate_ssh_config

        reservation = {
            "reservation_id": "abc12345-uuid",
            "pod_name": "gpu-dev-abc12345",
            "node_ip": "10.0.1.100",
            "node_port": 30123,
            "user": "dev",
        }

        config = generate_ssh_config(reservation)

        assert "Host" in config
        assert "10.0.1.100" in config or "HostName" in config
        assert "30123" in config or "Port" in config


class TestConnectionInfo:
    """Tests for connection info parsing"""

    def test_parse_connection_info(self):
        """Should parse connection info from reservation"""
        from gpu_dev_cli.connect import parse_connection_info

        reservation = {
            "reservation_id": "abc12345-uuid",
            "pod_name": "gpu-dev-abc12345",
            "node_ip": "10.0.1.100",
            "node_port": 30123,
            "status": "active",
        }

        info = parse_connection_info(reservation)

        assert info["host"] == "10.0.1.100"
        assert info["port"] == 30123
        assert info["user"] == "dev"

    def test_build_ssh_command(self):
        """Should build correct SSH command"""
        from gpu_dev_cli.connect import build_ssh_command

        connection_info = {
            "host": "10.0.1.100",
            "port": 30123,
            "user": "dev",
        }

        cmd = build_ssh_command(connection_info)

        assert "ssh" in cmd
        assert "10.0.1.100" in cmd
        assert "30123" in cmd


class TestShowDisplay:
    """Tests for show command display formatting"""

    def test_format_reservation_details(self):
        """Should format reservation details"""
        from gpu_dev_cli.display import format_reservation_details

        reservation = {
            "reservation_id": "abc12345-uuid-here-1234",
            "user_id": "testuser",
            "status": "active",
            "gpu_type": "h100",
            "gpu_count": 4,
            "duration_hours": 8,
            "created_at": "2024-01-15T10:00:00Z",
            "expires_at": "2024-01-15T18:00:00Z",
            "pod_name": "gpu-dev-abc12345",
            "node_ip": "10.0.1.100",
            "node_port": 30123,
        }

        output = format_reservation_details(reservation)

        assert "abc12345" in output
        assert "active" in output.lower()
        assert "h100" in output.lower()

    def test_get_status_color(self):
        """Should return status with color code"""
        from gpu_dev_cli.display import get_status_color

        assert get_status_color("active") == "green"
        assert get_status_color("queued") == "yellow"
        assert get_status_color("cancelled") == "red"
        assert get_status_color("expired") == "red"
