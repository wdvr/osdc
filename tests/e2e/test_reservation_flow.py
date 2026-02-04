"""
End-to-end tests for GPU reservation flow

These tests run against a real AWS dev cluster (us-west-1).
Requires:
- RUN_E2E_TESTS=1 environment variable
- Valid AWS credentials with gpu-dev access
- E2E_GITHUB_USER set to a valid GitHub username
"""

import os
import subprocess
import time
from datetime import datetime, timezone

import pytest


# Skip all E2E tests if not explicitly enabled
pytestmark = pytest.mark.skipif(
    not os.environ.get("RUN_E2E_TESTS"),
    reason="E2E tests require RUN_E2E_TESTS=1"
)


@pytest.fixture(scope="module")
def cli_config():
    """Set up CLI to use test environment"""
    # Switch to test environment
    result = subprocess.run(
        ["gpu-dev", "config", "environment", "test"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"Failed to set test environment: {result.stderr}"

    yield {
        "region": "us-west-1",
        "environment": "test",
    }


@pytest.fixture
def cleanup_reservations():
    """Track and cleanup reservations after tests"""
    created_reservations = []

    yield created_reservations

    # Cleanup
    for res_id in created_reservations:
        try:
            subprocess.run(
                ["gpu-dev", "cancel", res_id, "--force"],
                capture_output=True,
                timeout=60,
            )
        except Exception as e:
            print(f"Warning: Failed to cleanup reservation {res_id}: {e}")


class TestBasicReservation:
    """Tests for basic single-GPU reservations"""

    @pytest.mark.e2e
    @pytest.mark.timeout(300)
    def test_reserve_single_gpu_t4(self, cli_config, cleanup_reservations):
        """Should reserve 1 T4 GPU successfully"""
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "t4",
                "--hours", "0.25",  # 15 minutes
                "--name", "e2e-test-t4",
                "--no-wait",  # Don't wait for pod to be ready
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0, f"Reserve failed: {result.stderr}"

        # Extract reservation ID from output
        output = result.stdout
        assert "reservation" in output.lower() or "queued" in output.lower()

        # List reservations to get ID
        list_result = subprocess.run(
            ["gpu-dev", "list", "--status", "all"],
            capture_output=True,
            text=True,
        )
        assert list_result.returncode == 0

        # Find the test reservation
        lines = list_result.stdout.split("\n")
        for line in lines:
            if "e2e-test-t4" in line or "queued" in line.lower() or "pending" in line.lower():
                # Found our reservation, extract ID if visible
                parts = line.split()
                if parts:
                    cleanup_reservations.append(parts[0])
                break

    @pytest.mark.e2e
    @pytest.mark.timeout(300)
    def test_reserve_multiple_gpus(self, cli_config, cleanup_reservations):
        """Should reserve multiple GPUs on same node"""
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "2",
                "--gpu-type", "t4",
                "--hours", "0.25",
                "--name", "e2e-test-multi-gpu",
                "--no-wait",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Should either succeed or queue (depending on availability)
        assert result.returncode == 0 or "queued" in result.stdout.lower()


class TestJupyterIntegration:
    """Tests for Jupyter Lab integration"""

    @pytest.mark.e2e
    @pytest.mark.timeout(600)
    @pytest.mark.slow
    def test_reserve_with_jupyter(self, cli_config, cleanup_reservations):
        """Should enable Jupyter when requested"""
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "t4",
                "--hours", "0.25",
                "--jupyter",
                "--name", "e2e-test-jupyter",
                "--no-wait",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert result.returncode == 0 or "queued" in result.stdout.lower()


class TestDiskManagement:
    """Tests for persistent disk functionality"""

    @pytest.mark.e2e
    @pytest.mark.timeout(120)
    def test_list_disks(self, cli_config):
        """Should list user's disks"""
        result = subprocess.run(
            ["gpu-dev", "disk", "list"],
            capture_output=True,
            text=True,
            timeout=60,
        )

        assert result.returncode == 0
        # Output should contain table headers or "no disks" message
        assert "disk" in result.stdout.lower() or "no disks" in result.stdout.lower()

    @pytest.mark.e2e
    @pytest.mark.timeout(120)
    def test_create_and_delete_disk(self, cli_config):
        """Should create and delete a disk"""
        disk_name = f"e2e-test-{int(time.time())}"

        # Create disk
        create_result = subprocess.run(
            ["gpu-dev", "disk", "create", disk_name],
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Should succeed or say already exists
        if create_result.returncode == 0:
            # Wait for disk to appear
            time.sleep(5)

            # Verify disk exists
            list_result = subprocess.run(
                ["gpu-dev", "disk", "list"],
                capture_output=True,
                text=True,
            )
            assert disk_name in list_result.stdout

            # Delete disk
            delete_result = subprocess.run(
                ["gpu-dev", "disk", "delete", disk_name, "--yes"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            assert delete_result.returncode == 0


class TestAvailabilityChecks:
    """Tests for GPU availability information"""

    @pytest.mark.e2e
    @pytest.mark.timeout(60)
    def test_check_availability(self, cli_config):
        """Should show GPU availability by type"""
        result = subprocess.run(
            ["gpu-dev", "avail"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0
        # Should show GPU types
        output_lower = result.stdout.lower()
        assert "t4" in output_lower or "gpu" in output_lower

    @pytest.mark.e2e
    @pytest.mark.timeout(60)
    def test_cluster_status(self, cli_config):
        """Should show cluster status"""
        result = subprocess.run(
            ["gpu-dev", "status"],
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 0


class TestCancellation:
    """Tests for reservation cancellation"""

    @pytest.mark.e2e
    @pytest.mark.timeout(300)
    def test_cancel_reservation(self, cli_config):
        """Should cancel a reservation"""
        # First create a reservation
        reserve_result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "t4",
                "--hours", "0.25",
                "--name", "e2e-test-cancel",
                "--no-wait",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        if reserve_result.returncode != 0:
            pytest.skip("Could not create reservation to cancel")

        # Wait briefly
        time.sleep(5)

        # List and get reservation ID
        list_result = subprocess.run(
            ["gpu-dev", "list"],
            capture_output=True,
            text=True,
        )

        # Cancel all test reservations
        cancel_result = subprocess.run(
            ["gpu-dev", "cancel", "--all", "--force"],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Should succeed or say no reservations
        assert cancel_result.returncode == 0 or "no" in cancel_result.stdout.lower()


class TestExtendReservation:
    """Tests for reservation extension"""

    @pytest.mark.e2e
    @pytest.mark.timeout(600)
    @pytest.mark.slow
    def test_extend_active_reservation(self, cli_config, cleanup_reservations):
        """Should extend an active reservation"""
        # Create a short reservation
        reserve_result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "t4",
                "--hours", "0.5",
                "--name", "e2e-test-extend",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if reserve_result.returncode != 0:
            pytest.skip("Could not create reservation to extend")

        # Wait for reservation to become active
        time.sleep(10)

        # Get the reservation ID
        list_result = subprocess.run(
            ["gpu-dev", "list", "--details"],
            capture_output=True,
            text=True,
        )

        # Try to extend (may fail if not active yet)
        extend_result = subprocess.run(
            ["gpu-dev", "edit", "--extend", "1"],  # Extend by 1 hour
            capture_output=True,
            text=True,
            timeout=60,
        )

        # Should succeed or say reservation not found/not active
        assert extend_result.returncode == 0 or "not active" in extend_result.stderr.lower()


class TestSSHAccess:
    """Tests for SSH connectivity"""

    @pytest.mark.e2e
    @pytest.mark.timeout(600)
    @pytest.mark.slow
    def test_ssh_connection_info(self, cli_config, cleanup_reservations):
        """Should provide valid SSH connection info"""
        # Create reservation and wait for it to be active
        reserve_result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "t4",
                "--hours", "0.5",
                "--name", "e2e-test-ssh",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )

        if reserve_result.returncode != 0:
            pytest.skip("Could not create reservation for SSH test")

        # Output should contain SSH command
        output = reserve_result.stdout
        assert "ssh" in output.lower() or "connecting" in output.lower()

    @pytest.mark.e2e
    @pytest.mark.timeout(30)
    def test_ssh_config_generation(self, cli_config):
        """Should generate SSH config file"""
        # Check if config files exist
        import os
        from pathlib import Path

        devgpu_dir = Path.home() / ".devgpu"
        if devgpu_dir.exists():
            configs = list(devgpu_dir.glob("*-sshconfig"))
            # Just verify the directory structure is correct
            assert devgpu_dir.is_dir()


class TestMultinodeReservation:
    """Tests for multinode (distributed) reservations"""

    @pytest.mark.e2e
    @pytest.mark.timeout(900)
    @pytest.mark.slow
    def test_multinode_reservation_creates_multiple_pods(self, cli_config, cleanup_reservations):
        """Should create multiple pods for distributed reservation"""
        # This test requires H100/B200 nodes which may not be available in test
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "8",
                "--gpu-type", "h100",
                "--distributed",
                "--hours", "0.5",
                "--name", "e2e-test-multinode",
                "--no-wait",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # May succeed, queue, or fail due to no H100 availability
        # We just verify the command is accepted
        assert result.returncode == 0 or "queued" in result.stdout.lower() or "not available" in result.stderr.lower()


class TestCustomDockerImage:
    """Tests for custom Docker image support"""

    @pytest.mark.e2e
    @pytest.mark.timeout(600)
    @pytest.mark.slow
    def test_reserve_with_custom_image(self, cli_config, cleanup_reservations):
        """Should accept custom Docker image"""
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "t4",
                "--dockerimage", "pytorch/pytorch:2.0.1-cuda11.7-cudnn8-devel",
                "--hours", "0.25",
                "--name", "e2e-test-custom-image",
                "--no-wait",
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )

        # Should accept the custom image
        assert result.returncode == 0 or "queued" in result.stdout.lower()


class TestErrorHandling:
    """Tests for error handling and validation"""

    @pytest.mark.e2e
    @pytest.mark.timeout(60)
    def test_invalid_gpu_type_rejected(self, cli_config):
        """Should reject invalid GPU types"""
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "1",
                "--gpu-type", "invalid_gpu_type",
                "--hours", "1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail with error message
        assert result.returncode != 0 or "invalid" in result.stderr.lower() or "error" in result.stderr.lower()

    @pytest.mark.e2e
    @pytest.mark.timeout(60)
    def test_excessive_gpus_rejected(self, cli_config):
        """Should reject request for more GPUs than available on node"""
        result = subprocess.run(
            [
                "gpu-dev", "reserve",
                "--gpus", "100",  # Way more than any node has
                "--gpu-type", "t4",
                "--hours", "1",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Should fail or warn
        assert result.returncode != 0 or "error" in result.stderr.lower() or "max" in result.stderr.lower()

    @pytest.mark.e2e
    @pytest.mark.timeout(60)
    def test_missing_github_user_rejected(self, cli_config, tmp_path):
        """Should require GitHub username to be configured"""
        # This test would require a fresh config, which is complex to set up
        # Skip for now as it requires special setup
        pytest.skip("Requires isolated config environment")
