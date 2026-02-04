"""
Unit tests for FastAPI API service endpoints.
"""
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


# ============================================================================
# Authentication Tests
# ============================================================================

class TestVerifyApiKey:
    """Tests for API key verification."""

    @pytest.mark.asyncio
    async def test_verify_api_key_valid(self, mock_asyncpg_pool):
        """Valid API key returns user info."""
        pool, conn = mock_asyncpg_pool
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        conn.fetchrow.return_value = {
            "user_id": 1,
            "username": "testuser",
            "email": "testuser@example.com",
            "user_active": True,
            "key_id": 123,
            "expires_at": expires_at,
            "key_active": True
        }
        conn.execute.return_value = None

        with patch("app.main.db_pool", pool):
            with patch("app.main.hash_api_key", return_value="hashedkey"):
                from app.main import verify_api_key
                credentials = MagicMock()
                credentials.credentials = "valid_api_key_12345678"
                result = await verify_api_key(credentials)

        assert result["username"] == "testuser"
        assert result["user_id"] == 1

    @pytest.mark.asyncio
    async def test_verify_api_key_expired(self, mock_asyncpg_pool):
        """Expired API key raises 403."""
        pool, conn = mock_asyncpg_pool
        expires_at = datetime.now(UTC) - timedelta(hours=1)
        conn.fetchrow.return_value = {
            "user_id": 1,
            "username": "testuser",
            "email": "testuser@example.com",
            "user_active": True,
            "key_id": 123,
            "expires_at": expires_at,
            "key_active": True
        }

        with patch("app.main.db_pool", pool):
            with patch("app.main.hash_api_key", return_value="hashedkey"):
                from app.main import verify_api_key
                credentials = MagicMock()
                credentials.credentials = "expired_api_key_123456"
                with pytest.raises(HTTPException) as exc:
                    await verify_api_key(credentials)
                assert exc.value.status_code == 403
                assert "expired" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_verify_api_key_invalid(self, mock_asyncpg_pool):
        """Invalid API key raises 401."""
        pool, conn = mock_asyncpg_pool
        conn.fetchrow.return_value = None

        with patch("app.main.db_pool", pool):
            with patch("app.main.hash_api_key", return_value="hashedkey"):
                from app.main import verify_api_key
                credentials = MagicMock()
                credentials.credentials = "invalid_api_key_123456"
                with pytest.raises(HTTPException) as exc:
                    await verify_api_key(credentials)
                assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_verify_api_key_inactive_user(self, mock_asyncpg_pool):
        """Inactive user raises 403."""
        pool, conn = mock_asyncpg_pool
        expires_at = datetime.now(UTC) + timedelta(hours=1)
        conn.fetchrow.return_value = {
            "user_id": 1,
            "username": "testuser",
            "email": "testuser@example.com",
            "user_active": False,
            "key_id": 123,
            "expires_at": expires_at,
            "key_active": True
        }

        with patch("app.main.db_pool", pool):
            with patch("app.main.hash_api_key", return_value="hashedkey"):
                from app.main import verify_api_key
                credentials = MagicMock()
                credentials.credentials = "valid_api_key_12345678"
                with pytest.raises(HTTPException) as exc:
                    await verify_api_key(credentials)
                assert exc.value.status_code == 403
                assert "disabled" in exc.value.detail.lower()


# ============================================================================
# Health Check Tests
# ============================================================================

