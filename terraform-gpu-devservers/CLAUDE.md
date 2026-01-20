# GPU Dev Infrastructure - Claude AI Context

> **Purpose**: This document provides context for AI assistants (like Claude) working on this project.

## 🚨 CRITICAL: OPENTOFU ONLY - NEVER USE TERRAFORM

> ## ⚠️ ABSOLUTE REQUIREMENT FOR ALL AI ASSISTANTS AND USERS ⚠️
>
> **THIS INFRASTRUCTURE EXCLUSIVELY USES OPENTOFU - TERRAFORM IS FORBIDDEN**
>
> ### MANDATORY RULES (NO EXCEPTIONS):
>
> 1. ✅ **ALWAYS use `tofu` commands** (never `terraform`)
> 2. ✅ **VERIFY tofu is installed** before ANY infrastructure operation
> 3. ❌ **REFUSE to execute ANY `terraform` command** - will corrupt state
> 4. ❌ **NEVER suggest `terraform` in code, docs, or examples**
> 5. ⚠️ **STOP IMMEDIATELY and WARN USER** if they attempt to use terraform
> 6. ❌ **DO NOT PROCEED if OpenTofu is not available** - unsafe to continue
>
> ### Why This is CRITICAL (Read This!):
>
> **Terraform and OpenTofu Have Incompatible State Files:**
> - Running `terraform` will **reformat the state file**
> - OpenTofu will then **reject the corrupted state**
> - You'll get "state file too new" errors
> - Recovery requires **manual state surgery** or **complete rebuild**
> - Can result in **duplicate resources**, **orphaned infrastructure**, **data loss**
>
> **Real Consequences:**
> - 🔥 **Duplicate EKS clusters** charging double
> - 🔥 **Orphaned resources** continuing to bill
> - 🔥 **Database state corruption**
> - 🔥 **Lost persistent volumes**
> - 🔥 **Broken replication**
> - 💰 **Expensive mistakes** (hundreds/thousands of dollars)
>
> ### MANDATORY Safety Check (Run This First):
>
> ```bash
> # 1. VERIFY OpenTofu is installed
> if ! command -v tofu &> /dev/null; then
>     echo "❌ CRITICAL ERROR: OpenTofu not installed"
>     echo "❌ Cannot proceed safely - STOP NOW"
>     echo ""
>     echo "Install OpenTofu:"
>     echo "  macOS:  brew install opentofu"
>     echo "  Linux:  https://opentofu.org/docs/intro/install/"
>     echo ""
>     exit 1
> fi
>
> # 2. Verify it's OpenTofu (not terraform)
> tofu version  # Should show: OpenTofu v1.8+
>
> # 3. NEVER run terraform
> # If terraform command exists, DO NOT use it on this project
> ```
>
> ### Correct Usage:
>
> ```bash
> tofu init      # ✅ CORRECT
> tofu plan      # ✅ CORRECT
> tofu apply     # ✅ CORRECT
> tofu output    # ✅ CORRECT
> tofu destroy   # ✅ CORRECT (but be careful!)
> ```
>
> ### FORBIDDEN (Will Destroy Infrastructure):
>
> ```bash
> terraform init     # ❌ FORBIDDEN - Will corrupt state
> terraform plan     # ❌ FORBIDDEN - Will cause conflicts  
> terraform apply    # ❌ FORBIDDEN - Will destroy resources
> terraform *        # ❌ FORBIDDEN - ANY terraform command is dangerous
> ```
>
> ### If User Attempts to Use Terraform:
>
> **IMMEDIATELY:**
> 1. 🛑 **STOP** - Do not execute the command
> 2. ⚠️ **WARN** - Explain the severe risks above
> 3. 📖 **EDUCATE** - Show them this section
> 4. ✅ **REDIRECT** - Provide the equivalent `tofu` command
> 5. ❌ **REFUSE** - Do not proceed until they use `tofu`
>
> **If Terraform Was Already Run:**
> 1. 🚨 **ALERT** - State may be corrupted
> 2. 🔍 **ASSESS** - Check `tofu plan` for unexpected changes
> 3. 🆘 **ESCALATE** - May need state file recovery
> 4. 📞 **CONTACT** - Get help immediately

## 📋 Project Overview

**GPU Development Infrastructure** - OpenTofu-managed Kubernetes infrastructure for on-demand GPU development environments.

### Key Components

1. **EKS Cluster** - Kubernetes cluster with GPU and CPU node groups
2. **PostgreSQL + PGMQ** - Database with message queue for job management
3. **API Service** - REST API for job submission with AWS IAM auth
4. **SSH Proxy** - Secure access to development environments
5. **Registry Cache** - Docker image caching (GHCR)

