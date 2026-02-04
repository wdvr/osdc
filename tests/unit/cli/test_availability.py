"""
Unit tests for gpu_dev_cli availability command

Tests:
- GPU availability fetching via API
- Display formatting
- Cluster status
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest


class TestGPUAvailabilityFetching:
    """Tests for fetching GPU availability via API"""

    def test_get_availability_calls_api(self):
        """Should call API to get GPU availability"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "t4": {
                    "gpu_type": "t4",
                    "total": 8,
                    "available": 4,
                    "in_use": 4,
                    "queued": 0,
                    "max_per_node": 4,
                },
                "h100": {
                    "gpu_type": "h100",
                    "total": 16,
                    "available": 0,
                    "in_use": 16,
                    "queued": 2,
                    "max_per_node": 8,
                },
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            assert result is not None
            mock_api_client.get_gpu_availability.assert_called_once()

    def test_availability_transforms_api_response(self):
        """Should transform API response to expected format"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "h100": {
                    "gpu_type": "h100",
                    "total": 16,
                    "available": 8,
                    "in_use": 8,
                    "queued": 4,
                    "max_per_node": 8,
                },
            },
            "timestamp": "2026-01-20T18:30:00Z",
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            assert "h100" in result
            h100 = result["h100"]
            assert h100["available"] == 8
            assert h100["total"] == 16
            assert h100["queue_length"] == 4
            assert h100["max_reservable"] == 8
            assert h100["gpus_per_instance"] == 8

    def test_availability_calculates_full_nodes(self):
        """Should calculate number of full nodes available"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "h100": {
                    "gpu_type": "h100",
                    "total": 16,
                    "available": 16,
                    "in_use": 0,
                    "queued": 0,
                    "max_per_node": 8,
                },
            },
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            # 16 available / 8 max_per_node = 2 full nodes
            assert result["h100"]["full_nodes_available"] == 2

    def test_availability_calculates_estimated_wait(self):
        """Should calculate estimated wait time from queue"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "h100": {
                    "gpu_type": "h100",
                    "total": 16,
                    "available": 0,
                    "in_use": 16,
                    "queued": 4,
                    "max_per_node": 8,
                },
            },
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            # 4 queued * 15 minutes = 60 minutes
            assert result["h100"]["estimated_wait_minutes"] == 60

    def test_availability_handles_api_error(self):
        """Should return None on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            assert result is None


class TestStaticGPUConfig:
    """Tests for static GPU configuration fallback"""

    def test_static_config_returns_known_types(self):
        """Should return static config for known GPU types"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)

            # Test A100 static config
            config = manager._get_static_gpu_config("a100", queue_length=2, estimated_wait=30)
            assert config["total"] == 16
            assert config["queue_length"] == 2
            assert config["estimated_wait_minutes"] == 30

            # Test H100 static config
            config = manager._get_static_gpu_config("h100", queue_length=0, estimated_wait=0)
            assert config["total"] == 16

            # Test T4 static config
            config = manager._get_static_gpu_config("t4", queue_length=0, estimated_wait=0)
            assert config["total"] == 8

    def test_static_config_unknown_type(self):
        """Should return zero values for unknown GPU types"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            config = manager._get_static_gpu_config("unknown-gpu", queue_length=0, estimated_wait=0)

            assert config["total"] == 0
            assert config["available"] == 0


class TestClusterStatusAPI:
    """Tests for cluster status via API"""

    def test_cluster_status_calls_api(self):
        """Should call cluster status API endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_cluster_status.return_value = {
            "total_gpus": 64,
            "available_gpus": 32,
            "in_use_gpus": 32,
            "queued_gpus": 8,
            "active_reservations": 10,
            "queued_reservations": 2,
            "pending_reservations": 1,
            "preparing_reservations": 0,
            "by_gpu_type": {},
            "timestamp": "2026-01-20T18:30:00Z",
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_cluster_status()

            mock_api_client.get_cluster_status.assert_called_once()
            assert result["total_gpus"] == 64
            assert result["available_gpus"] == 32
            assert result["active_reservations"] == 10

    def test_cluster_status_transforms_response(self):
        """Should transform API response to CLI format"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_cluster_status.return_value = {
            "total_gpus": 64,
            "available_gpus": 24,
            "in_use_gpus": 40,
            "queued_gpus": 8,
            "active_reservations": 8,
            "queued_reservations": 3,
            "pending_reservations": 1,
            "preparing_reservations": 2,
            "by_gpu_type": {
                "h100": {"available": 8, "total": 16},
                "t4": {"available": 4, "total": 8},
            },
            "timestamp": "2026-01-20T18:30:00Z",
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_cluster_status()

            assert result["reserved_gpus"] == 40
            assert result["queue_length"] == 4
            assert result["queued_gpus"] == 8
            assert result["preparing_reservations"] == 2


class TestAvailabilityMultipleGPUTypes:
    """Tests for availability across multiple GPU types"""

    def test_availability_returns_all_gpu_types(self):
        """Should return availability for all GPU types from API"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "t4": {"total": 8, "available": 4, "in_use": 4, "queued": 0, "max_per_node": 4},
                "l4": {"total": 8, "available": 8, "in_use": 0, "queued": 0, "max_per_node": 4},
                "a100": {"total": 16, "available": 8, "in_use": 8, "queued": 1, "max_per_node": 8},
                "h100": {"total": 16, "available": 0, "in_use": 16, "queued": 3, "max_per_node": 8},
                "h200": {"total": 16, "available": 16, "in_use": 0, "queued": 0, "max_per_node": 8},
                "b200": {"total": 8, "available": 8, "in_use": 0, "queued": 0, "max_per_node": 8},
            },
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            assert len(result) == 6
            assert "t4" in result
            assert "l4" in result
            assert "a100" in result
            assert "h100" in result
            assert "h200" in result
            assert "b200" in result

    def test_availability_empty_response(self):
        """Should handle empty availability response"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {},
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            assert result == {}


class TestAvailabilityCalculations:
    """Tests for availability calculations"""

    def test_running_instances_calculation(self):
        """Should calculate running instances from in_use GPUs"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "h100": {
                    "total": 16,
                    "available": 0,
                    "in_use": 16,
                    "queued": 0,
                    "max_per_node": 8,
                },
            },
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            # 16 in_use / 8 max_per_node = 2 running instances
            assert result["h100"]["running_instances"] == 2

    def test_zero_wait_when_available(self):
        """Should show zero wait time when GPUs available"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_gpu_availability.return_value = {
            "availability": {
                "t4": {
                    "total": 8,
                    "available": 4,
                    "in_use": 4,
                    "queued": 0,
                    "max_per_node": 4,
                },
            },
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_gpu_availability_by_type()

            assert result["t4"]["estimated_wait_minutes"] == 0
