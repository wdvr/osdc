# Reservation Processor Service

Kubernetes-based replacement for the Lambda reservation processor.

## ⚠️ CRITICAL: OpenTofu Only - NEVER Use Terraform

**🚨 THIS PROJECT USES OPENTOFU (tofu) EXCLUSIVELY 🚨**

```bash
# ✅ CORRECT - Always use tofu
tofu init
tofu plan
tofu apply
tofu destroy

# ❌ WRONG - NEVER use terraform
terraform apply   # ⛔ DON'T DO THIS
terraform plan    # ⛔ DON'T DO THIS
terraform destroy # ⛔ DON'T DO THIS
```

**Why this matters:**
- 🔒 **State file incompatibility**: Terraform and OpenTofu have different state formats
- 💥 **Risk of infrastructure corruption**: Using terraform can corrupt the state
- 🔄 **Version drift**: OpenTofu and Terraform diverged at 1.6.x
- 🐛 **Unpredictable behavior**: Mixing tools will cause deployment failures

**Before running ANY command:**
1. ✅ Verify you're using `tofu`: `which tofu`
2. ✅ Check aliases: `alias | grep terraform`
3. ❌ If `terraform` is aliased to `tofu`, remove the alias - it's dangerous!

**Safety check:**
```bash
# Make sure tofu is installed
tofu version

# Make sure you're NOT accidentally using terraform
terraform version 2>&1 | grep -i "not found" && echo "✅ Safe - terraform not in PATH"
```

## Architecture

- **Container**: Python 3.11 with psycopg2, boto3, kubernetes client, and pgmq
- **Deployment**: Kubernetes Deployment (runs continuously)
- **Queue**: PGMQ (postgres message queue)
- **Database**: PostgreSQL in controlplane namespace

## Directory Structure

```
terraform-gpu-devservers/
├── shared/                        # Shared utilities (top-level)
│   ├── __init__.py
│   ├── k8s_client.py             # Kubernetes client setup
│   ├── k8s_resource_tracker.py   # GPU resource tracking
│   ├── snapshot_utils.py         # EBS snapshot management
│   ├── dns_utils.py              # Route53 DNS management
│   └── alb_utils.py              # ALB/NLB management
└── reservation-processor-service/
    ├── Dockerfile                # Container image definition
    ├── requirements.txt          # Python dependencies (all-in-one)
    └── processor/
        ├── __init__.py
        ├── main.py               # Main processing loop (PGMQ polling)
        ├── reservation_handler.py # Lambda handler logic (to be migrated)
        └── buildkit_job.py       # BuildKit job creation utilities
```

**Note:** The `shared/` directory is at the top level of `terraform-gpu-devservers/` to allow sharing across multiple services (reservation processor, API service, etc.).

## Processing Flow

1. Service polls PGMQ queue `gpu_reservations` every 5 seconds
2. Retrieves messages with 5-minute visibility timeout
3. Processes reservation requests (creates pods, manages volumes, etc.)
4. On success: deletes message from queue
5. On failure: archives message for debugging

## Migration Status

### ✅ Completed
- Basic service structure with PGMQ polling
- Docker container setup
- Kubernetes deployment configuration
- IAM permissions (IRSA) for AWS resources
- Copied lambda code to new structure:
  - `reservation_handler.py` (7915 lines of lambda logic)
  - `buildkit_job.py` (buildkit job creation)
  - All shared utilities (k8s_client, snapshot_utils, dns_utils, alb_utils, k8s_resource_tracker)

### 🚧 TODO
- [ ] Replace SQS calls with PGMQ operations in `reservation_handler.py`
- [ ] Replace DynamoDB calls with PostgreSQL queries
- [ ] Update imports in `reservation_handler.py` to use new structure
- [ ] Integrate `reservation_handler.py` logic into `main.py`
- [ ] Test message processing end-to-end
- [ ] Add health checks and monitoring
- [ ] Performance tuning and optimization

## Environment Variables