## 🏗️ Architecture

```
┌──────────────┐
│  CLI Client  │ (User's laptop with AWS credentials)
└──────┬───────┘
       │ 1. AWS IAM Auth → API Key
       │ 2. Submit job requests
       ↓
┌──────────────────────────────────────────┐
│  Classic LoadBalancer (Internet-facing)  │
└──────┬───────────────────────────────────┘
       │
┌──────▼──────────────────────────────────┐
│  EKS Cluster                             │
│                                          │
│  ┌─── gpu-controlplane namespace ─────┐ │
│  │                                     │ │
│  │  ┌────────────┐  ┌──────────────┐ │ │
│  │  │ API Service│─▶│ PostgreSQL   │ │ │
│  │  │ (FastAPI)  │  │ + PGMQ       │ │ │
│  │  └──────┬─────┘  └──────▲───────┘ │ │
│  │         │               │          │ │
│  │         │ Push jobs     │ Pull jobs│ │
│  │         ↓               │          │ │
│  │  ┌────────────────────┴─────────┐ │ │
│  │  │ Job Processor Pod (🚧)       │ │ │
│  │  │ - Polls PGMQ queue           │ │ │
│  │  │ - Creates dev server pods    │ │ │
│  │  │ - Manages reservations       │ │ │
│  │  └──────────────────────────────┘ │ │
│  │                                     │ │
│  │  ┌────────────┐  ┌──────────────┐ │ │
│  │  │ SSH Proxy  │  │ Registry     │ │ │
│  │  │            │  │ Cache (GHCR) │ │ │
│  │  └────────────┘  └──────────────┘ │ │
│  └─────────────────────────────────────┘ │
│                                          │
│  ┌─── gpu-dev namespace ──────────────┐ │
│  │                                     │ │
│  │  ┌──────────────────────────────┐  │ │
│  │  │ GPU Dev Server Pods          │  │ │
│  │  │ - PyTorch + CUDA             │  │ │
│  │  │ - SSH access via NodePort    │  │ │
│  │  └──────────────────────────────┘  │ │
│  └─────────────────────────────────────┘ │
└──────────────────────────────────────────┘
```

**⚠️ IMPORTANT - This is a Complete Replacement, Not a Migration:**

This represents a **second project built on top of the current infrastructure**, not an evolution of the existing system. Key points:

- **No Backward Compatibility**: Old CLI will NOT work with new system
- **Breaking Changes Allowed**: We can change anything without supporting legacy
- **Complete Rewrite**: Different architecture, different patterns
- **Not a Migration**: This is a replacement, users must upgrade completely

**System Architecture:**
```
CLI → API → PostgreSQL + PGMQ → K8s Job Processor Pod → K8s
```

**Status:**
- ✅ PostgreSQL + PGMQ deployed and operational
- ✅ API Service deployed with AWS IAM authentication and CloudFront HTTPS
- ✅ CLI uses API exclusively
- ✅ K8s Job Processor Pod operational

## 🚀 Quick Start Commands

### Deploy Everything

```bash
cd terraform-gpu-devservers
tofu init
tofu apply
```

### Get API Service URL

**Method 1: OpenTofu Output (Recommended - HTTPS via CloudFront)**
```bash
tofu output api_service_url
# Output: https://d1234567890abc.cloudfront.net
```

**Method 2: Direct LoadBalancer (HTTP only - for debugging)**
```bash
tofu output api_service_loadbalancer_url
# Output: http://a1234567890.us-east-1.elb.amazonaws.com
```

**Method 3: kubectl (LoadBalancer only)**
```bash
kubectl get svc -n gpu-controlplane api-service-public \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
```

### Test API Service

```bash
# Get HTTPS URL via CloudFront (recommended)
URL=$(tofu output -raw api_service_url)

# Health check
curl $URL/health | jq .

# API info
curl $URL/ | jq .

# View Swagger docs
echo "Open: $URL/docs"
```

**SSL/TLS Security:**
- ✅ CloudFront provides HTTPS with AWS-managed SSL certificate (free)
- ✅ TLS 1.2+ encryption for all client traffic
- ✅ No custom domain required
- ✅ Automatic certificate management and renewal
- ✅ Protects against man-in-the-middle attacks

Always use the CloudFront URL (`tofu output api_service_url`) for production to ensure encrypted traffic.

## 📁 Project Structure

