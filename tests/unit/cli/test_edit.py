"""
Unit tests for gpu_dev_cli edit command

Tests:
- Extend reservation (duration extension)
- Add user (collaborator)
- Enable/disable Jupyter
- Cancel reservation
- List reservations
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest


class TestExtendReservation:
    """Tests for extending reservation duration via API"""

    def test_extend_calls_api(self):
        """Should call API extend endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.extend_job.return_value = {"status": "extended"}
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=8)).isoformat(),
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            with patch("gpu_dev_cli.reservations.Live"):
                from gpu_dev_cli.reservations import ReservationManager

                manager = ReservationManager(mock_config)
                manager.extend_reservation(
                    reservation_id="res-123",
                    user_id="test-user",
                    extension_hours=4,
                )

                mock_api_client.extend_job.assert_called_once_with("res-123", 4)

    def test_extend_converts_float_to_int(self):
        """Should convert extension hours to int for API"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.extend_job.return_value = {"status": "extended"}
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (datetime.now(timezone.utc) + timedelta(hours=12)).isoformat(),
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            with patch("gpu_dev_cli.reservations.Live"):
                from gpu_dev_cli.reservations import ReservationManager

                manager = ReservationManager(mock_config)
                manager.extend_reservation(
                    reservation_id="res-123",
                    user_id="test-user",
                    extension_hours=4.5,
                )

                mock_api_client.extend_job.assert_called_once_with("res-123", 4)

    def test_extend_handles_api_error(self):
        """Should return False on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.extend_job.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.extend_reservation(
                reservation_id="res-123",
                user_id="test-user",
                extension_hours=4,
            )

            assert result is False


class TestAddUser:
    """Tests for adding collaborators via API"""

    def test_add_user_calls_api(self):
        """Should call API add user endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.add_user.return_value = {"status": "added"}
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123",
            "status": "active",
            "secondary_users": ["newuser"],
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            with patch("gpu_dev_cli.reservations.Live"):
                from gpu_dev_cli.reservations import ReservationManager

                manager = ReservationManager(mock_config)
                manager.add_user(
                    reservation_id="res-123",
                    user_id="test-user",
                    github_username="newuser",
                )

                mock_api_client.add_user.assert_called_once_with("res-123", "newuser")

    def test_add_user_validates_username_format(self):
        """Should reject invalid GitHub usernames"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)

            # Empty username
            result = manager.add_user("res-123", "test-user", "")
            assert result is False

            # Username with spaces
            result = manager.add_user("res-123", "test-user", "user with spaces")
            assert result is False

            mock_api_client.add_user.assert_not_called()

    def test_add_user_allows_valid_usernames(self):
        """Should allow valid GitHub username formats"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.add_user.return_value = {"status": "added"}
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123",
            "status": "active",
            "secondary_users": ["valid-user"],
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            with patch("gpu_dev_cli.reservations.Live"):
                from gpu_dev_cli.reservations import ReservationManager

                manager = ReservationManager(mock_config)

                manager.add_user("res-123", "test-user", "valid-user")
                mock_api_client.add_user.assert_called_with("res-123", "valid-user")

                manager.add_user("res-123", "test-user", "user_name")
                mock_api_client.add_user.assert_called_with("res-123", "user_name")

                manager.add_user("res-123", "test-user", "user123")
                mock_api_client.add_user.assert_called_with("res-123", "user123")

    def test_add_user_handles_api_error(self):
        """Should return False on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.add_user.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.add_user(
                reservation_id="res-123",
                user_id="test-user",
                github_username="newuser",
            )

            assert result is False


