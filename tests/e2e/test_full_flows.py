"""
End-to-end test stubs for ODC

These tests run against the real AWS us-west-1 test cluster.
They are skipped by default - run with RUN_E2E_TESTS=1 to enable.

Test flows:
- Complete reservation lifecycle
- Disk management
- Multinode reservations
- Jupyter integration
"""

import os
import subprocess
import time
from datetime import datetime, timezone

import pytest


# Skip all tests if E2E not enabled
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_E2E_TESTS"),
    reason="E2E tests require RUN_E2E_TESTS=1"
)


@pytest.fixture
def gpu_dev_cli():
    """Wrapper for gpu-dev CLI commands"""
    def run(*args, timeout=60):
        cmd = ["gpu-dev"] + list(args)
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result
    return run


@pytest.fixture
def cleanup_reservations():
    """Track and cleanup reservations after test"""
    created = []
    yield created

    # Cleanup
    for res_id in created:
        try:
            subprocess.run(
                ["gpu-dev", "cancel", res_id, "--force"],
                capture_output=True,
                timeout=30,
            )
        except Exception as e:
            print(f"Warning: Failed to cleanup {res_id}: {e}")


@pytest.mark.e2e
@pytest.mark.slow
class TestReservationLifecycle:
    """E2E tests for complete reservation lifecycle"""

    def test_reserve_wait_connect_cancel(self, gpu_dev_cli, cleanup_reservations):
        """Should complete full reservation lifecycle"""
        # Reserve
        result = gpu_dev_cli(
            "reserve",
            "--gpu-type", "t4",
            "--gpus", "1",
            "--hours", "0.25",  # 15 minutes
            "--disk", "none",
            "-y",  # Skip confirmation
            timeout=120,
        )

        assert result.returncode == 0, f"Reserve failed: {result.stderr}"

        # Extract reservation ID from output
        # Expected format: "Reservation created: abc12345..."
        res_id = None
        for line in result.stdout.split("\n"):
            if "reservation" in line.lower() and ":" in line:
                parts = line.split(":")
                if len(parts) >= 2:
                    res_id = parts[-1].strip()[:8]
                    break

        assert res_id is not None, "Could not extract reservation ID"
        cleanup_reservations.append(res_id)

        # Wait for active status
        max_wait = 180  # 3 minutes
        start = time.time()
        status = None

        while time.time() - start < max_wait:
            list_result = gpu_dev_cli("list", "--json")
            if list_result.returncode == 0:
                import json
                reservations = json.loads(list_result.stdout)
                for res in reservations:
                    if res["reservation_id"].startswith(res_id):
                        status = res["status"]
                        if status == "active":
                            break
            if status == "active":
                break
            time.sleep(5)

        assert status == "active", f"Reservation did not become active: {status}"

        # Get connection info
        show_result = gpu_dev_cli("show", res_id)
        assert show_result.returncode == 0
        assert "ssh" in show_result.stdout.lower()

        # Cancel
        cancel_result = gpu_dev_cli("cancel", res_id, "--force")
        assert cancel_result.returncode == 0

    def test_reserve_with_disk(self, gpu_dev_cli, cleanup_reservations):
        """Should create reservation with persistent disk"""
        # First create a disk
        disk_name = f"e2e-test-{int(time.time())}"

        create_result = gpu_dev_cli("disk", "create", disk_name)
        assert create_result.returncode == 0

        try:
            # Reserve with disk
            result = gpu_dev_cli(
                "reserve",
                "--gpu-type", "t4",
                "--gpus", "1",
                "--hours", "0.25",
                "--disk", disk_name,
                "-y",
                timeout=120,
            )

            assert result.returncode == 0
            # Extract and track reservation ID for cleanup
            # ... (similar to above)

        finally:
            # Cleanup disk
            gpu_dev_cli("disk", "delete", disk_name, "--force")


@pytest.mark.e2e
class TestDiskManagement:
    """E2E tests for disk management"""

    def test_disk_create_list_delete(self, gpu_dev_cli):
        """Should create, list, and delete a disk"""
        disk_name = f"e2e-disk-{int(time.time())}"

        # Create
        create_result = gpu_dev_cli("disk", "create", disk_name)
        assert create_result.returncode == 0

        try:
            # List
            list_result = gpu_dev_cli("disk", "list")
            assert list_result.returncode == 0
            assert disk_name in list_result.stdout

        finally:
            # Delete
            delete_result = gpu_dev_cli("disk", "delete", disk_name, "--force")
            assert delete_result.returncode == 0

    def test_disk_rename(self, gpu_dev_cli):
        """Should rename a disk"""
        old_name = f"e2e-old-{int(time.time())}"
        new_name = f"e2e-new-{int(time.time())}"

        # Create
        gpu_dev_cli("disk", "create", old_name)

        try:
            # Rename
            rename_result = gpu_dev_cli("disk", "rename", old_name, new_name)
            assert rename_result.returncode == 0

            # Verify
            list_result = gpu_dev_cli("disk", "list")
            assert new_name in list_result.stdout
            assert old_name not in list_result.stdout

        finally:
            # Cleanup (use new name)
            gpu_dev_cli("disk", "delete", new_name, "--force")


@pytest.mark.e2e
class TestAvailability:
    """E2E tests for availability checking"""

    def test_avail_command(self, gpu_dev_cli):
        """Should show GPU availability"""
        result = gpu_dev_cli("avail")

        assert result.returncode == 0
        # Should show at least one GPU type
        assert any(gpu in result.stdout.lower() for gpu in ["t4", "l4", "a100", "h100"])


@pytest.mark.e2e
class TestCLICommands:
    """E2E tests for basic CLI commands"""

    def test_config_show(self, gpu_dev_cli):
        """Should show configuration"""
        result = gpu_dev_cli("config", "show")

        assert result.returncode == 0
        assert "github" in result.stdout.lower() or "region" in result.stdout.lower()

    def test_list_command(self, gpu_dev_cli):
        """Should list reservations"""
        result = gpu_dev_cli("list")

        # Should succeed even with no reservations
        assert result.returncode == 0

    def test_help_command(self, gpu_dev_cli):
        """Should show help"""
        result = gpu_dev_cli("--help")

        assert result.returncode == 0
        assert "reserve" in result.stdout
        assert "list" in result.stdout


@pytest.mark.e2e
@pytest.mark.slow
class TestMultinodeReservation:
    """E2E tests for multinode reservations"""

    def test_multinode_reserve(self, gpu_dev_cli, cleanup_reservations):
        """Should create multinode reservation (requires 16 GPUs)"""
        pytest.skip("Multinode requires 16 GPUs - run manually when capacity available")

        result = gpu_dev_cli(
            "reserve",
            "--gpu-type", "h100",
            "--gpus", "16",
            "--distributed",
            "--hours", "0.5",
            "--disk", "none",
            "-y",
            timeout=300,
        )

        assert result.returncode == 0


@pytest.mark.e2e
@pytest.mark.slow
class TestJupyterIntegration:
    """E2E tests for Jupyter integration"""

    def test_reserve_with_jupyter(self, gpu_dev_cli, cleanup_reservations):
        """Should create reservation with Jupyter enabled"""
        result = gpu_dev_cli(
            "reserve",
            "--gpu-type", "t4",
            "--gpus", "1",
            "--hours", "0.25",
            "--jupyter",
            "--disk", "none",
            "-y",
            timeout=120,
        )

        assert result.returncode == 0

        # Wait for active and check Jupyter URL in show output
        # ... (implementation similar to lifecycle test)