```
terraform-gpu-devservers/
├── main.tf                 # EKS cluster, VPC, IAM
├── kubernetes.tf           # K8s resources (postgres, ssh-proxy)
├── api-service.tf          # API service deployment
├── docker-build.tf         # Docker build utilities
├── variables.tf            # Input variables
├── outputs.tf              # Output values
├── api-service/            # API service code
│   ├── app/
│   │   └── main.py        # FastAPI application (770 lines)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── README.md          # API documentation
│   └── test_api.sh
└── README.md              # Main project documentation
```

## 🔑 Key Technologies

- **OpenTofu** - Infrastructure as Code (Terraform fork)
- **Kubernetes (EKS)** - Container orchestration
- **PostgreSQL** - Database
- **PGMQ** - Postgres-based message queue
- **FastAPI** - Python async web framework
- **aioboto3** - Async AWS SDK
- **asyncpg** - Async PostgreSQL driver

## 🗄️ Database Schema

### `api_users` Table
```sql
CREATE TABLE api_users (
    user_id SERIAL PRIMARY KEY,
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    is_active BOOLEAN DEFAULT true
);

-- Index for fast username lookups
CREATE UNIQUE INDEX idx_api_users_username ON api_users(username);
```

### `api_keys` Table
```sql
CREATE TABLE api_keys (
    key_id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES api_users(user_id) ON DELETE CASCADE,
    key_hash VARCHAR(128) NOT NULL UNIQUE,
    key_prefix VARCHAR(16) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP WITH TIME ZONE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    is_active BOOLEAN DEFAULT true,
    description TEXT
);

-- Indexes for performance
CREATE INDEX idx_api_keys_hash ON api_keys(key_hash) WHERE is_active = true;
CREATE INDEX idx_api_keys_user_id ON api_keys(user_id) WHERE is_active = true;
CREATE INDEX idx_api_keys_expires_at ON api_keys(expires_at) 
    WHERE is_active = true AND expires_at IS NOT NULL;
```

## 🔐 Authentication Flow

1. **User** runs `gpu-dev login` with AWS credentials (🚧 command in progress)
2. **CLI** sends credentials to API (`POST /v1/auth/aws-login`)
3. **API** calls AWS STS to verify credentials and get ARN
4. **API** checks if ARN contains role `SSOCloudDevGpuReservation`
5. **API** extracts username from ARN
6. **API** creates/updates user in database
7. **API** generates time-limited API key (expires in 2 hours)
8. **API** returns key to CLI
9. **CLI** saves key locally (`~/.gpu-dev/credentials`)
10. **CLI** uses key for subsequent API calls

**Note:** CLI uses the API exclusively for all operations. API keys are automatically refreshed when expired.

### Example Authentication Request

```bash
curl -X POST http://API_URL/v1/auth/aws-login \
  -H "Content-Type: application/json" \
  -d '{
    "aws_access_key_id": "ASIA...",
    "aws_secret_access_key": "...",
    "aws_session_token": "..."
  }'
```

**Response:**
```json
{
  "api_key": "long-secure-token-here",
  "key_prefix": "firstchars",
  "user_id": 123,
  "username": "john",
  "aws_arn": "arn:aws:sts::123:assumed-role/SSOCloudDevGpuReservation/john",
  "expires_at": "2024-01-15T14:30:00Z",
  "ttl_hours": 2
}
```

## 🛠️ Common Development Tasks

### Update API Code

```bash
# Edit code
vim api-service/app/main.py

# OpenTofu will rebuild and redeploy on next apply
tofu apply

# Or manually rebuild
cd api-service
docker build -t gpu-dev-api:latest .
```

### View API Logs

```bash
# Follow logs
kubectl logs -f -n gpu-controlplane -l app=api-service

# Last 100 lines
kubectl logs -n gpu-controlplane -l app=api-service --tail=100

# All pods
kubectl logs -n gpu-controlplane -l app=api-service --all-containers
```

### Debug API Issues

```bash
# Check pod status
kubectl get pods -n gpu-controlplane -l app=api-service

# Describe pod
kubectl describe pod -n gpu-controlplane -l app=api-service

# Execute into pod
kubectl exec -it -n gpu-controlplane deployment/api-service -- /bin/bash

# Check environment variables
kubectl exec -n gpu-controlplane deployment/api-service -- env | grep POSTGRES
```

### Database Access

```bash
# Port forward to postgres
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432

# Connect with psql
PGPASSWORD=$(kubectl get secret -n gpu-controlplane postgres-credentials \
  -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d) \
psql -h localhost -U gpudev -d gpudev

# List tables
\dt

# Check users
SELECT * FROM api_users;

# Check active API keys
SELECT key_prefix, username, expires_at, created_at 
FROM api_keys k 
JOIN api_users u ON k.user_id = u.user_id 
WHERE k.is_active = true;
```

