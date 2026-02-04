"""
Unit tests for gpu_dev_cli reserve command

Tests:
- GPU type mapping and validation
- GPU count validation per type
- Duration validation
- All reservation options
- API request format transformation
"""

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


class TestGPUTypeMapping:
    """Tests for GPU type to instance type mapping"""

    def test_map_gpu_to_instance_type_valid_types(self):
        """Should map all valid GPU types to correct instance types"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        assert _map_gpu_to_instance_type("t4", 4) == "g4dn.12xlarge"
        assert _map_gpu_to_instance_type("t4-small", 1) == "g4dn.2xlarge"
        assert _map_gpu_to_instance_type("l4", 4) == "g6.12xlarge"
        assert _map_gpu_to_instance_type("a10g", 4) == "g5.12xlarge"
        assert _map_gpu_to_instance_type("a100", 8) == "p4d.24xlarge"
        assert _map_gpu_to_instance_type("h100", 8) == "p5.48xlarge"
        assert _map_gpu_to_instance_type("h200", 8) == "p5e.48xlarge"
        assert _map_gpu_to_instance_type("b200", 8) == "p6-b200.48xlarge"

    def test_map_gpu_type_case_insensitive(self):
        """Should handle GPU types case-insensitively"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        assert _map_gpu_to_instance_type("H100", 1) == "p5.48xlarge"
        assert _map_gpu_to_instance_type("T4", 1) == "g4dn.12xlarge"
        assert _map_gpu_to_instance_type("A100", 1) == "p4d.24xlarge"

    def test_map_gpu_invalid_type_raises(self):
        """Should raise ValueError for invalid GPU types"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        with pytest.raises(ValueError, match="Unsupported GPU type"):
            _map_gpu_to_instance_type("invalid-gpu", 1)

        with pytest.raises(ValueError, match="Unsupported GPU type"):
            _map_gpu_to_instance_type("v100", 1)

    def test_cpu_instance_types(self):
        """Should support CPU-only instance types"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        assert _map_gpu_to_instance_type("cpu-arm", 0) == "c7g.8xlarge"
        assert _map_gpu_to_instance_type("cpu-x86", 0) == "c7i.8xlarge"


class TestGPUCountValidation:
    """Tests for GPU count validation per type"""

    def test_valid_gpu_counts_per_type(self):
        """Should accept valid GPU counts for each type"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        # T4 max 4 GPUs
        _map_gpu_to_instance_type("t4", 1)
        _map_gpu_to_instance_type("t4", 2)
        _map_gpu_to_instance_type("t4", 4)

        # H100/A100/B200/H200 max 8 GPUs
        _map_gpu_to_instance_type("h100", 1)
        _map_gpu_to_instance_type("h100", 4)
        _map_gpu_to_instance_type("h100", 8)

        # T4-small max 1 GPU
        _map_gpu_to_instance_type("t4-small", 1)

    def test_exceeds_max_gpus_raises(self):
        """Should raise ValueError when GPU count exceeds maximum"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        # T4 max is 4
        with pytest.raises(ValueError, match="GPU count 5 exceeds maximum 4"):
            _map_gpu_to_instance_type("t4", 5)

        # H100 max is 8
        with pytest.raises(ValueError, match="GPU count 16 exceeds maximum 8"):
            _map_gpu_to_instance_type("h100", 16)

        # T4-small max is 1
        with pytest.raises(ValueError, match="GPU count 2 exceeds maximum 1"):
            _map_gpu_to_instance_type("t4-small", 2)

    def test_zero_gpus_for_gpu_instance_raises(self):
        """Should raise ValueError for 0 GPUs on GPU instances"""
        from gpu_dev_cli.reservations import _map_gpu_to_instance_type

        with pytest.raises(ValueError, match="GPU count must be at least 1"):
            _map_gpu_to_instance_type("t4", 0)