class TestHealthEndpoint:
    """Tests for /health endpoint."""

    @pytest.mark.asyncio
    async def test_health_check_healthy(self, mock_asyncpg_pool):
        """Health check returns healthy when DB and queue are OK."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 1
        conn.fetch.return_value = [{"queue_name": "gpu_reservations"}]

        with patch("app.main.db_pool", pool):
            with patch("app.main.QUEUE_NAME", "gpu_reservations"):
                from app.main import health_check
                result = await health_check()

        assert result["status"] == "healthy"
        assert result["database"] == "healthy"
        assert result["queue"] == "healthy"

    @pytest.mark.asyncio
    async def test_health_check_missing_queue(self, mock_asyncpg_pool):
        """Health check returns unhealthy when queue is missing."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 1
        conn.fetch.return_value = [{"queue_name": "other_queue"}]

        with patch("app.main.db_pool", pool):
            with patch("app.main.QUEUE_NAME", "gpu_reservations"):
                from app.main import health_check
                result = await health_check()

        assert result["status"] == "unhealthy"
        assert result["database"] == "healthy"
        assert result["queue"] == "missing"

    @pytest.mark.asyncio
    async def test_health_check_db_not_initialized(self):
        """Health check returns unhealthy when DB pool is None."""
        with patch("app.main.db_pool", None):
            from app.main import health_check
            result = await health_check()

        assert result["status"] == "unhealthy"
        assert result["database"] == "not initialized"


# ============================================================================
# Job Submission Tests
# ============================================================================

class TestSubmitJobEndpoint:
    """Tests for POST /v1/jobs/submit endpoint."""

    @pytest.mark.asyncio
    async def test_submit_job_success(self, mock_asyncpg_pool, sample_user_info):
        """Submitting a job returns queued status."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 123  # msg_id

        with patch("app.main.db_pool", pool):
            with patch("app.main.QUEUE_NAME", "gpu_reservations"):
                from app.main import submit_job, JobSubmissionRequest
                request = JobSubmissionRequest(
                    image="pytorch/pytorch:2.1.0",
                    instance_type="p4d.24xlarge",
                    duration_hours=4
                )
                user_info = sample_user_info()
                result = await submit_job(request, user_info)

        assert result.status == "queued"
        assert "123" in result.message

    @pytest.mark.asyncio
    async def test_submit_job_with_existing_reservation_id(
        self, mock_asyncpg_pool, sample_user_info
    ):
        """Job uses reservation_id from env_vars if provided."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 456

        existing_id = str(uuid.uuid4())
        with patch("app.main.db_pool", pool):
            with patch("app.main.QUEUE_NAME", "gpu_reservations"):
                from app.main import submit_job, JobSubmissionRequest
                request = JobSubmissionRequest(
                    image="pytorch/pytorch:2.1.0",
                    instance_type="p4d.24xlarge",
                    duration_hours=4,
                    env_vars={"RESERVATION_ID": existing_id}
                )
                user_info = sample_user_info()
                result = await submit_job(request, user_info)

        assert result.job_id == existing_id


# ============================================================================
# Get Job Tests
# ============================================================================

class TestGetJobEndpoint:
    """Tests for GET /v1/jobs/{job_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_job_success(self, mock_asyncpg_pool, sample_user_info):
        """Get job returns job details."""
        pool, conn = mock_asyncpg_pool
        job_id = str(uuid.uuid4())
        conn.fetchrow.return_value = {
            "reservation_id": job_id,
            "user_id": "testuser",
            "status": "active",
            "gpu_type": "a100",
            "gpu_count": 4,
            "instance_type": "p4d.24xlarge",
            "duration_hours": 4,
            "created_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=4),
            "name": "test-pod",
            "pod_name": "gpu-dev-test",
            "node_ip": "10.0.0.1",
            "node_port": 30001,
            "jupyter_enabled": False,
            "jupyter_url": None,
            "jupyter_token": None,
            "github_user": "testghuser"
        }

        with patch("app.main.db_pool", pool):
            from app.main import get_job_status
            user_info = sample_user_info()
            result = await get_job_status(job_id, user_info)

        assert result.job_id == job_id
        assert result.status == "active"
        assert result.gpu_count == 4

    @pytest.mark.asyncio
    async def test_get_job_not_found(self, mock_asyncpg_pool, sample_user_info):
        """Get non-existent job raises 404."""
        pool, conn = mock_asyncpg_pool
        conn.fetchrow.return_value = None

        with patch("app.main.db_pool", pool):
            from app.main import get_job_status
            with pytest.raises(HTTPException) as exc:
                await get_job_status("non-existent-id", sample_user_info())
            assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_get_job_unauthorized(self, mock_asyncpg_pool, sample_user_info):
        """Get another user's job raises 403."""
        pool, conn = mock_asyncpg_pool
        job_id = str(uuid.uuid4())
        conn.fetchrow.return_value = {
            "reservation_id": job_id,
            "user_id": "otheruser",  # Different user
            "status": "active",
            "gpu_type": "a100",
            "gpu_count": 4,
            "instance_type": "p4d.24xlarge",
            "duration_hours": 4,
            "created_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=4),
            "name": "test-pod",
            "pod_name": "gpu-dev-test",
            "node_ip": "10.0.0.1",
            "node_port": 30001,
            "jupyter_enabled": False,
            "jupyter_url": None,
            "jupyter_token": None,
            "github_user": "testghuser"
        }

        with patch("app.main.db_pool", pool):
            from app.main import get_job_status
            with pytest.raises(HTTPException) as exc:
                await get_job_status(job_id, sample_user_info())
            assert exc.value.status_code == 403