- `POSTGRES_HOST` - PostgreSQL host (default: postgres-primary.controlplane.svc.cluster.local)
- `POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `POSTGRES_USER` - Database user (default: gpudev)
- `POSTGRES_PASSWORD` - Database password (from secret)
- `POSTGRES_DB` - Database name (default: gpudev)
- `QUEUE_NAME` - PGMQ queue name (default: gpu_reservations)
- `POLL_INTERVAL_SECONDS` - Polling interval (default: 5)
- `VISIBILITY_TIMEOUT_SECONDS` - Message visibility timeout (default: 300)
- `BATCH_SIZE` - Number of messages to fetch per poll (default: 1)
- `AWS_REGION` - AWS region
- `EKS_CLUSTER_NAME` - EKS cluster name

## AWS Permissions (via IRSA)

The service has IAM permissions for:
- **STS**: GetCallerIdentity (for K8s auth)
- **EKS**: DescribeCluster
- **EC2**: Volume and snapshot management
- **ECR**: Docker image operations for buildkit

## Deployment

### Full Deployment (Recommended)

Deploy everything including Docker image build:
```bash
cd terraform-gpu-devservers
tofu apply -auto-approve
```

### Deploy Only Processor Image (After Code Changes)

If you've only changed the processor code and want to rebuild/redeploy just the image:
```bash
cd terraform-gpu-devservers
tofu apply -target=null_resource.reservation_processor_image
```

**⚠️ IMPORTANT: Always use `tofu apply` - NEVER manually build/push Docker images**

**❌ WRONG - Don't do this:**
```bash
# DON'T: Manual build and push will fail if ECR doesn't exist
docker build -t reservation-processor:latest .
docker push $ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/reservation-processor:latest
```

**✅ CORRECT - Use OpenTofu:**
```bash
# Correct: Handles everything automatically
tofu apply -target=null_resource.reservation_processor_image
```

**Why this matters:**
- ✅ ECR repository must exist before pushing (created by tofu)
- ✅ Proper build context from parent directory
- ✅ Automatic ECR authentication
- ✅ Triggers Kubernetes rollout
- ✅ Idempotent and safe

### Check Deployment Status

```bash
# Check pod status
kubectl get deployment -n gpu-controlplane reservation-processor

# View logs
kubectl logs -n gpu-controlplane -l app=reservation-processor -f

# Check rollout status
kubectl rollout status -n gpu-controlplane deployment/reservation-processor
```

## Development

### Local Testing
```bash
# Build container locally
cd reservation-processor-service
docker build -t reservation-processor:local .

# Run with local postgres
docker run --rm \
  -e POSTGRES_HOST=host.docker.internal \
  -e POSTGRES_PASSWORD=yourpassword \
  reservation-processor:local
```

### Code Organization

- **main.py**: Entry point, handles PGMQ polling and message routing
- **reservation_handler.py**: Original lambda handler logic (needs migration)
- **buildkit_job.py**: BuildKit job creation for Dockerfile builds
- **shared/**: Utilities shared with other services (K8s, AWS, DNS, etc.)

## Migration Notes

### SQS → PGMQ Mapping
- `sqs_client.receive_message()` → `pgmq.read()`
- `sqs_client.delete_message()` → `pgmq.delete()`
- Message format: SQS JSON body → PGMQ JSONB message column

### DynamoDB → PostgreSQL Mapping
- `reservations` table → `reservations` table (already exists)
- `disks` table → `disks` table (already exists)
- `availability` table → `gpu_availability` table (already exists)
- `dynamodb.Table().get_item()` → `SELECT * FROM table WHERE ...`
- `dynamodb.Table().put_item()` → `INSERT INTO table ...`
- `dynamodb.Table().update_item()` → `UPDATE table SET ...`
- `dynamodb.Table().scan()` → `SELECT * FROM table WHERE ...`

### Key Differences
1. **No Lambda context**: Remove `context` parameter usage
2. **Continuous running**: No cold starts, persistent connections
3. **Direct DB access**: No need for DynamoDB client setup
4. **PGMQ visibility timeout**: Automatic message redelivery on failure