class TestJupyterToggle:
    """Tests for enabling/disabling Jupyter"""

    def test_enable_jupyter_calls_api(self):
        """Should call API enable jupyter endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.enable_jupyter.return_value = {"status": "enabled"}
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123",
            "status": "active",
            "jupyter_enabled": True,
            "jupyter_url": "http://1.2.3.4:30888",
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            with patch("gpu_dev_cli.reservations.Live"):
                from gpu_dev_cli.reservations import ReservationManager

                manager = ReservationManager(mock_config)
                manager.enable_jupyter(
                    reservation_id="res-123",
                    user_id="test-user",
                )

                mock_api_client.enable_jupyter.assert_called_once_with("res-123")

    def test_disable_jupyter_calls_api(self):
        """Should call API disable jupyter endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.disable_jupyter.return_value = {"status": "disabled"}
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123",
            "status": "active",
            "jupyter_enabled": False,
            "jupyter_url": "",
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            with patch("gpu_dev_cli.reservations.Live"):
                from gpu_dev_cli.reservations import ReservationManager

                manager = ReservationManager(mock_config)
                manager.disable_jupyter(
                    reservation_id="res-123",
                    user_id="test-user",
                )

                mock_api_client.disable_jupyter.assert_called_once_with("res-123")

    def test_enable_jupyter_handles_api_error(self):
        """Should return False on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.enable_jupyter.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.enable_jupyter(
                reservation_id="res-123",
                user_id="test-user",
            )

            assert result is False


class TestCancelReservation:
    """Tests for cancelling reservations"""

    def test_cancel_calls_api(self):
        """Should call API cancel endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.cancel_job.return_value = {"status": "cancelled"}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.cancel_reservation(
                reservation_id="res-123",
                user_id="test-user",
            )

            assert result is True
            mock_api_client.cancel_job.assert_called_once_with("res-123")

    def test_cancel_handles_api_error(self):
        """Should return False on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.cancel_job.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.cancel_reservation(
                reservation_id="res-123",
                user_id="test-user",
            )

            assert result is False


class TestListReservations:
    """Tests for listing reservations"""

    def test_list_calls_api(self):
        """Should call API list jobs endpoint"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.list_jobs.return_value = {
            "jobs": [
                {"reservation_id": "res-1", "status": "active"},
                {"reservation_id": "res-2", "status": "pending"},
            ],
            "total": 2,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.list_reservations()

            assert len(result) == 2
            mock_api_client.list_jobs.assert_called_once()

    def test_list_with_status_filter(self):
        """Should pass status filter to API"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.list_jobs.return_value = {
            "jobs": [{"reservation_id": "res-1", "status": "active"}],
            "total": 1,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            manager.list_reservations(statuses_to_include=["active", "pending"])

            mock_api_client.list_jobs.assert_called_once_with(
                status_filter="active,pending",
                limit=500,
                offset=0,
            )

    def test_list_handles_api_error(self):
        """Should return empty list on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.list_jobs.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.list_reservations()

            assert result == []


class TestGetConnectionInfo:
    """Tests for getting connection info"""

    def test_get_connection_info_by_id(self):
        """Should get job details by full ID"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.return_value = {
            "reservation_id": "res-123-full-uuid",
            "status": "active",
            "node_ip": "1.2.3.4",
            "node_port": 30001,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="res-123-full-uuid",
                user_id="test-user",
            )

            assert result is not None
            assert result["node_ip"] == "1.2.3.4"
            mock_api_client.get_job_status.assert_called_with("res-123-full-uuid")

    def test_get_connection_info_by_prefix(self):
        """Should find job by prefix when exact ID fails"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.side_effect = RuntimeError("not found")
        mock_api_client.list_jobs.return_value = {
            "jobs": [
                {"reservation_id": "abc12345-full-uuid", "job_id": "abc12345-full-uuid", "status": "active"},
            ],
            "total": 1,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="abc12345",
                user_id="test-user",
            )

            assert result is not None
            assert result["reservation_id"] == "abc12345-full-uuid"

    def test_get_connection_info_handles_not_found(self):
        """Should return None when not found"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_job_status.side_effect = RuntimeError("not found")
        mock_api_client.list_jobs.return_value = {"jobs": [], "total": 0}

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_connection_info(
                reservation_id="nonexistent",
                user_id="test-user",
            )

            assert result is None


class TestClusterStatus:
    """Tests for cluster status retrieval"""

    def test_get_cluster_status(self):
        """Should call API and transform response"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_cluster_status.return_value = {
            "total_gpus": 64,
            "available_gpus": 32,
            "in_use_gpus": 24,
            "queued_gpus": 8,
            "active_reservations": 5,
            "queued_reservations": 2,
            "pending_reservations": 0,
            "preparing_reservations": 1,
        }

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_cluster_status()

            assert result is not None
            assert result["total_gpus"] == 64
            assert result["available_gpus"] == 32
            assert result["reserved_gpus"] == 24
            assert result["queue_length"] == 2

    def test_get_cluster_status_handles_error(self):
        """Should return None on API error"""
        mock_config = MagicMock()
        mock_api_client = MagicMock()
        mock_api_client.get_cluster_status.side_effect = Exception("API error")

        with patch("gpu_dev_cli.reservations.APIClient", return_value=mock_api_client):
            from gpu_dev_cli.reservations import ReservationManager

            manager = ReservationManager(mock_config)
            result = manager.get_cluster_status()

            assert result is None
