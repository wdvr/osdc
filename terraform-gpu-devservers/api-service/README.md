# GPU Dev API Service

REST API service for submitting GPU development jobs using PGMQ (PostgreSQL Message Queue).

## Features

- **API Key Authentication**: Secure token-based authentication
- **Job Submission**: Submit GPU reservation requests to PGMQ
- **User Management**: Create users and manage API keys
- **Health Checks**: Monitor service and database health
- **Auto-generated Docs**: Swagger UI at `/docs`

## Architecture

```
[CLI Client] --HTTPS--> [ALB + ACM] --HTTP--> [K8s Service] --HTTP--> [API Pod]
                                                                          |
                                                                          v
                                                                    [Postgres/PGMQ]
```

## API Endpoints

### Public Endpoints

- `GET /` - API information
- `GET /health` - Health check
- `GET /docs` - Swagger UI documentation

### Authenticated Endpoints (require API key)

- `POST /v1/jobs/submit` - Submit a new job
- `GET /v1/jobs/{job_id}` - Get job status
- `GET /v1/jobs` - List user's jobs
- `POST /v1/keys/rotate` - Generate a new API key

### Admin Endpoints

- `POST /admin/users` - Create a new user and API key

## Authentication

All authenticated endpoints require an API key in the Authorization header:

```bash
Authorization: Bearer <your-api-key>
```

## Local Development

### Prerequisites

- Python 3.11+
- PostgreSQL with PGMQ extension
- Running postgres instance (see terraform-gpu-devservers)

### Setup

```bash
cd terraform-gpu-devservers/api-service

# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Set database URL
export DATABASE_URL="postgresql://gpudev:password@localhost:5432/gpudev"

# Run development server
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Visit http://localhost:8000/docs for interactive API documentation.

### Create a Test User

```bash
curl -X POST http://localhost:8000/admin/users \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com"
  }'
```

Save the returned API key!

### Submit a Test Job

```bash
curl -X POST http://localhost:8000/v1/jobs/submit \
  -H "Authorization: Bearer <your-api-key>" \
  -H "Content-Type: application/json" \
  -d '{
    "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
    "instance_type": "p5.48xlarge",
    "duration_hours": 4,
    "disk_name": "my-training-data",
    "command": "python train.py"
  }'
```

## Docker Build

```bash
docker build -t gpu-dev-api:latest .
docker run -p 8000:8000 \
  -e DATABASE_URL="postgresql://gpudev:password@host.docker.internal:5432/gpudev" \
  gpu-dev-api:latest
```

## Database Schema

### `api_users` Table

| Column | Type | Description |
|--------|------|-------------|
| user_id | SERIAL | Primary key |
| username | VARCHAR(255) | Unique username |
| email | VARCHAR(255) | User email |
| created_at | TIMESTAMP | Account creation time |
| is_active | BOOLEAN | Account status |

### `api_keys` Table

| Column | Type | Description |
|--------|------|-------------|
| key_id | SERIAL | Primary key |
| user_id | INTEGER | Foreign key to users |
| key_hash | VARCHAR(128) | SHA-256 hash of API key |
| key_prefix | VARCHAR(16) | First 8 chars for identification |
| created_at | TIMESTAMP | Key creation time |
| expires_at | TIMESTAMP | Expiration time (optional) |
| last_used_at | TIMESTAMP | Last usage timestamp |
| is_active | BOOLEAN | Key status |
| description | TEXT | Key description |

## Security Considerations

### Current Implementation

- API keys are SHA-256 hashed before storage
- Keys are 64 bytes (512 bits) of cryptographically secure randomness
- Keys can be rotated without losing access
- Keys can be revoked individually
- User accounts can be disabled

### Production Recommendations

1. **Protect Admin Endpoints**: Add admin authentication or make internal-only
2. **Rate Limiting**: Add rate limiting to prevent abuse
3. **HTTPS Only**: Enforce TLS in production (handled by ALB)
4. **Key Expiration**: Consider adding automatic key expiration
5. **Audit Logging**: Log all API access for security monitoring
6. **Input Validation**: Already implemented with Pydantic
7. **Database Credentials**: Use secrets management (K8s secrets)

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| DATABASE_URL | postgres://gpudev:...@postgres-primary... | PostgreSQL connection string |
| API_KEY_LENGTH | 64 | Length of generated API keys |
| QUEUE_NAME | gpu_reservations | PGMQ queue name |

## Next Steps

1. Deploy to Kubernetes (see terraform config)
2. Integrate with CLI tool for automatic API key usage
3. Add job status tracking table and endpoints
4. Implement queue position estimation
5. Add metrics and monitoring (Prometheus)
6. Add request rate limiting
7. Implement webhook notifications for job status changes

