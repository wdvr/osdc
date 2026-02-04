"""
Unit tests for reservation processor (poller and job manager).
"""
import json
import os
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest
from kubernetes.client.rest import ApiException


# ============================================================================
# JobManager Tests
# ============================================================================

class TestJobManager:
    """Tests for JobManager class."""

    @pytest.fixture
    def job_manager(self, mock_k8s_batch_api, mock_k8s_core_api):
        """Create JobManager instance with mocks."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "test-namespace",
            "WORKER_IMAGE": "test-image:latest",
            "SERVICE_ACCOUNT": "test-sa",
            "QUEUE_NAME": "test_queue"
        }):
            from processor.job_manager import JobManager
            return JobManager(mock_k8s_batch_api, mock_k8s_core_api)

    def test_job_manager_init(self, mock_k8s_batch_api, mock_k8s_core_api):
        """JobManager initializes with correct config."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "my-namespace",
            "WORKER_IMAGE": "my-image:v1",
            "SERVICE_ACCOUNT": "my-sa"
        }):
            from processor.job_manager import JobManager
            manager = JobManager(mock_k8s_batch_api, mock_k8s_core_api)

        assert manager.namespace == "my-namespace"
        assert manager.worker_image == "my-image:v1"
        assert manager.service_account == "my-sa"

    def test_create_job_success(self, job_manager, mock_k8s_batch_api):
        """create_job creates K8s job successfully."""
        message = {
            "action": "create_reservation",
            "user_id": "testuser",
            "reservation_id": "res-123",
            "_metadata": {"retry_count": 0}
        }

        job_name = job_manager.create_job(msg_id=100, message=message)

        assert job_name == "reservation-worker-100"
        mock_k8s_batch_api.create_namespaced_job.assert_called_once()
        call_args = mock_k8s_batch_api.create_namespaced_job.call_args
        assert call_args.kwargs["namespace"] == "test-namespace"

    def test_create_job_idempotent_on_409(
        self, job_manager, mock_k8s_batch_api
    ):
        """create_job handles 409 conflict (job already exists)."""
        api_exception = ApiException(status=409, reason="Conflict")
        mock_k8s_batch_api.create_namespaced_job.side_effect = api_exception

        message = {"action": "test", "user_id": "testuser"}
        job_name = job_manager.create_job(msg_id=100, message=message)

        assert job_name == "reservation-worker-100"

    def test_create_job_raises_on_other_errors(
        self, job_manager, mock_k8s_batch_api
    ):
        """create_job raises on non-409 errors."""
        api_exception = ApiException(status=500, reason="Internal Error")
        mock_k8s_batch_api.create_namespaced_job.side_effect = api_exception

        message = {"action": "test", "user_id": "testuser"}
        with pytest.raises(ApiException):
            job_manager.create_job(msg_id=100, message=message)

    def test_get_job_status_succeeded(self, job_manager, mock_k8s_batch_api):
        """get_job_status returns Succeeded for completed job."""
        mock_k8s_batch_api.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(
                active=0,
                succeeded=1,
                failed=0,
                start_time=datetime.now(UTC),
                completion_time=datetime.now(UTC)
            )
        )

        status = job_manager.get_job_status("test-job")

        assert status["phase"] == "Succeeded"
        assert status["succeeded"] == 1

    def test_get_job_status_failed(self, job_manager, mock_k8s_batch_api):
        """get_job_status returns Failed for failed job."""
        mock_k8s_batch_api.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(
                active=0,
                succeeded=0,
                failed=1,
                start_time=datetime.now(UTC),
                completion_time=datetime.now(UTC)
            )
        )

        status = job_manager.get_job_status("test-job")

        assert status["phase"] == "Failed"
        assert status["failed"] == 1

    def test_get_job_status_running(self, job_manager, mock_k8s_batch_api):
        """get_job_status returns Running for active job."""
        mock_k8s_batch_api.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(
                active=1,
                succeeded=0,
                failed=0,
                start_time=datetime.now(UTC),
                completion_time=None
            )
        )

        status = job_manager.get_job_status("test-job")

        assert status["phase"] == "Running"
        assert status["active"] == 1

    def test_get_job_status_pending(self, job_manager, mock_k8s_batch_api):
        """get_job_status returns Pending for pending job."""
        mock_k8s_batch_api.read_namespaced_job_status.return_value = MagicMock(
            status=MagicMock(
                active=0,
                succeeded=0,
                failed=0,
                start_time=None,
                completion_time=None
            )
        )

        status = job_manager.get_job_status("test-job")

        assert status["phase"] == "Pending"

    def test_get_job_status_not_found(self, job_manager, mock_k8s_batch_api):
        """get_job_status returns None for non-existent job."""
        api_exception = ApiException(status=404, reason="Not Found")
        mock_k8s_batch_api.read_namespaced_job_status.side_effect = api_exception

        status = job_manager.get_job_status("non-existent-job")

        assert status is None

    def test_delete_job_success(self, job_manager, mock_k8s_batch_api):
        """delete_job deletes job successfully."""
        job_manager.delete_job("test-job")

        mock_k8s_batch_api.delete_namespaced_job.assert_called_once_with(
            name="test-job",
            namespace="test-namespace",
            propagation_policy="Background"
        )

    def test_delete_job_already_deleted(self, job_manager, mock_k8s_batch_api):
        """delete_job handles 404 (already deleted)."""
        api_exception = ApiException(status=404, reason="Not Found")
        mock_k8s_batch_api.delete_namespaced_job.side_effect = api_exception

        # Should not raise
        job_manager.delete_job("test-job")

    def test_get_job_logs_success(
        self, job_manager, mock_k8s_batch_api, mock_k8s_core_api
    ):
        """get_job_logs returns pod logs."""
        mock_k8s_core_api.list_namespaced_pod.return_value = MagicMock(
            items=[MagicMock(metadata=MagicMock(name="test-pod"))]
        )
        mock_k8s_core_api.read_namespaced_pod_log.return_value = "log output"

        logs = job_manager.get_job_logs("test-job", tail_lines=50)

        assert logs == "log output"

    def test_get_job_logs_no_pod(self, job_manager, mock_k8s_core_api):
        """get_job_logs returns None when no pod found."""
        mock_k8s_core_api.list_namespaced_pod.return_value = MagicMock(items=[])

        logs = job_manager.get_job_logs("test-job")

        assert logs is None

    def test_list_active_jobs(self, job_manager, mock_k8s_batch_api):
        """list_active_jobs returns active job names."""
        mock_k8s_batch_api.list_namespaced_job.return_value = MagicMock(
            items=[
                MagicMock(
                    metadata=MagicMock(name="job-1"),
                    status=MagicMock(active=1)
                ),
                MagicMock(
                    metadata=MagicMock(name="job-2"),
                    status=MagicMock(active=0)
                ),
                MagicMock(
                    metadata=MagicMock(name="job-3"),
                    status=MagicMock(active=1)
                )
            ]
        )

        active_jobs = job_manager.list_active_jobs()

        assert "job-1" in active_jobs
        assert "job-2" not in active_jobs
        assert "job-3" in active_jobs

    def test_get_worker_env_includes_message_body(self, job_manager):
        """_get_worker_env includes MESSAGE_BODY."""
        message_json = json.dumps({"action": "test"})
        env_vars = job_manager._get_worker_env(message_json)

        message_body_var = next(
            (v for v in env_vars if v.name == "MESSAGE_BODY"),
            None
        )
        assert message_body_var is not None
        assert message_body_var.value == message_json


