# GPU Dev API - Endpoints Reference

Quick reference for all API endpoints with examples.

## Base URL

```
Production: https://d174yzuil8470i.cloudfront.net (example)
Local:      http://localhost:8000
```

## Authentication

Most endpoints require an API key obtained via AWS authentication.

### 1. AWS Login

**Endpoint:** `POST /v1/auth/aws-login`  
**Authentication:** None (public)  
**Description:** Exchange AWS credentials for an API key

**Request:**
```json
{
  "aws_access_key_id": "ASIA...",
  "aws_secret_access_key": "...",
  "aws_session_token": "..."
}
```

**Response:**
```json
{
  "api_key": "zHfR3k...",
  "key_prefix": "zHfR3k",
  "user_id": 42,
  "username": "jschmidt",
  "aws_arn": "arn:aws:sts::308535385114:assumed-role/SSOCloudDevGpuReservation/jschmidt",
  "expires_at": "2026-01-20T20:00:00Z",
  "ttl_hours": 2
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/auth/aws-login" \
  -H "Content-Type: application/json" \
  -d '{
    "aws_access_key_id": "ASIA...",
    "aws_secret_access_key": "...",
    "aws_session_token": "..."
  }'
```

---

## Health & Info

### 2. Health Check

**Endpoint:** `GET /health`  
**Authentication:** None (public)  
**Description:** Check API health and dependencies

**Response:**
```json
{
  "status": "healthy",
  "database": "healthy",
  "queue": "healthy",
  "timestamp": "2026-01-20T18:30:00Z"
}
```

**Example:**
```bash
curl "$API_URL/health"
```

### 3. API Info

**Endpoint:** `GET /`  
**Authentication:** None (public)  
**Description:** Get API information and available endpoints

**Response:**
```json
{
  "service": "GPU Dev API",
  "version": "1.0.0",
  "docs": "/docs",
  "health": "/health",
  "auth": {
    "aws_login": "/v1/auth/aws-login",
    "description": "Use AWS credentials to obtain an API key"
  },
  "endpoints": {
    "jobs": "/v1/jobs",
    "disks": "/v1/disks",
    "gpu_availability": "/v1/gpu/availability",
    "cluster_status": "/v1/cluster/status"
  }
}
```

**Example:**
```bash
curl "$API_URL/"
```

---

## Job Management

All job endpoints require authentication: `-H "Authorization: Bearer $API_KEY"`

### 4. Submit Job

**Endpoint:** `POST /v1/jobs/submit`  
**Authentication:** Required  
**Description:** Submit a new GPU job to the queue

**Request:**
```json
{
  "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
  "instance_type": "p5.48xlarge",
  "duration_hours": 4,
  "disk_name": "my-training-data",
  "disk_size_gb": 100,
  "env_vars": {
    "WANDB_API_KEY": "secret",
    "EXPERIMENT": "training-v1"
  },
  "command": "python train.py --epochs 100"
}
```

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "status": "queued",
  "message": "Job submitted successfully to queue (message ID: 42)",
  "estimated_start_time": null
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/jobs/submit" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
    "instance_type": "p5.48xlarge",
    "duration_hours": 4
  }'
```

### 5. Get Job Status

**Endpoint:** `GET /v1/jobs/{job_id}`  
**Authentication:** Required  
**Description:** Get detailed information about a specific job

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "reservation_id": "abc-123-def-456",
  "user_id": "jschmidt@meta.com",
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
  "jupyter_enabled": true,
  "jupyter_url": "https://...",
  "jupyter_token": "token123",
  "github_user": "jeanschmidt"
}
```

**Example:**
```bash
curl "$API_URL/v1/jobs/abc-123-def-456" \
  -H "Authorization: Bearer $API_KEY"
```

### 6. List Jobs

**Endpoint:** `GET /v1/jobs`  
**Authentication:** Required  
**Description:** List jobs for the authenticated user with optional filtering

**Query Parameters:**
- `status` - Filter by status (comma-separated): `active,preparing,queued`
- `limit` - Max results (1-500, default: 50)
- `offset` - Pagination offset (default: 0)

