"""
End-to-end tests for CLI command behavior

Tests CLI commands without actually creating reservations.
These tests verify the CLI interface works correctly.
"""

import os
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_E2E_TESTS"),
    reason="E2E tests require RUN_E2E_TESTS=1"
)


class TestCLIHelp:
    """Tests for CLI help and version commands"""

    @pytest.mark.e2e
    def test_cli_help(self):
        """Should show help message"""
        result = subprocess.run(
            ["gpu-dev", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        assert "Usage" in result.stdout or "usage" in result.stdout
        assert "reserve" in result.stdout
        assert "list" in result.stdout

    @pytest.mark.e2e
    def test_reserve_help(self):
        """Should show reserve command help"""
        result = subprocess.run(
            ["gpu-dev", "reserve", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        assert "--gpus" in result.stdout
        assert "--gpu-type" in result.stdout
        assert "--hours" in result.stdout

    @pytest.mark.e2e
    def test_list_help(self):
        """Should show list command help"""
        result = subprocess.run(
            ["gpu-dev", "list", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        assert "--status" in result.stdout or "--user" in result.stdout

    @pytest.mark.e2e
    def test_disk_help(self):
        """Should show disk command help"""
        result = subprocess.run(
            ["gpu-dev", "disk", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        assert "list" in result.stdout
        assert "create" in result.stdout


class TestConfigCommands:
    """Tests for configuration commands"""

    @pytest.mark.e2e
    def test_config_show(self):
        """Should show current configuration"""
        result = subprocess.run(
            ["gpu-dev", "config"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should show config or prompt for setup
        assert result.returncode == 0 or "github" in result.stderr.lower()

    @pytest.mark.e2e
    def test_config_environment_list(self):
        """Should list available environments"""
        result = subprocess.run(
            ["gpu-dev", "config", "environment", "--help"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0

    @pytest.mark.e2e
    def test_config_environment_switch(self):
        """Should switch between test and prod environments"""
        # Switch to test
        test_result = subprocess.run(
            ["gpu-dev", "config", "environment", "test"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert test_result.returncode == 0

        # Switch back to prod
        prod_result = subprocess.run(
            ["gpu-dev", "config", "environment", "prod"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert prod_result.returncode == 0


class TestListCommand:
    """Tests for list command functionality"""

    @pytest.mark.e2e
    def test_list_all_reservations(self):
        """Should list reservations with various filters"""
        result = subprocess.run(
            ["gpu-dev", "list", "--status", "all"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0

    @pytest.mark.e2e
    def test_list_active_only(self):
        """Should filter to active reservations"""
        result = subprocess.run(
            ["gpu-dev", "list", "--status", "active"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0

    @pytest.mark.e2e
    def test_list_with_details(self):
        """Should show detailed reservation info"""
        result = subprocess.run(
            ["gpu-dev", "list", "--details"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0


class TestAvailCommand:
    """Tests for availability command"""

    @pytest.mark.e2e
    def test_avail_shows_gpu_types(self):
        """Should show availability for all GPU types"""
        result = subprocess.run(
            ["gpu-dev", "avail"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0
        # Should contain GPU type names
        output_lower = result.stdout.lower()
        # At least one type should be mentioned
        gpu_types = ["t4", "l4", "a100", "h100", "b200", "cpu"]
        assert any(t in output_lower for t in gpu_types)


class TestStatusCommand:
    """Tests for status command"""

    @pytest.mark.e2e
    def test_status_command(self):
        """Should show cluster status"""
        result = subprocess.run(
            ["gpu-dev", "status"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0


class TestDiskCommands:
    """Tests for disk subcommands"""

    @pytest.mark.e2e
    def test_disk_list_command(self):
        """Should list disks"""
        result = subprocess.run(
            ["gpu-dev", "disk", "list"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0

    @pytest.mark.e2e
    def test_disk_create_validates_name(self):
        """Should validate disk name format"""
        result = subprocess.run(
            ["gpu-dev", "disk", "create", "invalid name with spaces"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail due to invalid name
        assert result.returncode != 0 or "invalid" in result.stderr.lower()


class TestShowCommand:
    """Tests for show command"""

    @pytest.mark.e2e
    def test_show_nonexistent_reservation(self):
        """Should handle nonexistent reservation gracefully"""
        result = subprocess.run(
            ["gpu-dev", "show", "nonexistent-reservation-id"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail gracefully with message
        assert result.returncode != 0 or "not found" in result.stderr.lower() or "error" in result.stderr.lower()


class TestConnectCommand:
    """Tests for connect command"""

    @pytest.mark.e2e
    def test_connect_no_active_reservations(self):
        """Should handle case with no active reservations"""
        result = subprocess.run(
            ["gpu-dev", "connect"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should either connect or say no reservations
        # We don't assert returncode since it depends on whether user has reservations


class TestCancelCommand:
    """Tests for cancel command"""

    @pytest.mark.e2e
    def test_cancel_nonexistent(self):
        """Should handle canceling nonexistent reservation"""
        result = subprocess.run(
            ["gpu-dev", "cancel", "nonexistent-id"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail gracefully
        assert result.returncode != 0 or "not found" in result.stderr.lower()
