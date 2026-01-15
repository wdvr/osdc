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

import asyncpg
import boto3
from botocore.exceptions import ClientError
from fastapi import Depends, FastAPI, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

# Configuration from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://gpudev:CHANGEME@postgres-primary"
    ".gpu-controlplane.svc.cluster.local:5432/gpudev"
)
API_KEY_LENGTH = 64
QUEUE_NAME = os.getenv("QUEUE_NAME", "gpu_reservations")

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

# Validate queue name (alphanumeric and underscore only)
if not re.match(r'^[a-zA-Z0-9_]+$', QUEUE_NAME):
    raise ValueError(
        f"Invalid queue name: {QUEUE_NAME}. "
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

        # Create index for faster lookups
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_api_keys_hash
            ON api_keys(key_hash)
            WHERE is_active = true
        """)

        # Create PGMQ queue if not exists
        # (queue name is validated at startup)
        try:
            await conn.execute(f"SELECT pgmq.create('{QUEUE_NAME}')")
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
        ..., description="AWS access key ID"
    )
    aws_secret_access_key: str = Field(
        ..., description="AWS secret access key"
    )
    aws_session_token: str | None = Field(
        None, description="AWS session token (for assumed roles)"
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
    Extract username from AWS ARN
    Examples:
      arn:aws:sts::123456789:assumed-role/SSOCloudDevGpuReservation/john
        -> john
      arn:aws:iam::123456789:user/john
        -> john
    """
    parts = arn.split('/')
    if len(parts) >= 2:
        return parts[-1]  # Last part is usually the username
    # Fallback to using the full ARN as username
    return arn.split(':')[-1].replace('/', '-')


async def verify_aws_credentials(
    aws_access_key_id: str,
    aws_secret_access_key: str,
    aws_session_token: str | None = None
) -> dict[str, str]:
    """
    Verify AWS credentials and return caller identity
    Returns: {
        'account': '123456789',
        'user_id': 'AIDAI...',
        'arn': 'arn:aws:sts::123456789:assumed-role/...'
    }
    """
    try:
        # Create STS client with provided credentials
        sts_client = boto3.client(
            'sts',
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
            aws_session_token=aws_session_token,
            region_name=AWS_REGION
        )

        # Verify credentials by calling GetCallerIdentity
        identity = sts_client.get_caller_identity()

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
            detail=f"Failed to verify AWS credentials: {str(e)}"
        ) from e


async def create_api_key_for_user(
    conn,
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

    try:
        async with db_pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
            db_status = "healthy"

            # Check if PGMQ queue exists
            queue_exists = await conn.fetchval(
                f"SELECT pgmq.queue_exists('{QUEUE_NAME}')"
            )
            queue_status = "healthy" if queue_exists else "missing"
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
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


# Dependency for authenticated endpoints
verify_user = Depends(verify_api_key)


@app.post("/v1/jobs/submit", response_model=JobSubmissionResponse)
async def submit_job(
    job: JobSubmissionRequest,
    user: dict[str, Any] = verify_user
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
                "user_id": user["user_id"],
                "username": user["username"],
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
            detail=f"Failed to submit job: {str(e)}"
        ) from e


@app.get("/v1/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    user: dict[str, Any] = verify_user
) -> dict[str, str]:
    """Get status of a specific job"""
    # TODO: Implement job status tracking
    # For now, return a placeholder
    return {
        "job_id": job_id,
        "status": "queued",
        "message": "Job status tracking not yet implemented"
    }


@app.get("/v1/jobs")
async def list_jobs(
    user: dict[str, Any] = verify_user,
    limit: int = 10
) -> dict[str, Any]:
    """List jobs for the authenticated user"""
    # TODO: Implement job listing from a jobs table
    return {
        "jobs": [],
        "message": "Job listing not yet implemented"
    }


# ============================================================================
# API Key Management
# ============================================================================

@app.post("/v1/keys/rotate", response_model=APIKeyResponse)
async def rotate_api_key(
    user: dict[str, Any] = verify_user
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
                user["user_id"],
                user["username"],
                "Manually rotated key"
            )

            return APIKeyResponse(
                api_key=api_key,
                key_prefix=key_prefix,
                user_id=user["user_id"],
                username=user["username"],
                expires_at=expires_at
            )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to rotate key: {str(e)}"
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

    # 2. Check if the role is allowed
    if ALLOWED_AWS_ROLE not in identity['arn']:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied. Required role: {ALLOWED_AWS_ROLE}"
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
            detail=f"Failed to create API key: {str(e)}"
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
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