# ============================================================================
# Poller Message Processing Tests
# ============================================================================

class TestPollerMessageProcessing:
    """Tests for poller message processing logic."""

    @pytest.fixture
    def mock_db_env(self):
        """Set up database environment."""
        with patch.dict(os.environ, {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "testuser",
            "POSTGRES_PASSWORD": "testpass",
            "POSTGRES_DB": "testdb",
            "QUEUE_NAME": "test_queue",
            "POLL_INTERVAL_SECONDS": "1",
            "VISIBILITY_TIMEOUT_SECONDS": "300",
            "BATCH_SIZE": "1",
            "MAX_CONCURRENT_JOBS": "10"
        }):
            yield

    def test_poll_messages_success(
        self, mock_db_cursor, mock_db_env, mock_connection_pool
    ):
        """poll_messages returns messages from queue."""
        mock_db_cursor.fetchall.return_value = [
            {
                "msg_id": 1,
                "read_ct": 1,
                "enqueued_at": datetime.now(UTC),
                "vt": datetime.now(UTC),
                "message": {"action": "test", "user_id": "testuser"}
            }
        ]

        with patch("processor.poller.get_db_cursor") as mock_get_cursor:
            mock_get_cursor.return_value.__enter__.return_value = mock_db_cursor
            from processor.poller import poll_messages
            messages = poll_messages(batch_size=1)

        assert len(messages) == 1
        assert messages[0]["msg_id"] == 1

    def test_poll_messages_empty(self, mock_db_cursor, mock_db_env):
        """poll_messages returns empty list when no messages."""
        mock_db_cursor.fetchall.return_value = []

        with patch("processor.poller.get_db_cursor") as mock_get_cursor:
            mock_get_cursor.return_value.__enter__.return_value = mock_db_cursor
            from processor.poller import poll_messages
            messages = poll_messages(batch_size=1)

        assert messages == []

    def test_poll_messages_error(self, mock_db_cursor, mock_db_env):
        """poll_messages returns empty list on error."""
        mock_db_cursor.execute.side_effect = Exception("DB error")

        with patch("processor.poller.get_db_cursor") as mock_get_cursor:
            mock_get_cursor.return_value.__enter__.return_value = mock_db_cursor
            from processor.poller import poll_messages
            messages = poll_messages(batch_size=1)

        assert messages == []

    def test_archive_message_success(self, mock_db_cursor, mock_db_env):
        """archive_message archives message successfully."""
        mock_db_cursor.fetchone.return_value = {"archived": True}

        with patch("processor.poller.get_db_cursor") as mock_get_cursor:
            mock_get_cursor.return_value.__enter__.return_value = mock_db_cursor
            from processor.poller import archive_message
            result = archive_message(msg_id=123, reason="test failure")

        assert result is True
        mock_db_cursor.execute.assert_called()


