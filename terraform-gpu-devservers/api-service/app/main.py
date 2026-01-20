"""
GPU Dev API Service
Provides REST API for job submission using PGMQ (Postgres Message Queue)
"""
import hashlib
import json
import os
import re
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import aioboto3
import asyncpg
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Query, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

# Configuration from environment
# Build DATABASE_URL from components (or use pre-built URL)
if os.getenv("DATABASE_URL"):
    DATABASE_URL = os.getenv("DATABASE_URL")
else:
    # Build from individual components
    POSTGRES_HOST = os.getenv("POSTGRES_HOST", "postgres-primary.gpu-controlplane.svc.cluster.local")
    POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
    POSTGRES_USER = os.getenv("POSTGRES_USER", "gpudev")
    POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "CHANGEME")
    POSTGRES_DB = os.getenv("POSTGRES_DB", "gpudev")
    
    DATABASE_URL = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )
API_KEY_LENGTH = 64
QUEUE_NAME = os.getenv("QUEUE_NAME", "gpu_reservations")
DISK_QUEUE_NAME = os.getenv("DISK_QUEUE_NAME", "disk_operations")

# Parse and validate API_KEY_TTL_HOURS with error handling
try:
    API_KEY_TTL_HOURS = int(os.getenv("API_KEY_TTL_HOURS", "2"))
    if API_KEY_TTL_HOURS < 1 or API_KEY_TTL_HOURS > 168:
        raise ValueError(
            f"API_KEY_TTL_HOURS must be between 1-168 hours, "
            f"got {API_KEY_TTL_HOURS}"
        )
except ValueError as e:
    raise ValueError(
        f"Invalid API_KEY_TTL_HOURS environment variable: {e}"
    ) from e

ALLOWED_AWS_ROLE = os.getenv(
    "ALLOWED_AWS_ROLE", "SSOCloudDevGpuReservation"
)
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Validate queue names (alphanumeric and underscore only)
if not re.match(r'^[a-zA-Z0-9_]+$', QUEUE_NAME):
    raise ValueError(
        f"Invalid queue name: {QUEUE_NAME}. "
        f"Must contain only alphanumeric characters and underscores."
    )
if not re.match(r'^[a-zA-Z0-9_]+$', DISK_QUEUE_NAME):
    raise ValueError(
        f"Invalid disk queue name: {DISK_QUEUE_NAME}. "
        f"Must contain only alphanumeric characters and underscores."
    )