class TestAPIFormatTransformation:
    """Tests for _transform_to_api_format function"""

    def test_transform_basic_reservation(self):
        """Should transform basic reservation to API format"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "reservation_id": "res-123",
            "user_id": "test-user",
            "gpu_type": "h100",
            "gpu_count": 4,
            "duration_hours": 8,
            "github_user": "testgithub",
        }

        result = _transform_to_api_format(message)

        assert result["instance_type"] == "p5.48xlarge"
        assert result["duration_hours"] == 8
        assert "env_vars" in result
        assert result["env_vars"]["GPU_TYPE"] == "h100"
        assert result["env_vars"]["GPU_COUNT"] == "4"
        assert result["env_vars"]["GITHUB_USER"] == "testgithub"

    def test_transform_includes_docker_image(self):
        """Should include custom docker image when provided"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "t4",
            "gpu_count": 1,
            "duration_hours": 4,
            "dockerimage": "pytorch/pytorch:2.0.0-cuda11.8",
        }

        result = _transform_to_api_format(message)

        assert result["image"] == "pytorch/pytorch:2.0.0-cuda11.8"

    def test_transform_uses_default_image(self):
        """Should use default image when no custom image provided"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "h100",
            "gpu_count": 8,
            "duration_hours": 24,
        }

        result = _transform_to_api_format(message)

        assert "image" in result
        assert "pytorch" in result["image"]

    def test_transform_includes_disk_name(self):
        """Should include disk_name when provided"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "t4",
            "gpu_count": 1,
            "duration_hours": 4,
            "disk_name": "my-project",
        }

        result = _transform_to_api_format(message)

        assert result["disk_name"] == "my-project"

    def test_transform_includes_jupyter_flag(self):
        """Should include jupyter_enabled in env_vars"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "t4",
            "gpu_count": 1,
            "duration_hours": 4,
            "jupyter_enabled": True,
        }

        result = _transform_to_api_format(message)

        assert result["env_vars"]["JUPYTER_ENABLED"] == "true"

    def test_transform_includes_preserve_entrypoint(self):
        """Should include preserve_entrypoint in env_vars"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "t4",
            "gpu_count": 1,
            "duration_hours": 4,
            "preserve_entrypoint": True,
        }

        result = _transform_to_api_format(message)

        assert result["env_vars"]["PRESERVE_ENTRYPOINT"] == "true"

    def test_transform_includes_recreate_env(self):
        """Should include recreate_env in env_vars"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "t4",
            "gpu_count": 1,
            "duration_hours": 4,
            "recreate_env": True,
        }

        result = _transform_to_api_format(message)

        assert result["env_vars"]["RECREATE_ENV"] == "true"

    def test_transform_includes_pod_name(self):
        """Should include name as POD_NAME in env_vars"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        message = {
            "gpu_type": "t4",
            "gpu_count": 1,
            "duration_hours": 4,
            "name": "my-experiment",
        }

        result = _transform_to_api_format(message)

        assert result["env_vars"]["POD_NAME"] == "my-experiment"

    def test_transform_raises_without_gpu_fields(self):
        """Should raise ValueError if gpu_type or gpu_count missing"""
        from gpu_dev_cli.reservations import _transform_to_api_format

        with pytest.raises(ValueError, match="missing required fields"):
            _transform_to_api_format({"duration_hours": 4})

        with pytest.raises(ValueError, match="missing required fields"):
            _transform_to_api_format({"gpu_type": "t4"})


class TestReservationManager:
    """Tests for ReservationManager class"""

    def test_create_reservation_calls_api(self):
        """Should call API client to submit job"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.submit_job.return_value = {"job_id": "job-123"}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.create_reservation(
                user_id="test-user",
                gpu_count=4,
                gpu_type="h100",
                duration_hours=8,
                github_user="testgithub",
            )

            assert result is not None
            mock_api_client.submit_job.assert_called_once()
            call_args = mock_api_client.submit_job.call_args[0][0]
            assert call_args["instance_type"] == "p5.48xlarge"
            assert call_args["duration_hours"] == 8

    def test_create_reservation_normalizes_gpu_type(self):
        """Should normalize GPU type to lowercase"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.submit_job.return_value = {"job_id": "job-123"}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            manager.create_reservation(
                user_id="test-user",
                gpu_count=4,
                gpu_type="H100",
                duration_hours=8,
            )

            call_args = mock_api_client.submit_job.call_args[0][0]
            assert call_args["env_vars"]["GPU_TYPE"] == "h100"

    def test_create_reservation_with_all_options(self):
        """Should pass all options to API"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.submit_job.return_value = {"job_id": "job-123"}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            manager.create_reservation(
                user_id="test-user",
                gpu_count=2,
                gpu_type="t4",
                duration_hours=4,
                github_user="testgithub",
                jupyter_enabled=True,
                disk_name="my-disk",
                dockerimage="custom/image:latest",
                preserve_entrypoint=True,
                recreate_env=True,
            )

            call_args = mock_api_client.submit_job.call_args[0][0]
            assert call_args["image"] == "custom/image:latest"
            assert call_args["disk_name"] == "my-disk"
            assert call_args["env_vars"]["JUPYTER_ENABLED"] == "true"
            assert call_args["env_vars"]["PRESERVE_ENTRYPOINT"] == "true"
            assert call_args["env_vars"]["RECREATE_ENV"] == "true"

    def test_create_reservation_handles_api_error(self):
        """Should return None on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.submit_job.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.create_reservation(
                user_id="test-user",
                gpu_count=1,
                gpu_type="t4",
                duration_hours=4,
            )

            assert result is None