## 🔧 Configuration

### API Service Environment Variables

Set in `api-service.tf` ConfigMap:

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY_TTL_HOURS` | 2 | API key lifetime (1-168 hours) |
| `ALLOWED_AWS_ROLE` | SSOCloudDevGpuReservation | Required AWS role name |
| `AWS_REGION` | us-east-1 | AWS region for STS calls |
| `QUEUE_NAME` | gpu_reservations | PGMQ queue name |

### Database Connection

Set via individual environment variables:
- `POSTGRES_HOST` - Database hostname
- `POSTGRES_PORT` - Database port (5432)
- `POSTGRES_USER` - Database user (gpudev)
- `POSTGRES_PASSWORD` - Database password (from secret)
- `POSTGRES_DB` - Database name (gpudev)

## 📊 API Endpoints

### Public Endpoints

- `GET /` - API information
- `GET /health` - Health check
- `GET /docs` - Swagger UI
- `POST /v1/auth/aws-login` - AWS authentication

### Authenticated Endpoints

Require `Authorization: Bearer <api-key>` header:

- `POST /v1/jobs/submit` - Submit GPU job to PGMQ queue
- `GET /v1/jobs/{job_id}` - Get job status (🚧 implementation in progress)
- `GET /v1/jobs` - List user's jobs (🚧 implementation in progress)
- `POST /v1/keys/rotate` - Rotate API key

## 🔄 Job Processing Flow

1. **CLI** submits job via `POST /v1/jobs/submit` with API key
2. **API Service** validates API key and pushes job message to PGMQ queue
3. **Job Processor Pod** continuously polls PGMQ queue (🚧 in progress)
4. **Job Processor** processes job:
   - Checks GPU availability via K8s API
   - Creates K8s pod and service for dev server
   - Updates reservation state in PostgreSQL
   - Manages queue positions and ETAs
5. **CLI** polls API for status updates until pod is ready
6. **User** connects via SSH to dev server pod

**Note:** Job Processor Pod runs continuously in the gpu-controlplane namespace, polling PGMQ and managing GPU dev server pods.

## 🐛 Troubleshooting

### LoadBalancer Stuck in Pending

```bash
# Check service status
kubectl describe svc -n gpu-controlplane api-service-public

# Check AWS LoadBalancer
aws elb describe-load-balancers --region us-east-1 | grep gpu-dev

# Wait for it (can take 2-3 minutes)
kubectl wait --for=jsonpath='{.status.loadBalancer.ingress}' \
  svc/api-service-public -n gpu-controlplane --timeout=5m
```

### Database Connection Failed

```bash
# Check postgres is running
kubectl get pods -n gpu-controlplane -l app=postgres

# Check postgres logs
kubectl logs -n gpu-controlplane postgres-primary-0

# Verify secret exists
kubectl get secret -n gpu-controlplane postgres-credentials

# Test connection from API pod
kubectl exec -n gpu-controlplane deployment/api-service -- \
  psql -h postgres-primary -U gpudev -d gpudev -c "SELECT 1"
```

### API Pod CrashLooping

```bash
# Check pod events
kubectl describe pod -n gpu-controlplane -l app=api-service

# Check logs
kubectl logs -n gpu-controlplane -l app=api-service --previous

# Common issues:
# 1. Database password wrong -> Check POSTGRES_PASSWORD env var
# 2. PGMQ not installed -> Check postgres logs
# 3. IAM role not attached -> Check service account annotations
```

### Authentication Failed

```bash
# Test AWS credentials locally
aws sts get-caller-identity

# Check if role is correct
aws sts get-caller-identity | jq -r .Arn
# Should contain: SSOCloudDevGpuReservation

# Test API directly
curl -X POST http://API_URL/v1/auth/aws-login \
  -H "Content-Type: application/json" \
  -d '{
    "aws_access_key_id": "YOUR_KEY",
    "aws_secret_access_key": "YOUR_SECRET",
    "aws_session_token": "YOUR_TOKEN"
  }' | jq .