**Response:**
```json
{
  "jobs": [
    {
      "job_id": "abc-123",
      "status": "active",
      "gpu_type": "h100",
      "gpu_count": 4,
      "created_at": "2026-01-20T18:00:00Z",
      ...
    }
  ],
  "total": 10,
  "limit": 50,
  "offset": 0
}
```

**Examples:**
```bash
# List all jobs
curl "$API_URL/v1/jobs" \
  -H "Authorization: Bearer $API_KEY"

# Filter by status
curl "$API_URL/v1/jobs?status=active,preparing" \
  -H "Authorization: Bearer $API_KEY"

# Pagination
curl "$API_URL/v1/jobs?limit=10&offset=20" \
  -H "Authorization: Bearer $API_KEY"
```

### 7. Cancel Job

**Endpoint:** `POST /v1/jobs/{job_id}/cancel`  
**Authentication:** Required  
**Description:** Cancel a running or queued job

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "action": "cancel",
  "status": "requested",
  "message": "Cancellation request submitted (message ID: 42)"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/jobs/abc-123-def-456/cancel" \
  -H "Authorization: Bearer $API_KEY"
```

### 8. Extend Job

**Endpoint:** `POST /v1/jobs/{job_id}/extend`  
**Authentication:** Required  
**Description:** Extend the duration of a running job

**Request:**
```json
{
  "extension_hours": 2
}
```

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "action": "extend",
  "status": "requested",
  "message": "Extension request submitted for 2 hours (message ID: 42)"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/jobs/abc-123-def-456/extend" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"extension_hours": 2}'
```

### 9. Enable Jupyter

**Endpoint:** `POST /v1/jobs/{job_id}/jupyter/enable`  
**Authentication:** Required  
**Description:** Enable Jupyter Lab for a running job

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "action": "enable_jupyter",
  "status": "requested",
  "message": "Jupyter enable request submitted (message ID: 42)"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/jobs/abc-123-def-456/jupyter/enable" \
  -H "Authorization: Bearer $API_KEY"
```

### 10. Disable Jupyter

**Endpoint:** `POST /v1/jobs/{job_id}/jupyter/disable`  
**Authentication:** Required  
**Description:** Disable Jupyter Lab for a running job

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "action": "disable_jupyter",
  "status": "requested",
  "message": "Jupyter disable request submitted (message ID: 42)"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/jobs/abc-123-def-456/jupyter/disable" \
  -H "Authorization: Bearer $API_KEY"
```

### 11. Add User to Job

**Endpoint:** `POST /v1/jobs/{job_id}/users`  
**Authentication:** Required  
**Description:** Add a user's SSH keys to a running job (fetched from GitHub)

**Request:**
```json
{
  "github_username": "jeanschmidt"
}
```

**Response:**
```json
{
  "job_id": "abc-123-def-456",
  "action": "add_user",
  "status": "requested",
  "message": "Add user request submitted for GitHub user 'jeanschmidt' (message ID: 42)"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/jobs/abc-123-def-456/users" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"github_username": "jeanschmidt"}'
```

---

## Cluster Information

### 12. GPU Availability

**Endpoint:** `GET /v1/gpu/availability`  
**Authentication:** Required  
**Description:** Get current GPU availability across all GPU types

**Response:**
```json
{
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
```

**Example:**
```bash
curl "$API_URL/v1/gpu/availability" \
  -H "Authorization: Bearer $API_KEY"
```

### 13. Cluster Status

**Endpoint:** `GET /v1/cluster/status`  
**Authentication:** Required  
**Description:** Get overall cluster status and statistics

**Response:**
```json
{
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
```

**Example:**
```bash
curl "$API_URL/v1/cluster/status" \
  -H "Authorization: Bearer $API_KEY"
```

---

## Disk Operations

### 14. Create Disk

**Endpoint:** `POST /v1/disks`  
**Authentication:** Required  
**Description:** Create a new persistent disk (queued operation)

**Request:**
```json
{
  "disk_name": "my-training-data",
  "size_gb": 500
}
```