# Global connection pool
db_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage database connection pool lifecycle"""
    global db_pool
    # Startup
    db_pool = await asyncpg.create_pool(
        DATABASE_URL,
        min_size=2,
        max_size=10,
        command_timeout=60
    )

    # Initialize database schema and PGMQ queue
    async with db_pool.acquire() as conn:
        # Create users table if not exists
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS api_users (
                user_id SERIAL PRIMARY KEY,
                username VARCHAR(255) UNIQUE NOT NULL,
                email VARCHAR(255),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT true
            )
        """)

        # Create API keys table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_id SERIAL PRIMARY KEY,
                user_id INTEGER REFERENCES api_users(user_id)
                    ON DELETE CASCADE,
                key_hash VARCHAR(128) NOT NULL UNIQUE,
                key_prefix VARCHAR(16) NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP WITH TIME ZONE,
                last_used_at TIMESTAMP WITH TIME ZONE,
                is_active BOOLEAN DEFAULT true,
                description TEXT
            )
        """)

        # Create indexes for faster lookups
        # Index on api_keys.key_hash (for API key verification)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash
            ON api_keys(key_hash)
            WHERE is_active = true
        """)

        # Index on api_keys.user_id (for listing user's keys)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_keys_user_id
            ON api_keys(user_id)
            WHERE is_active = true
        """)

        # Index on api_keys.expires_at (for cleanup queries)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_keys_expires_at
            ON api_keys(expires_at)
            WHERE is_active = true AND expires_at IS NOT NULL
        """)

        # Index on api_users.username (for login lookups)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_users_username
            ON api_users(username)
        """)

        # Create reservations table if not exists (MUST be before disks due to FK)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reservations (
                reservation_id VARCHAR(255) PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                status VARCHAR(50) NOT NULL,
                gpu_type VARCHAR(50),
                gpu_count INTEGER,
                instance_type VARCHAR(100),
                duration_hours FLOAT NOT NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL,
                launched_at TIMESTAMP WITH TIME ZONE,
                expires_at TIMESTAMP WITH TIME ZONE,
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                name VARCHAR(255),
                github_user VARCHAR(255),
                pod_name VARCHAR(255),
                namespace VARCHAR(100) DEFAULT 'default',
                node_ip VARCHAR(50),
                node_port INTEGER,
                ssh_command TEXT,
                jupyter_enabled BOOLEAN DEFAULT FALSE,
                jupyter_url TEXT,
                jupyter_port INTEGER,
                jupyter_token VARCHAR(255),
                jupyter_error TEXT,
                ebs_volume_id VARCHAR(255),
                disk_name VARCHAR(255),
                failure_reason TEXT,
                current_detailed_status TEXT,
                status_history JSONB DEFAULT '[]'::jsonb,
                pod_logs TEXT,
                warning TEXT,
                secondary_users JSONB DEFAULT '[]'::jsonb,
                is_multinode BOOLEAN DEFAULT FALSE,
                master_reservation_id VARCHAR(255),
                node_index INTEGER,
                total_nodes INTEGER,
                cli_version VARCHAR(50)
            )
        """)

        # Create indexes for reservations table
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_user_id
            ON reservations(user_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_user_status
            ON reservations(user_id, status)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_status
            ON reservations(status)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_gpu_type_status
            ON reservations(gpu_type, status)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_created_at
            ON reservations(created_at DESC)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_expires_at
            ON reservations(expires_at)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_reservations_master_id
            ON reservations(master_reservation_id)
            WHERE master_reservation_id IS NOT NULL
        """)

        # Create trigger function for reservations updated_at
        await conn.execute("""
            CREATE OR REPLACE FUNCTION update_reservations_updated_at()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.updated_at = NOW();
                RETURN NEW;
            END;
            $$ LANGUAGE plpgsql
        """)

        # Create trigger for reservations
        await conn.execute("""
            DROP TRIGGER IF EXISTS trigger_reservations_updated_at ON reservations
        """)
        await conn.execute("""
            CREATE TRIGGER trigger_reservations_updated_at
            BEFORE UPDATE ON reservations
            FOR EACH ROW
            EXECUTE FUNCTION update_reservations_updated_at()
        """)

        # Create disks table if not exists (AFTER reservations due to FK)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS disks (
                disk_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                disk_name TEXT NOT NULL,
                user_id TEXT NOT NULL,
                size_gb INTEGER,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                last_used TIMESTAMP WITH TIME ZONE,
                in_use BOOLEAN DEFAULT FALSE,
                reservation_id VARCHAR(255) REFERENCES reservations(reservation_id) ON DELETE SET NULL,
                is_backing_up BOOLEAN DEFAULT FALSE,
                is_deleted BOOLEAN DEFAULT FALSE,
                delete_date DATE,
                snapshot_count INTEGER DEFAULT 0,
                pending_snapshot_count INTEGER DEFAULT 0,
                ebs_volume_id TEXT,
                last_snapshot_at TIMESTAMP WITH TIME ZONE,
                operation_id UUID,
                operation_status TEXT,
                operation_error TEXT,
                latest_snapshot_content_s3 TEXT,
                last_updated TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                UNIQUE(user_id, disk_name)
            )
        """)

        # Create indexes for disks table
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_disks_user_id ON disks (user_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_disks_in_use
            ON disks (in_use) WHERE in_use = true
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_disks_is_deleted
            ON disks (is_deleted) WHERE is_deleted = true
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_disks_operation_id
            ON disks (operation_id) WHERE operation_id IS NOT NULL
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_disks_reservation_id
            ON disks (reservation_id) WHERE reservation_id IS NOT NULL
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_disks_delete_date
            ON disks (delete_date) WHERE delete_date IS NOT NULL
        """)

        # Create trigger function for disks table
        await conn.execute("""
            CREATE OR REPLACE FUNCTION update_disks_last_updated_column()
            RETURNS TRIGGER AS $$
            BEGIN
                NEW.last_updated = NOW();
                RETURN NEW;
            END;
            $$ language 'plpgsql'
        """)

        # Create trigger for disks table
        await conn.execute("""
            DROP TRIGGER IF EXISTS update_disks_last_updated ON disks
        """)
        await conn.execute("""
            CREATE TRIGGER update_disks_last_updated
            BEFORE UPDATE ON disks
            FOR EACH ROW
            EXECUTE FUNCTION update_disks_last_updated_column()
        """)

        # Create PGMQ queues if not exists
        # Queue names are validated at startup (alphanumeric + underscore only)
        # PGMQ functions require queue name as a string parameter, not an identifier
        try:
            await conn.execute("SELECT pgmq.create($1)", QUEUE_NAME)
        except asyncpg.exceptions.DuplicateObjectError:
            # Queue already exists, that's fine
            pass
        
        try:
            await conn.execute("SELECT pgmq.create($1)", DISK_QUEUE_NAME)
        except asyncpg.exceptions.DuplicateObjectError:
            # Queue already exists, that's fine
            pass

    yield

    # Shutdown
    await db_pool.close()


app = FastAPI(
    title="GPU Dev API",
    description="API for submitting GPU development job reservations",
    version="1.0.0",
    lifespan=lifespan
)

# Security and dependency injection
security = HTTPBearer()
security_scheme = Security(security)


# ============================================================================
# Pydantic Models
# ============================================================================

class JobSubmissionRequest(BaseModel):
    """Request model for job submission"""
    image: str = Field(..., description="Docker image to run")
    instance_type: str = Field(
        ..., description="EC2 instance type (e.g., p5.48xlarge)"
    )
    duration_hours: int = Field(
        1, ge=1, le=72, description="Duration in hours (1-72)"
    )
    disk_name: str | None = Field(
        None, description="Named disk to attach"
    )
    disk_size_gb: int | None = Field(
        None, ge=10, le=10000, description="New disk size in GB"
    )
    env_vars: dict | None = Field(
        default_factory=dict, description="Environment variables"
    )
    command: str | None = Field(None, description="Command to run")

    class Config:
        json_schema_extra = {
            "example": {
                "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
                "instance_type": "p5.48xlarge",
                "duration_hours": 4,
                "disk_name": "my-training-data",
                "env_vars": {"WANDB_API_KEY": "secret"},
                "command": "python train.py"
            }
        }


class JobSubmissionResponse(BaseModel):
    """Response model for job submission"""
    job_id: str = Field(..., description="Unique job ID")
    status: str = Field(..., description="Submission status")
    message: str = Field(
        ..., description="Human-readable message"
    )
    estimated_start_time: str | None = None


class JobActionResponse(BaseModel):
    """Response model for job actions (cancel, extend, etc.)"""
    job_id: str = Field(..., description="Job/Reservation ID")
    action: str = Field(..., description="Action performed")
    status: str = Field(..., description="Action status")
    message: str = Field(..., description="Human-readable message")


class ExtendJobRequest(BaseModel):
    """Request model for extending job duration"""
    extension_hours: int = Field(
        ..., ge=1, le=72, description="Hours to extend (1-72)"
    )


class AddUserRequest(BaseModel):
    """Request model for adding user to job"""
    github_username: str = Field(
        ..., description="GitHub username for SSH key retrieval"
    )


class DiskCreateRequest(BaseModel):
    """Request model for creating a disk"""
    disk_name: str = Field(..., description="Name of the disk to create")
    size_gb: int | None = Field(None, description="Disk size in GB (optional, uses default if not specified)")


class DiskDeleteRequest(BaseModel):
    """Request model for deleting a disk"""
    disk_name: str = Field(..., description="Name of the disk to delete")


class DiskOperationResponse(BaseModel):
    """Response for disk create/delete operations"""
    operation_id: str = Field(..., description="Operation ID for tracking")
    disk_name: str = Field(..., description="Name of the disk")
    action: str = Field(..., description="Action performed (create/delete)")
    message: str = Field(..., description="Status message")
    requested_at: str = Field(..., description="Request timestamp (ISO 8601)")


class DiskInfo(BaseModel):
    """Information about a disk"""
    disk_name: str = Field(..., description="Name of the disk")
    user_id: str = Field(..., description="Owner user ID")
    size_gb: int | None = Field(None, description="Disk size in GB")
    created_at: str | None = Field(None, description="Creation timestamp")
    last_used: str | None = Field(None, description="Last used timestamp")
    in_use: bool = Field(False, description="Whether disk is currently in use")
    reservation_id: str | None = Field(None, description="Current reservation ID if in use")
    is_backing_up: bool = Field(False, description="Whether disk is being backed up")
    is_deleted: bool = Field(False, description="Whether disk is marked for deletion")
    snapshot_count: int = Field(0, description="Number of snapshots")


class DiskListResponse(BaseModel):
    """Response for listing disks"""
    disks: list[DiskInfo] = Field(..., description="List of disks")
    total: int = Field(..., description="Total number of disks")


class DiskRenameRequest(BaseModel):
    """Request model for renaming a disk"""
    new_name: str = Field(..., description="New name for the disk")


class DiskContentResponse(BaseModel):
    """Response for disk content listing"""
    disk_name: str = Field(..., description="Name of the disk")
    content: str | None = Field(None, description="Snapshot contents (ls -R output)")
    s3_path: str | None = Field(None, description="S3 path where contents are stored")
    snapshot_date: str | None = Field(None, description="When the snapshot was taken")
    message: str | None = Field(None, description="Status message if content unavailable")


class JobDetail(BaseModel):
    """Detailed information about a job/reservation"""
    job_id: str = Field(..., description="Job ID (reservation_id)")
    reservation_id: str = Field(..., description="Reservation ID (same as job_id)")
    user_id: str = Field(..., description="User email/ID")
    status: str = Field(..., description="Job status")
    gpu_type: str | None = Field(None, description="GPU type (h100, a100, etc.)")
    gpu_count: int | None = Field(None, description="Number of GPUs")
    instance_type: str = Field(..., description="EC2 instance type")
    duration_hours: float = Field(..., description="Reservation duration in hours")
    created_at: str = Field(..., description="Creation timestamp (ISO 8601)")
    expires_at: str | None = Field(None, description="Expiration timestamp (ISO 8601)")
    name: str | None = Field(None, description="User-provided name")
    pod_name: str | None = Field(None, description="Kubernetes pod name")
    node_ip: str | None = Field(None, description="Node IP address")
    node_port: int | None = Field(None, description="NodePort for SSH")
    ssh_command: str | None = Field(None, description="SSH command to connect")
    jupyter_enabled: bool = Field(False, description="Whether Jupyter Lab is enabled")
    jupyter_url: str | None = Field(None, description="Jupyter Lab URL")
    jupyter_token: str | None = Field(None, description="Jupyter Lab token")
    github_user: str | None = Field(None, description="GitHub username for SSH keys")
    
    class Config:
        json_schema_extra = {
            "example": {
                "job_id": "abc-123-def-456",
                "reservation_id": "abc-123-def-456",
                "user_id": "john@example.com",
                "status": "active",
                "gpu_type": "h100",
                "gpu_count": 4,
                "instance_type": "p5.48xlarge",
                "duration_hours": 2.0,
                "created_at": "2026-01-20T18:00:00Z",
                "expires_at": "2026-01-20T20:00:00Z",
                "name": "training-run",
                "pod_name": "gpu-dev-abc123",
                "node_ip": "10.0.1.42",
                "node_port": 30123,
                "ssh_command": "ssh gpu-dev-abc123",
                "jupyter_enabled": True,
                "jupyter_url": "https://...",
                "jupyter_token": "token123",
                "github_user": "johndoe"
            }
        }


class JobListResponse(BaseModel):
    """Response for listing jobs"""
    jobs: list[JobDetail] = Field(..., description="List of jobs")
    total: int = Field(..., description="Total number of jobs matching filters")
    limit: int = Field(..., description="Limit used for this query")
    offset: int = Field(..., description="Offset used for this query")


class GPUTypeAvailability(BaseModel):
    """Availability info for a specific GPU type"""
    gpu_type: str = Field(..., description="GPU type (h100, a100, etc.)")
    total: int = Field(..., description="Total GPUs of this type in cluster")
    available: int = Field(..., description="GPUs currently available")
    in_use: int = Field(..., description="GPUs currently in use")
    queued: int = Field(
        ..., description="GPUs requested by queued reservations"
    )
    max_per_node: int = Field(
        ..., description="Maximum GPUs per node for this type"
    )


class GPUAvailabilityResponse(BaseModel):
    """Response for GPU availability query"""
    availability: dict[str, GPUTypeAvailability] = Field(
        ..., description="Availability by GPU type"
    )
    timestamp: datetime = Field(..., description="When availability was computed")
    
    class Config:
        json_schema_extra = {
            "example": {
                "availability": {
                    "h100": {
                        "gpu_type": "h100",
                        "total": 16,
                        "available": 8,
                        "in_use": 8,
                        "queued": 4,
                        "max_per_node": 8
                    },
                    "a100": {
                        "gpu_type": "a100",
                        "total": 16,
                        "available": 12,
                        "in_use": 4,
                        "queued": 0,
                        "max_per_node": 8
                    }
                },
                "timestamp": "2026-01-20T18:30:00Z"
            }
        }


class ClusterStatusResponse(BaseModel):
    """Response for cluster status query"""
    total_gpus: int = Field(..., description="Total GPUs in cluster")
    available_gpus: int = Field(..., description="GPUs currently available")
    in_use_gpus: int = Field(..., description="GPUs currently in use")
    queued_gpus: int = Field(
        ..., description="GPUs requested by queued reservations"
    )
    active_reservations: int = Field(
        ..., description="Number of active reservations"
    )
    preparing_reservations: int = Field(
        ..., description="Number of preparing reservations"
    )
    queued_reservations: int = Field(
        ..., description="Number of queued reservations"
    )
    pending_reservations: int = Field(
        ..., description="Number of pending reservations"
    )
    by_gpu_type: dict[str, GPUTypeAvailability] = Field(
        ..., description="Breakdown by GPU type"
    )
    timestamp: datetime = Field(..., description="When status was computed")
    
    class Config:
        json_schema_extra = {
            "example": {
                "total_gpus": 64,
                "available_gpus": 32,
                "in_use_gpus": 24,
                "queued_gpus": 8,
                "active_reservations": 5,
                "preparing_reservations": 1,
                "queued_reservations": 2,
                "pending_reservations": 0,
                "by_gpu_type": {
                    "h100": {
                        "gpu_type": "h100",
                        "total": 16,
                        "available": 8,
                        "in_use": 8,
                        "queued": 4,
                        "max_per_node": 8
                    }
                },
                "timestamp": "2026-01-20T18:30:00Z"
            }
        }


class APIKeyResponse(BaseModel):
    """Response containing a new API key"""
    api_key: str = Field(
        ..., description="API key - save this, it won't be shown again!"
    )
    key_prefix: str = Field(
        ..., description="Key prefix for identification"
    )
    user_id: int
    username: str
    expires_at: datetime = Field(..., description="When the API key expires")


class AWSLoginRequest(BaseModel):
    """Request for AWS-based authentication"""
    aws_access_key_id: str = Field(
        ...,
        description="AWS access key ID",
        min_length=16,
        max_length=128
    )
    aws_secret_access_key: str = Field(
        ...,
        description="AWS secret access key",
        min_length=40,
        max_length=128
    )
    aws_session_token: str | None = Field(
        None,
        description="AWS session token (for assumed roles)",
        min_length=100,
        max_length=2048
    )


class AWSLoginResponse(BaseModel):
    """Response from AWS login"""
    api_key: str = Field(..., description="API key for future requests")
    key_prefix: str
    user_id: int
    username: str
    aws_arn: str = Field(..., description="Verified AWS ARN")
    expires_at: datetime = Field(..., description="When the API key expires")
    ttl_hours: int = Field(..., description="Time to live in hours")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str
    database: str
    queue: str
    timestamp: datetime


# ============================================================================
# Database Helpers
# ============================================================================

def hash_api_key(api_key: str) -> str:
    """Hash API key for storage"""
    return hashlib.sha256(api_key.encode()).hexdigest()


def extract_username_from_arn(arn: str) -> str:
    """
    Extract username from AWS ARN with validation
    Examples:
      arn:aws:sts::123456789:assumed-role/SSOCloudDevGpuReservation/john
        -> john
      arn:aws:iam::123456789:user/john
        -> john
    """
    parts = arn.split('/')
    if len(parts) >= 2:
        username = parts[-1]
        # Validate username contains only safe characters
        # Allow: alphanumeric, dot, underscore, hyphen
        if username and re.match(r'^[a-zA-Z0-9._-]+$', username):
            return username[:255]  # Ensure max length
        # If invalid characters, sanitize them
        sanitized = re.sub(r'[^a-zA-Z0-9._-]', '-', username)[:255]
        if sanitized:
            return sanitized

    # Fallback - sanitize ARN suffix
    fallback = arn.split(':')[-1].replace('/', '-')
    sanitized = re.sub(r'[^a-zA-Z0-9._-]', '-', fallback)[:255]

    # Ensure we got something valid
    if not sanitized or len(sanitized) < 1:
        raise ValueError(
            f"Cannot extract valid username from ARN: {arn}"
        )

    return sanitized


def extract_role_from_arn(arn: str) -> str:
    """
    Extract role name from AWS ARN (exact match, not substring)
    Examples:
      arn:aws:sts::123:assumed-role/SSOCloudDevGpuReservation/john
        -> SSOCloudDevGpuReservation
      arn:aws:iam::123:role/SSOCloudDevGpuReservation
        -> SSOCloudDevGpuReservation
      arn:aws:iam::123:user/john
        -> (empty - not a role)
    """
    # Handle assumed-role format (most common for SSO)
    if ':assumed-role/' in arn:
        parts = arn.split('/')
        if len(parts) >= 2:
            return parts[1]  # Role name is 2nd part after 'assumed-role/'

    # Handle direct role format
    elif ':role/' in arn:
        parts = arn.split('/')
        if len(parts) >= 1:
            return parts[-1]  # Role name is last part after 'role/'

    # Not a role ARN (could be user, etc.)
    return ""


async def verify_aws_credentials(
    aws_access_key_id: str,
    aws_secret_access_key: str,
    aws_session_token: str | None = None
) -> dict[str, str]:
    """
    Verify AWS credentials and return caller identity (async)
    Returns: {
        'account': '123456789',
        'user_id': 'AIDAI...',
        'arn': 'arn:aws:sts::123456789:assumed-role/...'
    }
    """
    try:
        # Create async STS client with provided credentials
        session = aioboto3.Session()
        async with session.client(
            'sts',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=AWS_REGION
        ) as sts_client:
            # Verify credentials by calling GetCallerIdentity (async)
            identity = await sts_client.get_caller_identity()

            return {
                'account': identity['Account'],
                'user_id': identity['UserId'],
                'arn': identity['Arn']
            }

    except ClientError as e:
        error_code = e.response['Error']['Code']
        if error_code == 'InvalidClientTokenId':
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid AWS credentials"
            ) from e
        elif error_code == 'SignatureDoesNotMatch':
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="AWS signature verification failed"
            ) from e
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"AWS authentication failed: {error_code}"
            ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to verify AWS credentials"
        ) from e


async def create_api_key_for_user(
    conn: asyncpg.Connection,
    user_id: int,
    username: str,
    description: str = "API key"
) -> tuple[str, str, datetime]:
    """
    Create a new API key with TTL for a user
    Returns: (api_key, key_prefix, expires_at)
    """
    api_key = secrets.token_urlsafe(API_KEY_LENGTH)
    key_hash = hash_api_key(api_key)
    key_prefix = api_key[:8]
    expires_at = datetime.now(UTC) + timedelta(hours=API_KEY_TTL_HOURS)

    await conn.execute(
        """
        INSERT INTO api_keys
            (user_id, key_hash, key_prefix, expires_at, description)
        VALUES ($1, $2, $3, $4, $5)
        """,
        user_id, key_hash, key_prefix, expires_at, description
    )

    return api_key, key_prefix, expires_at


async def verify_api_key(
    credentials: HTTPAuthorizationCredentials = security_scheme
) -> dict[str, Any]:
    """Verify API key and return user info"""
    api_key = credentials.credentials

    # Validate API key format (length check)
    if not api_key or len(api_key) < 16 or len(api_key) > 256:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key format"
        )

    key_hash = hash_api_key(api_key)

    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("""
            SELECT
                u.user_id, u.username, u.email, u.is_active as user_active,
                k.key_id, k.expires_at, k.is_active as key_active
            FROM api_keys k
            JOIN api_users u ON k.user_id = u.user_id
            WHERE k.key_hash = $1
        """, key_hash)

        if not row:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key"
            )

        # Check if user is active
        if not row['user_active']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User account is disabled"
            )

        # Check if key is active
        if not row['key_active']:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key has been revoked"
            )

        # Check expiration
        if row['expires_at'] and row['expires_at'] < datetime.now(UTC):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="API key has expired"
            )

        # Update last used timestamp
        await conn.execute("""
            UPDATE api_keys
            SET last_used_at = CURRENT_TIMESTAMP
            WHERE key_id = $1
        """, row['key_id'])

        return {
            "user_id": row['user_id'],
            "username": row['username'],
            "email": row['email']
        }


# ============================================================================
# API Endpoints
# ============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check() -> dict[str, Any]:
    """Health check endpoint"""
    db_status = "unknown"
    queue_status = "unknown"

    # Check if db_pool is initialized
    if db_pool is None:
        return {
            "status": "unhealthy",
            "database": "not initialized",
            "queue": "unknown",
            "timestamp": datetime.now(UTC)
        }

    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            db_status = "healthy"

            # Check if PGMQ queue exists
            # Note: queue_exists() doesn't exist, use list_queues() instead
            queues = await conn.fetch(
                "SELECT queue_name FROM pgmq.list_queues()"
            )
            queue_names = [row['queue_name'] for row in queues]
            queue_status = (
                "healthy" if QUEUE_NAME in queue_names else "missing"
            )
    except Exception:
        # Don't expose exception details in health check
        db_status = "unhealthy"
        queue_status = "unknown"

    overall_status = (
        "healthy"
        if db_status == "healthy" and queue_status == "healthy"
        else "unhealthy"
    )

    return {
        "status": overall_status,
        "database": db_status,
        "queue": queue_status,
        "timestamp": datetime.now(UTC)
    }


@app.post("/v1/jobs/submit", response_model=JobSubmissionResponse)
async def submit_job(
    job: JobSubmissionRequest,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobSubmissionResponse:
    """
    Submit a new GPU job to the queue

    Requires valid API key in Authorization header:
    `Authorization: Bearer <your-api-key>`
    """
    try:
        async with db_pool.acquire() as conn:
            # Create job message
            job_id = str(uuid.uuid4())
            message = {
                "job_id": job_id,
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "image": job.image,
                "instance_type": job.instance_type,
                "duration_hours": job.duration_hours,
                "disk_name": job.disk_name,
                "disk_size_gb": job.disk_size_gb,
                "env_vars": job.env_vars,
                "command": job.command,
                "submitted_at": datetime.now(UTC).isoformat(),
                "status": "queued"
            }

            # Send to PGMQ
            msg_id = await conn.fetchval(
                f"SELECT pgmq.send('{QUEUE_NAME}', $1)",
                json.dumps(message)
            )

            return JobSubmissionResponse(
                job_id=job_id,
                status="queued",
                message=(
                    f"Job submitted successfully to queue "
                    f"(message ID: {msg_id})"
                ),
                estimated_start_time=None
            )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit job"
        ) from e


@app.get("/v1/jobs/{job_id}", response_model=JobDetail)
async def get_job_status(
    job_id: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobDetail:
    """
    Get detailed information about a specific job/reservation
    
    Returns comprehensive job details including status, connection info,
    and resource allocation.
    """
    try:
        async with db_pool.acquire() as conn:
            # Query reservations table from DynamoDB structure
            # Note: This assumes the Job Processor updates a reservations table in PostgreSQL
            query = """
                SELECT 
                    reservation_id,
                    user_id,
                    status,
                    gpu_type,
                    gpu_count,
                    instance_type,
                    duration_hours,
                    created_at,
                    expires_at,
                    name,
                    pod_name,
                    node_ip,
                    node_port,
                    jupyter_enabled,
                    jupyter_url,
                    jupyter_token,
                    github_user
                FROM reservations
                WHERE reservation_id = $1
                LIMIT 1
            """
            
            row = await conn.fetchrow(query, job_id)
            
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Job {job_id} not found"
                )
            
            # Check authorization - user can only see their own jobs
            if row["user_id"] != user_info["username"] and row["user_id"] != user_info["user_id"]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only view your own jobs"
                )
            
            # Build SSH command if pod is active
            ssh_command = None
            if row["pod_name"] and row["status"] == "active":
                ssh_command = f"ssh {row['pod_name']}"
            
            return JobDetail(
                job_id=row["reservation_id"],
                reservation_id=row["reservation_id"],
                user_id=row["user_id"],
                status=row["status"],
                gpu_type=row.get("gpu_type"),
                gpu_count=row.get("gpu_count"),
                instance_type=row.get("instance_type", "unknown"),
                duration_hours=float(row.get("duration_hours", 0)),
                created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                expires_at=row["expires_at"].isoformat() if row.get("expires_at") else None,
                name=row.get("name"),
                pod_name=row.get("pod_name"),
                node_ip=row.get("node_ip"),
                node_port=row.get("node_port"),
                ssh_command=ssh_command,
                jupyter_enabled=row.get("jupyter_enabled", False),
                jupyter_url=row.get("jupyter_url"),
                jupyter_token=row.get("jupyter_token"),
                github_user=row.get("github_user")
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve job details: {str(e)}"
        ) from e


@app.get("/v1/jobs", response_model=JobListResponse)
async def list_jobs(
    user_info: dict[str, Any] = Security(verify_api_key),
    status_filter: str | None = Query(None, alias="status", description="Filter by status (comma-separated)"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip")
) -> JobListResponse:
    """
    List jobs/reservations for the authenticated user
    
    Supports filtering by status and pagination.
    Returns jobs sorted by creation time (newest first).
    """
    try:
        async with db_pool.acquire() as conn:
            # Build query with optional status filter
            query_conditions = ["user_id = $1"]
            query_params: list[Any] = [user_info["username"]]
            param_index = 2
            
            if status_filter:
                statuses = [s.strip() for s in status_filter.split(",")]
                placeholders = ", ".join(f"${i}" for i in range(param_index, param_index + len(statuses)))
                query_conditions.append(f"status IN ({placeholders})")
                query_params.extend(statuses)
                param_index += len(statuses)
            
            where_clause = " AND ".join(query_conditions)
            
            # Count total matching jobs
            count_query = f"""
                SELECT COUNT(*)
                FROM reservations
                WHERE {where_clause}
            """
            total = await conn.fetchval(count_query, *query_params)
            
            # Fetch paginated results
            query = f"""
                SELECT 
                    reservation_id,
                    user_id,
                    status,
                    gpu_type,
                    gpu_count,
                    instance_type,
                    duration_hours,
                    created_at,
                    expires_at,
                    name,
                    pod_name,
                    node_ip,
                    node_port,
                    jupyter_enabled,
                    jupyter_url,
                    jupyter_token,
                    github_user
                FROM reservations
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${param_index}
                OFFSET ${param_index + 1}
            """
            query_params.extend([limit, offset])
            
            rows = await conn.fetch(query, *query_params)
            
            # Convert rows to JobDetail objects
            jobs = []
            for row in rows:
                # Build SSH command if pod is active
                ssh_command = None
                if row["pod_name"] and row["status"] == "active":
                    ssh_command = f"ssh {row['pod_name']}"
                
                jobs.append(JobDetail(
                    job_id=row["reservation_id"],
                    reservation_id=row["reservation_id"],
                    user_id=row["user_id"],
                    status=row["status"],
                    gpu_type=row.get("gpu_type"),
                    gpu_count=row.get("gpu_count"),
                    instance_type=row.get("instance_type", "unknown"),
                    duration_hours=float(row.get("duration_hours", 0)),
                    created_at=row["created_at"].isoformat() if row.get("created_at") else None,
                    expires_at=row["expires_at"].isoformat() if row.get("expires_at") else None,
                    name=row.get("name"),
                    pod_name=row.get("pod_name"),
                    node_ip=row.get("node_ip"),
                    node_port=row.get("node_port"),
                    ssh_command=ssh_command,
                    jupyter_enabled=row.get("jupyter_enabled", False),
                    jupyter_url=row.get("jupyter_url"),
                    jupyter_token=row.get("jupyter_token"),
                    github_user=row.get("github_user")
                ))
            
            return JobListResponse(
                jobs=jobs,
                total=total or 0,
                limit=limit,
                offset=offset
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list jobs: {str(e)}"
        ) from e


@app.post("/v1/jobs/{job_id}/cancel", response_model=JobActionResponse)
async def cancel_job(
    job_id: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobActionResponse:
    """
    Cancel a running or queued job
    
    Sends a cancellation action to PGMQ for the Job Processor to handle.
    """
    try:
        async with db_pool.acquire() as conn:
            # Create cancellation message
            message = {
                "action": "cancel",
                "job_id": job_id,
                "reservation_id": job_id,  # For backward compatibility
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "requested_at": datetime.now(UTC).isoformat(),
            }
            
            # Send to PGMQ
            msg_id = await conn.fetchval(
                f"SELECT pgmq.send('{QUEUE_NAME}', $1)",
                json.dumps(message)
            )
            
            return JobActionResponse(
                job_id=job_id,
                action="cancel",
                status="requested",
                message=f"Cancellation request submitted (message ID: {msg_id})"
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit cancellation request"
        ) from e


@app.post("/v1/jobs/{job_id}/extend", response_model=JobActionResponse)
async def extend_job(
    job_id: str,
    request: ExtendJobRequest,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobActionResponse:
    """
    Extend the duration of a running job
    
    Sends an extend action to PGMQ for the Job Processor to handle.
    """
    try:
        async with db_pool.acquire() as conn:
            # Create extend message
            message = {
                "action": "extend",
                "job_id": job_id,
                "reservation_id": job_id,  # For backward compatibility
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "extension_hours": request.extension_hours,
                "requested_at": datetime.now(UTC).isoformat(),
            }
            
            # Send to PGMQ
            msg_id = await conn.fetchval(
                f"SELECT pgmq.send('{QUEUE_NAME}', $1)",
                json.dumps(message)
            )
            
            return JobActionResponse(
                job_id=job_id,
                action="extend",
                status="requested",
                message=(
                    f"Extension request submitted for {request.extension_hours} hours "
                    f"(message ID: {msg_id})"
                )
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit extension request"
        ) from e


@app.post("/v1/jobs/{job_id}/jupyter/enable", response_model=JobActionResponse)
async def enable_jupyter(
    job_id: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobActionResponse:
    """
    Enable Jupyter Lab for a running job
    
    Sends an enable_jupyter action to PGMQ for the Job Processor to handle.
    """
    try:
        async with db_pool.acquire() as conn:
            # Create enable jupyter message
            message = {
                "action": "enable_jupyter",
                "job_id": job_id,
                "reservation_id": job_id,  # For backward compatibility
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "requested_at": datetime.now(UTC).isoformat(),
            }
            
            # Send to PGMQ
            msg_id = await conn.fetchval(
                f"SELECT pgmq.send('{QUEUE_NAME}', $1)",
                json.dumps(message)
            )
            
            return JobActionResponse(
                job_id=job_id,
                action="enable_jupyter",
                status="requested",
                message=f"Jupyter enable request submitted (message ID: {msg_id})"
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit Jupyter enable request"
        ) from e


@app.post("/v1/jobs/{job_id}/jupyter/disable", response_model=JobActionResponse)
async def disable_jupyter(
    job_id: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobActionResponse:
    """
    Disable Jupyter Lab for a running job
    
    Sends a disable_jupyter action to PGMQ for the Job Processor to handle.
    """
    try:
        async with db_pool.acquire() as conn:
            # Create disable jupyter message
            message = {
                "action": "disable_jupyter",
                "job_id": job_id,
                "reservation_id": job_id,  # For backward compatibility
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "requested_at": datetime.now(UTC).isoformat(),
            }
            
            # Send to PGMQ
            msg_id = await conn.fetchval(
                f"SELECT pgmq.send('{QUEUE_NAME}', $1)",
                json.dumps(message)
            )
            
            return JobActionResponse(
                job_id=job_id,
                action="disable_jupyter",
                status="requested",
                message=f"Jupyter disable request submitted (message ID: {msg_id})"
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit Jupyter disable request"
        ) from e


@app.post("/v1/jobs/{job_id}/users", response_model=JobActionResponse)
async def add_user_to_job(
    job_id: str,
    request: AddUserRequest,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobActionResponse:
    """
    Add a user's SSH keys to a running job
    
    Fetches SSH keys from GitHub and adds them to the job's authorized_keys.
    Sends an add_user action to PGMQ for the Job Processor to handle.
    """
    try:
        async with db_pool.acquire() as conn:
            # Create add user message
            message = {
                "action": "add_user",
                "job_id": job_id,
                "reservation_id": job_id,  # For backward compatibility
                "user_id": user_info["user_id"],
                "username": user_info["username"],
                "github_username": request.github_username,
                "requested_at": datetime.now(UTC).isoformat(),
            }
            
            # Send to PGMQ
            msg_id = await conn.fetchval(
                f"SELECT pgmq.send('{QUEUE_NAME}', $1)",
                json.dumps(message)
            )
            
            return JobActionResponse(
                job_id=job_id,
                action="add_user",
                status="requested",
                message=(
                    f"Add user request submitted for GitHub user "
                    f"'{request.github_username}' (message ID: {msg_id})"
                )
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to submit add user request"
        ) from e


# ============================================================================
# GPU Availability
# ============================================================================

@app.get("/v1/gpu/availability", response_model=GPUAvailabilityResponse)
async def get_gpu_availability(
    user_info: dict[str, Any] = Security(verify_api_key)
) -> GPUAvailabilityResponse:
    """
    Get current GPU availability across all GPU types
    
    Returns the total, available, in-use, and queued GPU counts for each
    GPU type in the cluster. This helps users decide which GPU type to
    reserve based on current availability.
    
    Calculations:
    - total: Known cluster capacity per GPU type (from config)
    - in_use: Sum of gpu_count for active/preparing reservations
    - queued: Sum of gpu_count for queued/pending reservations
    - available: total - in_use
    """
    try:
        async with db_pool.acquire() as conn:
            # GPU configuration - matches Terraform and Lambda configs
            # This should ideally come from a config table or environment
            GPU_CONFIG = {
                "h100": {"total": 16, "max_per_node": 8},
                "h200": {"total": 16, "max_per_node": 8},
                "b200": {"total": 16, "max_per_node": 8},
                "a100": {"total": 16, "max_per_node": 8},
                "a10g": {"total": 4, "max_per_node": 4},
                "t4": {"total": 8, "max_per_node": 4},
                "t4-small": {"total": 1, "max_per_node": 1},
                "l4": {"total": 4, "max_per_node": 4},
            }
            
            # Query active/preparing reservations (GPU in use)
            in_use_query = """
                SELECT 
                    gpu_type,
                    COALESCE(SUM(gpu_count), 0) as count
                FROM reservations
                WHERE status IN ('active', 'preparing')
                AND gpu_type IS NOT NULL
                GROUP BY gpu_type
            """
            in_use_rows = await conn.fetch(in_use_query)
            in_use_map = {row["gpu_type"]: int(row["count"]) for row in in_use_rows}
            
            # Query queued/pending reservations
            queued_query = """
                SELECT 
                    gpu_type,
                    COALESCE(SUM(gpu_count), 0) as count
                FROM reservations
                WHERE status IN ('queued', 'pending')
                AND gpu_type IS NOT NULL
                GROUP BY gpu_type
            """
            queued_rows = await conn.fetch(queued_query)
            queued_map = {row["gpu_type"]: int(row["count"]) for row in queued_rows}
            
            # Build availability response
            availability = {}
            for gpu_type, config in GPU_CONFIG.items():
                total = config["total"]
                in_use = in_use_map.get(gpu_type, 0)
                queued = queued_map.get(gpu_type, 0)
                available = max(0, total - in_use)  # Can't be negative
                
                availability[gpu_type] = GPUTypeAvailability(
                    gpu_type=gpu_type,
                    total=total,
                    available=available,
                    in_use=in_use,
                    queued=queued,
                    max_per_node=config["max_per_node"]
                )
            
            return GPUAvailabilityResponse(
                availability=availability,
                timestamp=datetime.now(UTC)
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get GPU availability: {str(e)}"
        ) from e


@app.get("/v1/cluster/status", response_model=ClusterStatusResponse)
async def get_cluster_status(
    user_info: dict[str, Any] = Security(verify_api_key)
) -> ClusterStatusResponse:
    """
    Get overall cluster status and statistics
    
    Returns aggregate statistics across the entire GPU cluster including
    total capacity, current utilization, queue depth, and breakdown by
    GPU type.
    
    This is useful for admins and monitoring dashboards to understand
    overall cluster health and utilization.
    """
    try:
        async with db_pool.acquire() as conn:
            # GPU configuration (same as availability endpoint)
            GPU_CONFIG = {
                "h100": {"total": 16, "max_per_node": 8},
                "h200": {"total": 16, "max_per_node": 8},
                "b200": {"total": 16, "max_per_node": 8},
                "a100": {"total": 16, "max_per_node": 8},
                "a10g": {"total": 4, "max_per_node": 4},
                "t4": {"total": 8, "max_per_node": 4},
                "t4-small": {"total": 1, "max_per_node": 1},
                "l4": {"total": 4, "max_per_node": 4},
            }
            
            # Count reservations by status
            status_query = """
                SELECT 
                    status,
                    COUNT(*) as count
                FROM reservations
                WHERE status IN ('active', 'preparing', 'queued', 'pending')
                GROUP BY status
            """
            status_rows = await conn.fetch(status_query)
            status_counts = {row["status"]: int(row["count"]) for row in status_rows}
            
            # Query GPU usage by type and status
            in_use_query = """
                SELECT 
                    gpu_type,
                    COALESCE(SUM(gpu_count), 0) as count
                FROM reservations
                WHERE status IN ('active', 'preparing')
                AND gpu_type IS NOT NULL
                GROUP BY gpu_type
            """
            in_use_rows = await conn.fetch(in_use_query)
            in_use_map = {row["gpu_type"]: int(row["count"]) for row in in_use_rows}
            
            # Query queued/pending GPUs by type
            queued_query = """
                SELECT 
                    gpu_type,
                    COALESCE(SUM(gpu_count), 0) as count
                FROM reservations
                WHERE status IN ('queued', 'pending')
                AND gpu_type IS NOT NULL
                GROUP BY gpu_type
            """
            queued_rows = await conn.fetch(queued_query)
            queued_map = {row["gpu_type"]: int(row["count"]) for row in queued_rows}
            
            # Calculate cluster-wide totals
            total_gpus = sum(config["total"] for config in GPU_CONFIG.values())
            in_use_gpus = sum(in_use_map.values())
            queued_gpus = sum(queued_map.values())
            available_gpus = max(0, total_gpus - in_use_gpus)
            
            # Build per-GPU-type breakdown
            by_gpu_type = {}
            for gpu_type, config in GPU_CONFIG.items():
                total = config["total"]
                in_use = in_use_map.get(gpu_type, 0)
                queued = queued_map.get(gpu_type, 0)
                available = max(0, total - in_use)
                
                by_gpu_type[gpu_type] = GPUTypeAvailability(
                    gpu_type=gpu_type,
                    total=total,
                    available=available,
                    in_use=in_use,
                    queued=queued,
                    max_per_node=config["max_per_node"]
                )
            
            return ClusterStatusResponse(
                total_gpus=total_gpus,
                available_gpus=available_gpus,
                in_use_gpus=in_use_gpus,
                queued_gpus=queued_gpus,
                active_reservations=status_counts.get("active", 0),
                preparing_reservations=status_counts.get("preparing", 0),
                queued_reservations=status_counts.get("queued", 0),
                pending_reservations=status_counts.get("pending", 0),
                by_gpu_type=by_gpu_type,
                timestamp=datetime.now(UTC)
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get cluster status: {str(e)}"
        ) from e


# ============================================================================
# API Key Management
# ============================================================================

@app.post("/v1/keys/rotate", response_model=APIKeyResponse)
async def rotate_api_key(
    user_info: dict[str, Any] = Security(verify_api_key)
) -> APIKeyResponse:
    """
    Generate a new API key for the authenticated user

    This creates a new API key with a fresh TTL.
    Old keys remain valid until they expire.
    """
    try:
        async with db_pool.acquire() as conn:
            # Generate new key with TTL
            api_key, key_prefix, expires_at = await create_api_key_for_user(
                conn,
                user_info["user_id"],
                user_info["username"],
                "Manually rotated key"
            )

            return APIKeyResponse(
                api_key=api_key,
                key_prefix=key_prefix,
                user_id=user_info["user_id"],
                username=user_info["username"],
                expires_at=expires_at
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to rotate key"
        ) from e


@app.post("/v1/auth/aws-login", response_model=AWSLoginResponse)
async def aws_login(request: AWSLoginRequest) -> AWSLoginResponse:
    """
    Authenticate using AWS credentials and receive an API key

    This endpoint verifies AWS credentials by calling
    AWS STS GetCallerIdentity. If the credentials are valid and
    the role matches ALLOWED_AWS_ROLE, it creates or updates the user
    and issues a time-limited API key.

    The API key expires after API_KEY_TTL_HOURS (default 2 hours).
    The CLI should automatically re-authenticate when the key expires.
    """
    # 1. Verify AWS credentials
    identity = await verify_aws_credentials(
        request.aws_access_key_id,
        request.aws_secret_access_key,
        request.aws_session_token
    )

    # 2. Extract and verify role (exact match, not substring)
    role = extract_role_from_arn(identity['arn'])
    if role != ALLOWED_AWS_ROLE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Access denied. Required role: {ALLOWED_AWS_ROLE}, "
                f"got: {role or 'none'}"
            )
        )

    # 3. Extract username from ARN
    username = extract_username_from_arn(identity['arn'])

    try:
        async with db_pool.acquire() as conn:
            async with conn.transaction():
                # 4. Create or get user (reliable upsert pattern)
                # First, check if user exists
                user_id = await conn.fetchval(
                    "SELECT user_id FROM api_users "
                    "WHERE username = $1",
                    username
                )

                if user_id is None:
                    # User doesn't exist, create new user
                    user_id = await conn.fetchval("""
                        INSERT INTO api_users (username, is_active)
                        VALUES ($1, true)
                        RETURNING user_id
                    """, username)
                else:
                    # User exists, ensure they're active
                    await conn.execute("""
                        UPDATE api_users SET is_active = true
                        WHERE user_id = $1
                    """, user_id)

                # 5. Revoke old keys (optional)
                # Keep old keys valid or revoke?
                # For now, keep old keys valid until they expire
                # await conn.execute("""
                #     UPDATE api_keys SET is_active = false
                #     WHERE user_id = $1 AND is_active = true
                # """, user_id)

                # 6. Create new API key with TTL
                api_key, key_prefix, expires_at = (
                    await create_api_key_for_user(
                        conn,
                        user_id,
                        username,
                        f"AWS login from {identity['arn']}"
                    )
                )

        return AWSLoginResponse(
            api_key=api_key,
            key_prefix=key_prefix,
            user_id=user_id,
            username=username,
            aws_arn=identity['arn'],
            expires_at=expires_at,
            ttl_hours=API_KEY_TTL_HOURS
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create API key"
        ) from e


@app.post("/v1/disks", response_model=DiskOperationResponse)
async def create_disk(
    request: DiskCreateRequest,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> DiskOperationResponse:
    """Create a new persistent disk
    
    This endpoint queues a disk creation request to be processed by the job processor.
    The actual disk creation happens asynchronously.
    """
    username = user_info["username"]
    operation_id = str(uuid.uuid4())
    requested_at = datetime.now(UTC)
    
    # Validate disk name (alphanumeric + hyphens + underscores)
    if not re.match(r'^[a-zA-Z0-9_-]+$', request.disk_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Disk name must contain only letters, numbers, hyphens, and underscores"
        )
    
    # Queue disk creation message to PGMQ
    message = {
        "action": "create_disk",
        "operation_id": operation_id,
        "user_id": username,
        "disk_name": request.disk_name,
        "size_gb": request.size_gb,
        "requested_at": requested_at.isoformat()
    }
    
    try:
        async with db_pool.acquire() as conn:
            # Send message to PGMQ
            await conn.execute(
                f"SELECT pgmq.send('{DISK_QUEUE_NAME}', $1::jsonb)",
                json.dumps(message)
            )
        
        return DiskOperationResponse(
            operation_id=operation_id,
            disk_name=request.disk_name,
            action="create",
            message=f"Disk creation request queued successfully",
            requested_at=requested_at.isoformat()
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue disk creation: {str(e)}"
        ) from e


@app.delete("/v1/disks/{disk_name}", response_model=DiskOperationResponse)
async def delete_disk(
    disk_name: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> DiskOperationResponse:
    """Delete a persistent disk (soft delete with 30-day retention)
    
    This endpoint queues a disk deletion request to be processed by the job processor.
    The disk will be marked for deletion and removed after 30 days.
    """
    username = user_info["username"]
    operation_id = str(uuid.uuid4())
    requested_at = datetime.now(UTC)
    
    # Calculate deletion date (30 days from now)
    delete_date = requested_at + timedelta(days=30)
    delete_date_str = delete_date.strftime('%Y-%m-%d')
    
    # Queue disk deletion message to PGMQ
    message = {
        "action": "delete_disk",
        "operation_id": operation_id,
        "user_id": username,
        "disk_name": disk_name,
        "delete_date": delete_date_str,
        "requested_at": requested_at.isoformat()
    }
    
    try:
        async with db_pool.acquire() as conn:
            # Send message to PGMQ
            await conn.execute(
                f"SELECT pgmq.send('{DISK_QUEUE_NAME}', $1::jsonb)",
                json.dumps(message)
            )
        
        return DiskOperationResponse(
            operation_id=operation_id,
            disk_name=disk_name,
            action="delete",
            message=f"Disk deletion request queued successfully. Will be deleted on {delete_date_str}",
            requested_at=requested_at.isoformat()
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue disk deletion: {str(e)}"
        ) from e


@app.get("/v1/disks", response_model=DiskListResponse)
async def list_disks(
    user_info: dict[str, Any] = Security(verify_api_key)
) -> DiskListResponse:
    """List all persistent disks for the current user
    
    Returns disk information from PostgreSQL.
    Excludes deleted disks by default.
    """
    username = user_info["username"]
    
    try:
        async with db_pool.acquire() as conn:
            # Query disks for this user (exclude deleted by default)
            rows = await conn.fetch("""
                SELECT 
                    disk_name, user_id, size_gb, created_at, last_used,
                    in_use, reservation_id, is_backing_up, is_deleted,
                    delete_date, snapshot_count, pending_snapshot_count,
                    ebs_volume_id, last_snapshot_at
                FROM disks
                WHERE user_id = $1 AND is_deleted = false
                ORDER BY created_at DESC
            """, username)
            
            # Convert to DiskInfo objects
            disks = []
            for row in rows:
                disk = DiskInfo(
                    disk_name=row['disk_name'],
                    user_id=row['user_id'],
                    size_gb=row['size_gb'],
                    created_at=row['created_at'].isoformat() if row['created_at'] else None,
                    last_used=row['last_used'].isoformat() if row['last_used'] else None,
                    in_use=row['in_use'],
                    reservation_id=str(row['reservation_id']) if row['reservation_id'] else None,
                    is_backing_up=row['is_backing_up'],
                    is_deleted=row['is_deleted'],
                    snapshot_count=row['snapshot_count']
                )
                disks.append(disk)
            
            return DiskListResponse(
                disks=disks,
                total=len(disks)
            )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list disks: {str(e)}"
        ) from e


@app.get("/v1/disks/{disk_name}", response_model=DiskInfo)
async def get_disk_info(
    disk_name: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> DiskInfo:
    """Get information about a specific disk
    
    Returns detailed disk information from PostgreSQL.
    """
    username = user_info["username"]
    
    try:
        async with db_pool.acquire() as conn:
            # Query specific disk
            row = await conn.fetchrow("""
                SELECT 
                    disk_name, user_id, size_gb, created_at, last_used,
                    in_use, reservation_id, is_backing_up, is_deleted,
                    delete_date, snapshot_count, pending_snapshot_count,
                    ebs_volume_id, last_snapshot_at
                FROM disks
                WHERE user_id = $1 AND disk_name = $2
            """, username, disk_name)
            
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Disk '{disk_name}' not found"
                )
            
            return DiskInfo(
                disk_name=row['disk_name'],
                user_id=row['user_id'],
                size_gb=row['size_gb'],
                created_at=row['created_at'].isoformat() if row['created_at'] else None,
                last_used=row['last_used'].isoformat() if row['last_used'] else None,
                in_use=row['in_use'],
                reservation_id=str(row['reservation_id']) if row['reservation_id'] else None,
                is_backing_up=row['is_backing_up'],
                is_deleted=row['is_deleted'],
                snapshot_count=row['snapshot_count']
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get disk info: {str(e)}"
        ) from e


@app.get("/v1/disks/{disk_name}/operations/{operation_id}")
async def get_disk_operation_status(
    disk_name: str,
    operation_id: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> dict[str, Any]:
    """Poll the status of a disk operation (create/delete)
    
    Returns operation status and details from PostgreSQL.
    Used by CLI to poll for operation completion.
    """
    username = user_info["username"]
    
    try:
        async with db_pool.acquire() as conn:
            # Query disk with matching operation_id
            row = await conn.fetchrow("""
                SELECT 
                    disk_name, user_id, operation_id, operation_status,
                    operation_error, created_at, last_updated,
                    is_deleted, delete_date
                FROM disks
                WHERE user_id = $1 AND disk_name = $2 AND operation_id::text = $3
            """, username, disk_name, operation_id)
            
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Operation '{operation_id}' not found for disk '{disk_name}'"
                )
            
            # Return operation status
            return {
                "operation_id": operation_id,
                "disk_name": row['disk_name'],
                "status": row['operation_status'] or "unknown",
                "error": row['operation_error'],
                "is_deleted": row['is_deleted'],
                "delete_date": row['delete_date'].isoformat() if row['delete_date'] else None,
                "created_at": row['created_at'].isoformat() if row['created_at'] else None,
                "last_updated": row['last_updated'].isoformat() if row['last_updated'] else None,
                "completed": row['operation_status'] in ['completed', 'failed']
            }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get operation status: {str(e)}"
        ) from e


@app.get("/v1/disks/{disk_name}/content", response_model=DiskContentResponse)
async def get_disk_content(
    disk_name: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> DiskContentResponse:
    """Get the contents of a disk's latest snapshot
    
    Returns the ls -R output stored in S3 when the last snapshot was taken.
    This allows users to view disk contents without mounting the volume.
    
    Requires authentication via API key.
    """
    username = user_info["username"]
    
    try:
        async with db_pool.acquire() as conn:
            # Get disk info including S3 path
            row = await conn.fetchrow("""
                SELECT 
                    disk_name, latest_snapshot_content_s3, 
                    last_snapshot_at, is_deleted
                FROM disks
                WHERE user_id = $1 AND disk_name = $2
            """, username, disk_name)
            
            if not row:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Disk '{disk_name}' not found"
                )
            
            # Check if disk is deleted
            if row['is_deleted']:
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=f"Disk '{disk_name}' is marked for deletion"
                )
            
            s3_path = row['latest_snapshot_content_s3']
            
            # If no S3 path, return empty content with message
            if not s3_path:
                return DiskContentResponse(
                    disk_name=disk_name,
                    content=None,
                    s3_path=None,
                    snapshot_date=None,
                    message="No snapshot contents available. This may be a newly created disk or a disk created before content tracking was enabled."
                )
            
            # Parse S3 path (s3://bucket/key)
            if not s3_path.startswith('s3://'):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid S3 path format in database"
                )
            
            path_parts = s3_path[5:].split('/', 1)
            if len(path_parts) != 2:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Invalid S3 path format in database"
                )
            
            bucket_name, s3_key = path_parts
            
            # Fetch contents from S3 using aioboto3
            session = aioboto3.Session()
            async with session.client('s3', region_name=AWS_REGION) as s3:
                try:
                    response = await s3.get_object(Bucket=bucket_name, Key=s3_key)
                    async with response['Body'] as stream:
                        contents = await stream.read()
                        content_str = contents.decode('utf-8')
                    
                    return DiskContentResponse(
                        disk_name=disk_name,
                        content=content_str,
                        s3_path=s3_path,
                        snapshot_date=row['last_snapshot_at'].isoformat() if row['last_snapshot_at'] else None,
                        message=None
                    )
                
                except s3.exceptions.NoSuchKey:
                    return DiskContentResponse(
                        disk_name=disk_name,
                        content=None,
                        s3_path=s3_path,
                        snapshot_date=row['last_snapshot_at'].isoformat() if row['last_snapshot_at'] else None,
                        message="Contents file not found in S3"
                    )
                except Exception as s3_error:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to fetch contents from S3: {str(s3_error)}"
                    ) from s3_error
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get disk content: {str(e)}"
        ) from e