# ============================================================================
# List Jobs Tests
# ============================================================================

class TestListJobsEndpoint:
    """Tests for GET /v1/jobs endpoint."""

    @pytest.mark.asyncio
    async def test_list_jobs_success(self, mock_asyncpg_pool, sample_user_info):
        """List jobs returns user's jobs."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 2  # total count
        conn.fetch.return_value = [
            {
                "reservation_id": str(uuid.uuid4()),
                "user_id": "testuser",
                "status": "active",
                "gpu_type": "a100",
                "gpu_count": 4,
                "instance_type": "p4d.24xlarge",
                "duration_hours": 4,
                "created_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(hours=4),
                "name": "test-pod-1",
                "pod_name": "gpu-dev-test-1",
                "node_ip": "10.0.0.1",
                "node_port": 30001,
                "jupyter_enabled": False,
                "jupyter_url": None,
                "jupyter_token": None,
                "github_user": "testghuser"
            },
            {
                "reservation_id": str(uuid.uuid4()),
                "user_id": "testuser",
                "status": "queued",
                "gpu_type": "h100",
                "gpu_count": 8,
                "instance_type": "p5.48xlarge",
                "duration_hours": 2,
                "created_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(hours=2),
                "name": "test-pod-2",
                "pod_name": None,
                "node_ip": None,
                "node_port": None,
                "jupyter_enabled": False,
                "jupyter_url": None,
                "jupyter_token": None,
                "github_user": "testghuser"
            }
        ]

        with patch("app.main.db_pool", pool):
            from app.main import list_jobs
            result = await list_jobs(sample_user_info())

        assert result.total == 2
        assert len(result.jobs) == 2
        assert result.jobs[0].status == "active"

    @pytest.mark.asyncio
    async def test_list_jobs_with_status_filter(
        self, mock_asyncpg_pool, sample_user_info
    ):
        """List jobs with status filter returns filtered results."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 1
        conn.fetch.return_value = [
            {
                "reservation_id": str(uuid.uuid4()),
                "user_id": "testuser",
                "status": "active",
                "gpu_type": "a100",
                "gpu_count": 4,
                "instance_type": "p4d.24xlarge",
                "duration_hours": 4,
                "created_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(hours=4),
                "name": "test-pod",
                "pod_name": "gpu-dev-test",
                "node_ip": "10.0.0.1",
                "node_port": 30001,
                "jupyter_enabled": False,
                "jupyter_url": None,
                "jupyter_token": None,
                "github_user": "testghuser"
            }
        ]

        with patch("app.main.db_pool", pool):
            from app.main import list_jobs
            result = await list_jobs(sample_user_info(), status_filter="active")

        assert result.total == 1
        assert result.jobs[0].status == "active"