**Response:**
```json
{
  "operation_id": "op-123-abc",
  "disk_name": "my-training-data",
  "action": "create",
  "message": "Disk creation request queued successfully",
  "requested_at": "2026-01-20T18:30:00Z"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/disks" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "disk_name": "my-training-data",
    "size_gb": 500
  }'
```

### 15. List Disks

**Endpoint:** `GET /v1/disks`  
**Authentication:** Required  
**Description:** List all persistent disks for the authenticated user

**Response:**
```json
{
  "disks": [
    {
      "disk_name": "my-training-data",
      "user_id": "jschmidt@meta.com",
      "size_gb": 500,
      "created_at": "2026-01-15T10:00:00Z",
      "last_used": "2026-01-20T18:00:00Z",
      "in_use": true,
      "reservation_id": "abc-123",
      "is_backing_up": false,
      "is_deleted": false,
      "snapshot_count": 3
    }
  ],
  "total": 1
}
```

**Example:**
```bash
curl "$API_URL/v1/disks" \
  -H "Authorization: Bearer $API_KEY"
```

### 16. Get Disk Info

**Endpoint:** `GET /v1/disks/{disk_name}`  
**Authentication:** Required  
**Description:** Get detailed information about a specific disk

**Response:**
```json
{
  "disk_name": "my-training-data",
  "user_id": "jschmidt@meta.com",
  "size_gb": 500,
  "created_at": "2026-01-15T10:00:00Z",
  "last_used": "2026-01-20T18:00:00Z",
  "in_use": true,
  "reservation_id": "abc-123",
  "is_backing_up": false,
  "is_deleted": false,
  "snapshot_count": 3
}
```

**Example:**
```bash
curl "$API_URL/v1/disks/my-training-data" \
  -H "Authorization: Bearer $API_KEY"
```

### 17. Delete Disk

**Endpoint:** `DELETE /v1/disks/{disk_name}`  
**Authentication:** Required  
**Description:** Delete a persistent disk (soft delete with 30-day retention)

**Response:**
```json
{
  "operation_id": "op-456-def",
  "disk_name": "my-training-data",
  "action": "delete",
  "message": "Disk deletion request queued successfully. Will be deleted on 2026-02-19",
  "requested_at": "2026-01-20T18:30:00Z"
}
```

**Example:**
```bash
curl -X DELETE "$API_URL/v1/disks/my-training-data" \
  -H "Authorization: Bearer $API_KEY"
```

### 18. Get Disk Operation Status

**Endpoint:** `GET /v1/disks/{disk_name}/operations/{operation_id}`  
**Authentication:** Required  
**Description:** Poll the status of a disk operation (create/delete)

**Response:**
```json
{
  "operation_id": "op-123-abc",
  "disk_name": "my-training-data",
  "status": "completed",
  "error": null,
  "is_deleted": false,
  "delete_date": null,
  "created_at": "2026-01-20T18:30:00Z",
  "last_updated": "2026-01-20T18:35:00Z",
  "completed": true
}
```

**Example:**
```bash
curl "$API_URL/v1/disks/my-training-data/operations/op-123-abc" \
  -H "Authorization: Bearer $API_KEY"
```

---

### 19. Rename Disk

**Endpoint:** `POST /v1/disks/{disk_name}/rename`  
**Authentication:** Required  
**Description:** Rename a persistent disk

Updates the disk name in PostgreSQL and updates tags on all associated EBS snapshots.
The disk must not be in use during the rename operation.

**Request:**
```json
{
  "new_name": "new-disk-name"
}
```

**Response (Success):**
```json
{
  "message": "Disk renamed from 'old-disk-name' to 'new-disk-name' (3 snapshots updated)",
  "old_name": "old-disk-name",
  "new_name": "new-disk-name",
  "snapshots_updated": 3
}
```

**Response (No Snapshots):**
```json
{
  "message": "Disk renamed from 'old-disk-name' to 'new-disk-name' (no snapshots found)",
  "old_name": "old-disk-name",
  "new_name": "new-disk-name",
  "snapshots_updated": 0
}
```