@app.post("/v1/disks/{disk_name}/rename")
async def rename_disk(
    disk_name: str,
    request: DiskRenameRequest,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> dict[str, Any]:
    """Rename a persistent disk
    
    Updates the disk name in PostgreSQL and tags on all associated EBS snapshots.
    The disk must not be in use during the rename operation.
    
    Requires authentication via API key.
    """
    username = user_info["username"]
    new_name = request.new_name
    
    # Validate new disk name (alphanumeric + hyphens + underscores)
    if not re.match(r'^[a-zA-Z0-9_-]+$', new_name):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Disk name must contain only letters, numbers, hyphens, and underscores"
        )
    
    try:
        async with db_pool.acquire() as conn:
            # Check if old disk exists
            old_disk = await conn.fetchrow("""
                SELECT disk_name, in_use, reservation_id, ebs_volume_id, is_deleted
                FROM disks
                WHERE user_id = $1 AND disk_name = $2
            """, username, disk_name)
            
            if not old_disk:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Disk '{disk_name}' not found"
                )
            
            # Check if disk is deleted
            if old_disk['is_deleted']:
                raise HTTPException(
                    status_code=status.HTTP_410_GONE,
                    detail=f"Cannot rename disk '{disk_name}' - it is marked for deletion"
                )
            
            # Check if disk is in use
            if old_disk['in_use']:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Cannot rename disk '{disk_name}' - it is currently in use by reservation {old_disk['reservation_id']}"
                )
            
            # Check if new name already exists
            existing_disk = await conn.fetchrow("""
                SELECT disk_name FROM disks
                WHERE user_id = $1 AND disk_name = $2
            """, username, new_name)
            
            if existing_disk:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Disk '{new_name}' already exists"
                )
            
            # Update disk name in PostgreSQL
            await conn.execute("""
                UPDATE disks
                SET disk_name = $1, last_updated = NOW()
                WHERE user_id = $2 AND disk_name = $3
            """, new_name, username, disk_name)
            
            # Update EBS snapshot tags using aioboto3
            session = aioboto3.Session()
            async with session.client('ec2', region_name=AWS_REGION) as ec2:
                # Find all snapshots for this disk
                response = await ec2.describe_snapshots(
                    OwnerIds=["self"],
                    Filters=[
                        {"Name": "tag:gpu-dev-user", "Values": [username]},
                        {"Name": "tag:disk_name", "Values": [disk_name]},
                    ]
                )
                
                snapshots = response.get('Snapshots', [])
                
                if not snapshots:
                    # No snapshots to update - this is OK for new disks
                    return {
                        "message": f"Disk renamed from '{disk_name}' to '{new_name}' (no snapshots found)",
                        "old_name": disk_name,
                        "new_name": new_name,
                        "snapshots_updated": 0
                    }
                
                # Update disk_name tag on each snapshot
                renamed_count = 0
                errors = []
                for snapshot in snapshots:
                    snapshot_id = snapshot['SnapshotId']
                    try:
                        await ec2.create_tags(
                            Resources=[snapshot_id],
                            Tags=[{"Key": "disk_name", "Value": new_name}]
                        )
                        renamed_count += 1
                    except Exception as tag_error:
                        errors.append(f"{snapshot_id}: {str(tag_error)}")
                
                if errors:
                    # Partial success - some snapshots updated
                    return {
                        "message": f"Disk renamed from '{disk_name}' to '{new_name}' ({renamed_count}/{len(snapshots)} snapshots updated)",
                        "old_name": disk_name,
                        "new_name": new_name,
                        "snapshots_updated": renamed_count,
                        "errors": errors
                    }
                
                return {
                    "message": f"Disk renamed from '{disk_name}' to '{new_name}' ({renamed_count} snapshots updated)",
                    "old_name": disk_name,
                    "new_name": new_name,
                    "snapshots_updated": renamed_count
                }
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rename disk: {str(e)}"
        ) from e


@app.get("/")
async def root() -> dict[str, Any]:
    """Root endpoint with API information"""
    return {
        "service": "GPU Dev API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "auth": {
            "aws_login": "/v1/auth/aws-login",
            "description": (
                "Use AWS credentials to obtain an API key"
            )
        },
        "endpoints": {
            "jobs": "/v1/jobs",
            "disks": "/v1/disks",
            "disk_operations": "/v1/disks/{disk_name}/operations/{operation_id}",
            "disk_content": "/v1/disks/{disk_name}/content",
            "disk_rename": "/v1/disks/{disk_name}/rename",
            "gpu_availability": "/v1/gpu/availability",
            "cluster_status": "/v1/cluster/status"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