class TestMultinodeReservation:
    """Tests for multinode reservation creation"""

    def test_multinode_calculates_node_count(self):
        """Should calculate correct number of nodes for multinode"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.submit_job.return_value = {"job_id": "job-123"}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.create_multinode_reservation(
                user_id="test-user",
                gpu_count=16,
                gpu_type="h100",
                duration_hours=8,
            )

            assert result is not None
            assert len(result) == 2
            assert mock_api_client.submit_job.call_count == 2

    def test_multinode_rejects_invalid_gpu_count(self):
        """Should reject GPU count not divisible by max per node"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.create_multinode_reservation(
                user_id="test-user",
                gpu_count=12,
                gpu_type="h100",
                duration_hours=8,
            )

            assert result is None
            mock_api_client.submit_job.assert_not_called()

    def test_multinode_jupyter_only_on_master(self):
        """Should enable Jupyter only on master node (node 0)"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.submit_job.return_value = {"job_id": "job-123"}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            manager.create_multinode_reservation(
                user_id="test-user",
                gpu_count=16,
                gpu_type="h100",
                duration_hours=8,
                jupyter_enabled=True,
            )

            calls = mock_api_client.submit_job.call_args_list
            first_call_env = calls[0][0][0]["env_vars"]
            second_call_env = calls[1][0][0]["env_vars"]

            # First node (master) should have Jupyter enabled
            assert first_call_env.get("JUPYTER_ENABLED") == "true"
            # Second node should either have Jupyter disabled or not set
            assert second_call_env.get("JUPYTER_ENABLED") in (None, "false")


class TestSSHHelpers:
    """Tests for SSH-related helper functions"""

    def test_add_agent_forwarding_to_ssh(self):
        """Should add -A flag to SSH command"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        result = _add_agent_forwarding_to_ssh("ssh dev@1.2.3.4 -p 30001")
        assert "-A" in result
        assert result == "ssh -A dev@1.2.3.4 -p 30001"

    def test_add_agent_forwarding_already_present(self):
        """Should not add -A if already present"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        result = _add_agent_forwarding_to_ssh("ssh -A dev@1.2.3.4 -p 30001")
        assert result.count("-A") == 1

    def test_add_agent_forwarding_invalid_command(self):
        """Should return unchanged for non-SSH commands"""
        from gpu_dev_cli.reservations import _add_agent_forwarding_to_ssh

        assert _add_agent_forwarding_to_ssh("") == ""
        assert _add_agent_forwarding_to_ssh("scp file user@host:") == "scp file user@host:"

    def test_generate_vscode_command(self):
        """Should generate VS Code remote SSH command"""
        from gpu_dev_cli.reservations import _generate_vscode_command

        result = _generate_vscode_command("ssh dev@myhost.io -p 30001")

        assert result is not None
        assert "code --remote" in result
        assert "myhost.io" in result
        assert "ForwardAgent=yes" in result

    def test_generate_vscode_command_invalid_input(self):
        """Should return None for invalid SSH commands"""
        from gpu_dev_cli.reservations import _generate_vscode_command

        assert _generate_vscode_command("") is None
        assert _generate_vscode_command("not-ssh-command") is None


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

    def test_get_ssh_config_path(self):
        """Should return correct SSH config path"""
        from gpu_dev_cli.reservations import get_ssh_config_path

        path = get_ssh_config_path("abc12345-full-uuid")

        assert ".gpu-dev" in path
        assert "abc12345" in path
        assert "sshconfig" in path

    def test_get_ssh_config_path_uses_short_id(self):
        """Should use short ID regardless of name parameter"""
        from gpu_dev_cli.reservations import get_ssh_config_path

        path1 = get_ssh_config_path("abc12345-full-uuid", name="my-experiment")
        path2 = get_ssh_config_path("abc12345-full-uuid")

        assert "abc12345" in path1
        assert "abc12345" in path2


class TestIDELinks:
    """Tests for IDE URL generation"""

    def test_make_vscode_link(self):
        """Should generate correct VS Code remote SSH link"""
        from gpu_dev_cli.reservations import _make_vscode_link

        link = _make_vscode_link("gpu-dev-abc123")

        assert link == "vscode://vscode-remote/ssh-remote+gpu-dev-abc123/home/dev"
        assert link.startswith("vscode://")
        assert "ssh-remote" in link

    def test_make_cursor_link(self):
        """Should generate correct Cursor IDE remote SSH link"""
        from gpu_dev_cli.reservations import _make_cursor_link

        link = _make_cursor_link("gpu-dev-abc123")

        assert link == "cursor://vscode-remote/ssh-remote+gpu-dev-abc123/home/dev"
        assert link.startswith("cursor://")
        assert "ssh-remote" in link


class TestExtractIPFromReservation:
    """Tests for IP extraction from reservation data"""

    def test_extract_ip_with_node_ip_and_port(self):
        """Should return IP:Port format"""
        from gpu_dev_cli.reservations import _extract_ip_from_reservation

        reservation = {
            "node_ip": "1.2.3.4",
            "node_port": 30001,
        }

        result = _extract_ip_from_reservation(reservation)
        assert result == "1.2.3.4:30001"

    def test_extract_ip_with_only_node_ip(self):
        """Should return just IP when no port"""
        from gpu_dev_cli.reservations import _extract_ip_from_reservation

        reservation = {
            "node_ip": "1.2.3.4",
        }

        result = _extract_ip_from_reservation(reservation)
        assert result == "1.2.3.4"

    def test_extract_ip_missing_data(self):
        """Should return N/A when no IP data"""
        from gpu_dev_cli.reservations import _extract_ip_from_reservation

        assert _extract_ip_from_reservation({}) == "N/A"
        assert _extract_ip_from_reservation({"status": "active"}) == "N/A"