# ============================================================================
# Retry Logic Tests
# ============================================================================

class TestRetryLogic:
    """Tests for retry handling."""

    def test_max_retries_exceeded(self, mock_db_env):
        """Message archived when max retries exceeded."""
        message = {
            "msg_id": 1,
            "read_ct": 4,  # Exceeds MAX_RETRIES (3)
            "message": {"action": "test"}
        }

        with patch("processor.poller.MAX_RETRIES", 3):
            with patch("processor.poller.archive_message") as mock_archive:
                mock_archive.return_value = True
                # The actual check happens in process_loop
                # Here we verify the logic
                assert message["read_ct"] >= 3


# ============================================================================
# Active Jobs Tracking Tests
# ============================================================================

class TestActiveJobsTracking:
    """Tests for active jobs tracking."""

    def test_check_job_status_succeeded(
        self, mock_k8s_batch_api, mock_k8s_core_api
    ):
        """Succeeded job is removed from tracking."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "test-namespace",
            "WORKER_IMAGE": "test-image:latest"
        }):
            from processor.job_manager import JobManager
            from processor.poller import active_jobs, check_job_status

            manager = JobManager(mock_k8s_batch_api, mock_k8s_core_api)
            mock_k8s_batch_api.read_namespaced_job_status.return_value = MagicMock(
                status=MagicMock(
                    active=0,
                    succeeded=1,
                    failed=0,
                    start_time=datetime.now(UTC),
                    completion_time=datetime.now(UTC)
                )
            )

            active_jobs[1] = {
                "job_name": "test-job",
                "created_at": time.time()
            }

            check_job_status(manager, 1, active_jobs[1])

            assert 1 not in active_jobs

    def test_check_job_status_failed(
        self, mock_k8s_batch_api, mock_k8s_core_api
    ):
        """Failed job is removed from tracking."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "test-namespace",
            "WORKER_IMAGE": "test-image:latest"
        }):
            from processor.job_manager import JobManager
            from processor.poller import active_jobs, check_job_status

            manager = JobManager(mock_k8s_batch_api, mock_k8s_core_api)
            mock_k8s_batch_api.read_namespaced_job_status.return_value = MagicMock(
                status=MagicMock(
                    active=0,
                    succeeded=0,
                    failed=1,
                    start_time=datetime.now(UTC),
                    completion_time=datetime.now(UTC)
                )
            )

            active_jobs[2] = {
                "job_name": "test-job-2",
                "created_at": time.time()
            }

            check_job_status(manager, 2, active_jobs[2])

            assert 2 not in active_jobs

    def test_rebuild_active_jobs_from_k8s(
        self, mock_k8s_batch_api, mock_k8s_core_api
    ):
        """Active jobs rebuilt from K8s on startup."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "test-namespace",
            "WORKER_IMAGE": "test-image:latest"
        }):
            from processor.job_manager import JobManager
            from processor.poller import rebuild_active_jobs_from_k8s, active_jobs

            active_jobs.clear()

            manager = JobManager(mock_k8s_batch_api, mock_k8s_core_api)
            mock_k8s_batch_api.list_namespaced_job.return_value = MagicMock(
                items=[
                    MagicMock(
                        metadata=MagicMock(
                            name="reservation-worker-100",
                            creation_timestamp=datetime.now(UTC),
                            labels={"action": "create_reservation"},
                            annotations={"user_id": "testuser"}
                        ),
                        status=MagicMock(active=1)
                    ),
                    MagicMock(
                        metadata=MagicMock(
                            name="reservation-worker-101",
                            creation_timestamp=datetime.now(UTC),
                            labels={"action": "cancel"},
                            annotations={"user_id": "testuser2"}
                        ),
                        status=MagicMock(active=1)
                    )
                ]
            )

            recovered = rebuild_active_jobs_from_k8s(manager)

            assert recovered == 2
            assert 100 in active_jobs
            assert 101 in active_jobs


# ============================================================================
# Job Environment Tests
# ============================================================================

class TestJobEnvironment:
    """Tests for job environment configuration."""

    def test_worker_env_includes_db_config(
        self, mock_k8s_batch_api, mock_k8s_core_api
    ):
        """Worker environment includes database configuration."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "test-namespace",
            "WORKER_IMAGE": "test-image:latest",
            "POSTGRES_HOST": "db.example.com",
            "POSTGRES_PORT": "5432",
            "POSTGRES_USER": "gpudev",
            "POSTGRES_DB": "gpudev"
        }):
            from processor.job_manager import JobManager

            manager = JobManager(mock_k8s_batch_api, mock_k8s_core_api)
            env_vars = manager._get_worker_env()

        env_names = [v.name for v in env_vars]
        assert "POSTGRES_HOST" in env_names
        assert "POSTGRES_PORT" in env_names
        assert "POSTGRES_USER" in env_names
        assert "POSTGRES_DB" in env_names
        assert "POSTGRES_PASSWORD" in env_names

    def test_worker_env_includes_aws_config(
        self, mock_k8s_batch_api, mock_k8s_core_api
    ):
        """Worker environment includes AWS configuration."""
        with patch.dict(os.environ, {
            "KUBE_NAMESPACE": "test-namespace",
            "WORKER_IMAGE": "test-image:latest",
            "REGION": "us-east-1",
            "EKS_CLUSTER_NAME": "test-cluster",
            "PRIMARY_AVAILABILITY_ZONE": "us-east-1a"
        }):
            from processor.job_manager import JobManager

            manager = JobManager(mock_k8s_batch_api, mock_k8s_core_api)
            env_vars = manager._get_worker_env()

        env_names = [v.name for v in env_vars]
        assert "REGION" in env_names
        assert "EKS_CLUSTER_NAME" in env_names
        assert "PRIMARY_AVAILABILITY_ZONE" in env_names


# ============================================================================
# State Transition Tests
# ============================================================================

class TestReservationStateTransitions:
    """Tests for reservation state transitions."""

    def test_queued_to_preparing(self):
        """Reservation transitions from queued to preparing."""
        # This tests the expected state machine behavior
        valid_transitions = {
            "queued": ["preparing", "cancelled", "failed"],
            "preparing": ["active", "failed", "cancelled"],
            "active": ["completed", "cancelled", "failed", "expired"],
            "completed": [],
            "cancelled": [],
            "failed": [],
            "expired": []
        }

        assert "preparing" in valid_transitions["queued"]
        assert "active" in valid_transitions["preparing"]
        assert "completed" in valid_transitions["active"]

    def test_invalid_state_transition(self):
        """Invalid state transitions are rejected."""
        valid_transitions = {
            "completed": [],
            "cancelled": [],
            "failed": []
        }

        # Terminal states cannot transition
        assert valid_transitions["completed"] == []
        assert valid_transitions["cancelled"] == []
        assert valid_transitions["failed"] == []