**Response (Partial Success):**
```json
{
  "message": "Disk renamed from 'old-disk-name' to 'new-disk-name' (2/3 snapshots updated)",
  "old_name": "old-disk-name",
  "new_name": "new-disk-name",
  "snapshots_updated": 2,
  "errors": [
    "snap-1234567890abcdef: Access denied"
  ]
}
```

**Error Responses:**
- **400 Bad Request** - Invalid disk name format (must be alphanumeric + hyphens + underscores)
- **404 Not Found** - Disk doesn't exist
- **409 Conflict** - Disk is currently in use OR new name already exists
- **410 Gone** - Disk is marked for deletion

**Example:**
```bash
curl -X POST "$API_URL/v1/disks/my-training-data/rename" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "new_name": "my-training-data-v2"
  }'
```

**Constraints:**
- Disk must not be in use (not attached to any reservation)
- Disk must not be marked for deletion
- New name must be unique for the user
- New name must contain only letters, numbers, hyphens, and underscores

---

## API Key Management

### 20. Rotate API Key

**Endpoint:** `POST /v1/keys/rotate`  
**Authentication:** Required  
**Description:** Generate a new API key with a fresh TTL

**Response:**
```json
{
  "api_key": "new-key-xyz...",
  "key_prefix": "new-key-",
  "user_id": 42,
  "username": "jschmidt",
  "expires_at": "2026-01-20T22:00:00Z"
}
```

**Example:**
```bash
curl -X POST "$API_URL/v1/keys/rotate" \
  -H "Authorization: Bearer $API_KEY"
```

---

## Error Responses

### Common HTTP Status Codes

| Code | Meaning | When |
|------|---------|------|
| 200 | OK | Request succeeded |
| 400 | Bad Request | Invalid input (e.g., missing required fields) |
| 401 | Unauthorized | Invalid or missing API key |
| 403 | Forbidden | Valid API key but insufficient permissions |
| 404 | Not Found | Resource doesn't exist (e.g., job_id not found) |
| 500 | Internal Server Error | Server-side error |

### Error Response Format

```json
{
  "detail": "Invalid API key"
}
```

or for validation errors:

```json
{
  "detail": [
    {
      "type": "missing",
      "loc": ["body", "image"],
      "msg": "Field required",
      "input": {...}
    }
  ]
}
```

---

## Interactive API Documentation

The API provides interactive documentation via Swagger UI:

**URL:** `https://your-api-url/docs`

Features:
- Browse all endpoints
- Try endpoints directly from the browser
- View request/response schemas
- See example payloads

---

## Quick Start Script

```bash
#!/bin/bash
# Quick start script for GPU Dev API

# 1. Get AWS credentials
eval $(cloud_corp aws get-credentials fbossci --role SSOCloudDevGpuReservation --output cli)

# 2. Get API key
API_URL="https://d174yzuil8470i.cloudfront.net"
RESPONSE=$(curl -s -X POST "$API_URL/v1/auth/aws-login" \
  -H "Content-Type: application/json" \
  -d "{
    \"aws_access_key_id\": \"$AWS_ACCESS_KEY_ID\",
    \"aws_secret_access_key\": \"$AWS_SECRET_ACCESS_KEY\",
    \"aws_session_token\": \"$AWS_SESSION_TOKEN\"
  }")

API_KEY=$(echo "$RESPONSE" | jq -r .api_key)
echo "API Key: ${API_KEY:0:8}..."

# 3. Submit a job
JOB_RESPONSE=$(curl -s -X POST "$API_URL/v1/jobs/submit" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
    "instance_type": "p5.48xlarge",
    "duration_hours": 4
  }')

JOB_ID=$(echo "$JOB_RESPONSE" | jq -r .job_id)
echo "Job ID: $JOB_ID"

# 4. Check job status
curl -s "$API_URL/v1/jobs/$JOB_ID" \
  -H "Authorization: Bearer $API_KEY" | jq .
```

---

## Related Documentation

- [API Service README](./README.md) - Architecture and deployment

---

## Changelog

### 2026-01-20
- ✨ Initial comprehensive API reference
- 📝 All 20 endpoints documented with examples
- 📝 Added disk rename endpoint documentation
- 📝 Added error handling reference
- 📝 Added quick start script