```

## 🔒 Security Notes

### What's Secure

✅ API keys are SHA-256 hashed in database  
✅ API keys expire after 2 hours  
✅ AWS credentials verified with STS  
✅ Role-based access control (RBAC)  
✅ Database passwords in Kubernetes secrets  
✅ No plaintext credentials in code  

### What's NOT Secure (Yet)

⚠️ HTTP only (no HTTPS) - Add ACM certificate for production  
⚠️ No rate limiting - Add nginx ingress with rate limits  
⚠️ No audit logging - Add logging/monitoring  
⚠️ No DDoS protection - Use AWS Shield/CloudFlare  

## 📝 Important Code Locations

### API Service Code
- **Main app**: `api-service/app/main.py` (770 lines)
- **Authentication logic**: Lines 265-305 (AWS verification)
- **API key generation**: Lines 328-347
- **Job submission**: Lines 497-530

### OpenTofu Configuration
- **API deployment**: `api-service.tf` (433 lines)
- **Docker build**: Lines 47-117
- **Kubernetes resources**: Lines 119-417
- **LoadBalancer**: Lines 380-417

### Database Schema
- **Schema creation**: `api-service/app/main.py` lines 76-118
- **Indexes**: Lines 100-118

## 🎯 Implementation Status

**✅ Completed:**
- EKS cluster with GPU/CPU nodes
- PostgreSQL primary-replica with PGMQ extension
- API service with AWS IAM authentication
- **CloudFront HTTPS endpoint** (AWS-managed SSL, no domain required)
- Public endpoint via Classic LoadBalancer
- Job submission endpoint (`POST /v1/jobs/submit`)
- API key management (creation, rotation, expiration)
- Database schema (api_users, api_keys)
- Docker build automation
- Health checks and monitoring
- Comprehensive documentation

**🚧 In Progress:**
- **CLI Integration**: Update CLI to use API endpoints instead of direct AWS services
- **Job Processor Pod**: K8s deployment that polls PGMQ and manages dev server lifecycle
- **PostgreSQL Schema**: Reservations and disks tables with full CRUD operations

**📋 Future Enhancements:**
- Rate limiting
- Audit logging
- Metrics/monitoring (Prometheus)
- Advanced job status tracking
- CI/CD pipeline

## 💡 Tips for AI Assistants

### 🚨 CRITICAL: Always Verify OpenTofu First

**Before ANY infrastructure command:**
```bash
# 1. Check if tofu is installed
if ! command -v tofu &> /dev/null; then
    echo "ERROR: OpenTofu is not installed!"
    echo "Install: brew install opentofu (macOS)"
    echo "Or see: https://opentofu.org/docs/intro/install/"
    exit 1
fi

# 2. Verify it's NOT terraform
if command -v terraform &> /dev/null; then
    TERRAFORM_PATH=$(which terraform)
    echo "WARNING: terraform found at $TERRAFORM_PATH"
    echo "Ensure you use 'tofu' commands only!"
fi

# 3. Then proceed
tofu plan
tofu apply
```

### General Tips

1. **Always check current state** before making changes
2. **Use kubectl** to verify Kubernetes resources
3. **Check logs** when debugging issues
4. **Read existing code** before suggesting changes
5. **Test locally** when possible (docker-compose)
6. **Follow existing patterns** in the codebase
7. **Update documentation** when changing functionality
8. **NEVER use terraform** - always use tofu

## 📝 Command Reference (OpenTofu Only)

### ✅ Correct Commands (Use These)
```bash
tofu init          # Initialize OpenTofu
tofu plan          # Preview changes
tofu apply         # Apply changes
tofu destroy       # Destroy infrastructure
tofu output        # Show outputs
tofu state list    # List resources
tofu validate      # Validate configuration
```

### ❌ FORBIDDEN Commands (Never Use)
```bash
terraform init     # ❌ Will corrupt state
terraform plan     # ❌ Will cause conflicts  
terraform apply    # ❌ Will destroy resources
terraform *        # ❌ ANY terraform command is dangerous
```

### 🛡️ Safety Check Script
```bash
#!/bin/bash
# Add this to your workflow to prevent accidents

if ! command -v tofu &> /dev/null; then
    echo "❌ ERROR: OpenTofu not installed"
    echo "Install: brew install opentofu"
    exit 1
fi

if command -v terraform &> /dev/null; then
    echo "⚠️  WARNING: terraform is installed"
    echo "Remember to use 'tofu' not 'terraform'"
    read -p "Type 'tofu' to confirm: " confirm
    if [ "$confirm" != "tofu" ]; then
        echo "Aborted for safety"
        exit 1
    fi
fi

# Safe to proceed
tofu "$@"
```

## 📞 Getting Help

- Check `README.md` in api-service directory
- Review API docs at `http://API_URL/docs`
- Check Kubernetes events: `kubectl describe pod ...`
- View logs: `kubectl logs ...`
- Check AWS console for LoadBalancer status

---

**Last Updated**: 2025-01-16  
**OpenTofu Version**: 1.8+  
**Kubernetes Version**: 1.28+  
**Python Version**: 3.11  