# ============================================================================
# Cancel Job Tests
# ============================================================================

class TestCancelJobEndpoint:
    """Tests for POST /v1/jobs/{job_id}/cancel endpoint."""

    @pytest.mark.asyncio
    async def test_cancel_job_success(self, mock_asyncpg_pool, sample_user_info):
        """Cancel job sends message to queue."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 789  # msg_id

        with patch("app.main.db_pool", pool):
            with patch("app.main.QUEUE_NAME", "gpu_reservations"):
                from app.main import cancel_job
                job_id = str(uuid.uuid4())
                result = await cancel_job(job_id, sample_user_info())

        assert result.action == "cancel"
        assert result.status == "requested"
        assert result.job_id == job_id
        conn.fetchval.assert_called_once()


# ============================================================================
# Extend Job Tests
# ============================================================================

class TestExtendJobEndpoint:
    """Tests for POST /v1/jobs/{job_id}/extend endpoint."""

    @pytest.mark.asyncio
    async def test_extend_job_success(self, mock_asyncpg_pool, sample_user_info):
        """Extend job sends message to queue."""
        pool, conn = mock_asyncpg_pool
        conn.fetchval.return_value = 101  # msg_id

        with patch("app.main.db_pool", pool):
            with patch("app.main.QUEUE_NAME", "gpu_reservations"):
                from app.main import extend_job, ExtendJobRequest
                job_id = str(uuid.uuid4())
                request = ExtendJobRequest(extension_hours=2)
                result = await extend_job(job_id, request, sample_user_info())

        assert result.action == "extend"
        assert result.status == "requested"
        assert "2 hours" in result.message


# ============================================================================
# GPU Availability Tests
# ============================================================================

class TestGPUAvailabilityEndpoint:
    """Tests for GET /v1/gpu/availability endpoint."""

    @pytest.mark.asyncio
    async def test_get_gpu_availability_success(
        self, mock_asyncpg_pool, sample_user_info
    ):
        """Get GPU availability returns availability data."""
        pool, conn = mock_asyncpg_pool
        # GPU config query
        gpu_config = [
            {"gpu_type": "a100", "total_cluster_gpus": 16, "max_per_node": 8},
            {"gpu_type": "h100", "total_cluster_gpus": 16, "max_per_node": 8}
        ]
        # In-use query
        in_use = [{"gpu_type": "a100", "count": 4}]
        # Queued query
        queued = [{"gpu_type": "h100", "count": 8}]

        conn.fetch.side_effect = [gpu_config, in_use, queued]

        with patch("app.main.db_pool", pool):
            from app.main import get_gpu_availability
            result = await get_gpu_availability(sample_user_info())

        assert "a100" in result.availability
        assert result.availability["a100"].total == 16
        assert result.availability["a100"].in_use == 4
        assert result.availability["a100"].available == 12

    @pytest.mark.asyncio
    async def test_get_gpu_availability_empty(
        self, mock_asyncpg_pool, sample_user_info
    ):
        """Get GPU availability returns empty when no GPU config."""
        pool, conn = mock_asyncpg_pool
        conn.fetch.return_value = []  # No GPU config

        with patch("app.main.db_pool", pool):
            from app.main import get_gpu_availability
            result = await get_gpu_availability(sample_user_info())

        assert result.availability == {}


# ============================================================================
# AWS Login Tests
# ============================================================================

class TestAWSLoginEndpoint:
    """Tests for POST /v1/auth/aws-login endpoint."""

    @pytest.mark.asyncio
    async def test_aws_login_success(self, mock_asyncpg_pool, mock_sts_client):
        """AWS login with valid credentials returns API key."""
        pool, conn = mock_asyncpg_pool
        # User lookup (not found -> create new)
        conn.fetchrow.side_effect = [None, {"user_id": 1}]
        conn.fetchval.return_value = 1  # user_id after insert

        with patch("app.main.db_pool", pool):
            with patch("app.main.verify_aws_credentials") as mock_verify:
                mock_verify.return_value = {
                    "account": "123456789012",
                    "user_id": "AIDAEXAMPLE",
                    "arn": "arn:aws:sts::123456789012:assumed-role/SSOCloudDevGpuReservation/testuser"
                }
                with patch("app.main.ALLOWED_AWS_ROLE", "SSOCloudDevGpuReservation"):
                    with patch("app.main.create_api_key_for_user") as mock_create_key:
                        mock_create_key.return_value = (
                            "test_api_key",
                            "test_pre",
                            datetime.now(UTC) + timedelta(hours=2)
                        )
                        from app.main import aws_login, AWSLoginRequest
                        request = AWSLoginRequest(
                            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
                            aws_session_token="session_token_123" * 10
                        )
                        result = await aws_login(request)

        assert result.api_key == "test_api_key"
        assert "testuser" in result.aws_arn

    @pytest.mark.asyncio
    async def test_aws_login_wrong_role(self, mock_asyncpg_pool):
        """AWS login with wrong role raises 403."""
        pool, conn = mock_asyncpg_pool

        with patch("app.main.db_pool", pool):
            with patch("app.main.verify_aws_credentials") as mock_verify:
                mock_verify.return_value = {
                    "account": "123456789012",
                    "user_id": "AIDAEXAMPLE",
                    "arn": "arn:aws:sts::123456789012:assumed-role/WrongRole/testuser"
                }
                with patch("app.main.ALLOWED_AWS_ROLE", "SSOCloudDevGpuReservation"):
                    from app.main import aws_login, AWSLoginRequest
                    request = AWSLoginRequest(
                        aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
                        aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
                    )
                    with pytest.raises(HTTPException) as exc:
                        await aws_login(request)
                    assert exc.value.status_code == 403


# ============================================================================
# Helper Function Tests
# ============================================================================

class TestHelperFunctions:
    """Tests for helper functions."""

    def test_extract_username_from_arn_assumed_role(self):
        """Extract username from assumed-role ARN."""
        from app.main import extract_username_from_arn
        arn = "arn:aws:sts::123456789:assumed-role/SSOCloudDevGpuReservation/john"
        assert extract_username_from_arn(arn) == "john"

    def test_extract_username_from_arn_iam_user(self):
        """Extract username from IAM user ARN."""
        from app.main import extract_username_from_arn
        arn = "arn:aws:iam::123456789:user/jane"
        assert extract_username_from_arn(arn) == "jane"

    def test_extract_role_from_arn_assumed_role(self):
        """Extract role name from assumed-role ARN."""
        from app.main import extract_role_from_arn
        arn = "arn:aws:sts::123456789:assumed-role/SSOCloudDevGpuReservation/john"
        assert extract_role_from_arn(arn) == "SSOCloudDevGpuReservation"

    def test_extract_role_from_arn_user(self):
        """Extract role name from user ARN returns empty."""
        from app.main import extract_role_from_arn
        arn = "arn:aws:iam::123456789:user/john"
        assert extract_role_from_arn(arn) == ""

    def test_hash_api_key(self):
        """Hash API key produces consistent hash."""
        from app.main import hash_api_key
        key = "test_api_key_12345"
        hash1 = hash_api_key(key)
        hash2 = hash_api_key(key)
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex

    def test_ensure_utc_naive_datetime(self):
        """ensure_utc adds UTC timezone to naive datetime."""
        from app.main import ensure_utc
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = ensure_utc(naive)
        assert result.tzinfo is not None

    def test_ensure_utc_none(self):
        """ensure_utc returns None for None input."""
        from app.main import ensure_utc
        assert ensure_utc(None) is None

    def test_create_message_metadata(self):
        """create_message_metadata returns proper structure."""
        from app.main import create_message_metadata
        metadata = create_message_metadata(max_retries=5)
        assert metadata["retry_count"] == 0
        assert metadata["max_retries"] == 5
        assert "created_at" in metadata
