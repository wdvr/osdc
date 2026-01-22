# Availability Updater Service

**Status**: Migrated from Lambda to Kubernetes CronJob  
**Version**: 1.0  
**Last Updated**: 2026-01-21

---

## Overview

The Availability Updater Service is a Kubernetes CronJob that maintains real-time GPU availability metrics by:

- **Querying ASG capacity** for all GPU types across multiple Auto Scaling Groups
- **Checking Kubernetes API** for actual GPU allocation and node status
- **Calculating availability metrics** including total GPUs, available GPUs, and max reservable
- **Supporting multinode reservations** for high-end GPUs (H100, H200, A100, B200)
- **Handling CPU-only nodes** with special user slot tracking
- **Updating PostgreSQL** with current availability data every 5 minutes

This service replaced the original Lambda function `lambda/availability_updater` as part of the DynamoDB → PostgreSQL migration.

---

## Architecture

### Execution Model

- **Type**: Kubernetes CronJob
- **Schedule**: Every 5 minutes (`*/5 * * * *`)
- **Concurrency**: Allow (updates are idempotent)
- **Timeout**: 5 minutes (`activeDeadlineSeconds: 300`)
- **Namespace**: `gpu-controlplane`

### Key Components

1. **ASG Query**: Scans all Auto Scaling Groups matching pattern `pytorch-gpu-dev-gpu-nodes-{gpu_type}*`
2. **Kubernetes Integration**: Queries node status and pod GPU requests via K8s API
3. **Multinode Support**: Calculates max reservable GPUs considering 4-node configurations
4. **CPU Node Handling**: Tracks user slots on CPU-only nodes (3 users per node)
5. **Database Updates**: Uses UPSERT to maintain current availability in PostgreSQL

---

## Database Integration

The service uses PostgreSQL instead of DynamoDB:

- **GPU Types Table**: Updates `gpu_types` table with real-time availability from Kubernetes
- **Shared Utilities**: `shared/availability_db.py` for CRUD operations
- **Connection Pooling**: `shared/db_pool.py` for efficient connections

### Key Functions

- `get_supported_gpu_types()` - Get all active GPU types from database
- `update_gpu_availability(...)` - Update availability metrics in gpu_types table
- `get_gpu_availability(gpu_type)` - Query current availability for specific GPU type
- `list_gpu_availability()` - List all GPU types with availability

### Database Schema

Table: `gpu_types` (with availability columns added by migration 009)

**Static Configuration Columns:**
| Column | Type | Description |
|--------|------|-------------|
| `gpu_type` | VARCHAR(50) | GPU type identifier (PK) |
| `instance_type` | VARCHAR(100) | AWS instance type |
| `max_gpus` | INTEGER | Maximum GPUs supported |
| `cpus` | INTEGER | CPU count |
| `memory_gb` | INTEGER | Memory in GB |
| `is_active` | BOOLEAN | Whether this GPU type is active |

**Dynamic Availability Columns** (updated every 5 minutes):
| Column | Type | Description |
|--------|------|-------------|
| `total_cluster_gpus` | INTEGER | Total GPUs across all running instances (from K8s) |
| `available_gpus` | INTEGER | Schedulable GPUs (from K8s API) |
| `max_reservable` | INTEGER | Max GPUs for single reservation (multinode aware) |
| `full_nodes_available` | INTEGER | Nodes with all GPUs free |
| `running_instances` | INTEGER | InService ASG instances or K8s node count |
| `desired_capacity` | INTEGER | Total ASG desired capacity |
| `max_per_node` | INTEGER | GPUs per instance (0 for CPU nodes) |
| `last_availability_update` | TIMESTAMP WITH TIME ZONE | Last availability update timestamp |
| `last_availability_updated_by` | VARCHAR(100) | Pod/service that performed update |

---

## Environment Variables

### Required

- `POSTGRES_HOST` - PostgreSQL host (injected by Terraform)
- `POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `POSTGRES_USER` - PostgreSQL username
- `POSTGRES_PASSWORD` - PostgreSQL password (from secret)
- `POSTGRES_DB` - PostgreSQL database name
- `AWS_REGION` - AWS region (default: us-east-2)
- `EKS_CLUSTER_NAME` - EKS cluster name for Kubernetes client

### Optional

- `HOSTNAME` - Pod hostname (automatically set by Kubernetes)

---

## IAM Permissions

The service requires the following AWS permissions via IRSA:

- **STS**: `GetCallerIdentity` (for Kubernetes client setup)
- **EKS**: `DescribeCluster` (for cluster access)
- **AutoScaling**: `DescribeAutoScalingGroups` (for capacity queries)
- **EC2**: `DescribeInstances`, `DescribeAvailabilityZones` (for instance info)

### Kubernetes RBAC

The service has cluster-wide permissions for:

- **Nodes**: get, list, watch (for GPU availability checks)
- **Pods**: get, list, watch (for GPU request tracking)
- **Pod Status**: get, list, watch (for pod phase checks)

---

## Deployment

### Build and Deploy

```bash
cd terraform-gpu-devservers

# Build and push Docker image
tofu apply -target=null_resource.availability_updater_build

# Deploy CronJob
tofu apply -target=kubernetes_cron_job_v1.availability_updater

# Verify deployment
kubectl get cronjob -n gpu-controlplane availability-updater
kubectl get jobs -n gpu-controlplane -l app=availability-updater
```

### Manual Trigger (for testing)

```bash
# Create a one-off job from the CronJob
kubectl create job -n gpu-controlplane --from=cronjob/availability-updater test-$(date +%s)

# Watch logs
kubectl logs -n gpu-controlplane -l app=availability-updater --tail=100 -f
```

### Suspend/Resume

```bash
# Suspend (stop running)
kubectl patch cronjob availability-updater -n gpu-controlplane -p '{"spec":{"suspend":true}}'

# Resume
kubectl patch cronjob availability-updater -n gpu-controlplane -p '{"spec":{"suspend":false}}'
```

---

## Monitoring

### Metrics to Monitor

- **Job Success Rate**: Should be ~100%
- **Job Duration**: Should be <2 minutes (max 5 minutes)
- **GPU Types Updated**: Should match number of active GPU types
- **Failed Jobs**: Should be 0 or very rare

### Check Logs

```bash
# Get recent jobs
kubectl get jobs -n gpu-controlplane -l app=availability-updater --sort-by=.metadata.creationTimestamp

# View logs from latest job
LATEST_JOB=$(kubectl get jobs -n gpu-controlplane -l app=availability-updater --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
kubectl logs -n gpu-controlplane job/$LATEST_JOB

# Check for errors
kubectl logs -n gpu-controlplane -l app=availability-updater | grep ERROR

# Check database was updated
kubectl exec -it -n gpu-controlplane postgres-primary-0 -- \
  psql -U gpudev -d gpudev -c "SELECT gpu_type, available_gpus, total_cluster_gpus as total_gpus, last_availability_update, running_instances FROM gpu_types WHERE is_active = true ORDER BY gpu_type;"
```

### Job History

The CronJob keeps the last 3 successful and 3 failed jobs for debugging.

---

## Troubleshooting

### Job Failing

```bash
# Describe the CronJob
kubectl describe cronjob -n gpu-controlplane availability-updater

# Check failed jobs
kubectl get jobs -n gpu-controlplane -l app=availability-updater --field-selector status.successful!=1

# Get logs from failed job
kubectl logs -n gpu-controlplane job/<failed-job-name>
```

### Common Issues

#### No ASGs Found
- **Symptom**: Logs show "No ASGs found matching pattern"
- **Cause**: ASG naming doesn't match expected pattern
- **Fix**: Check ASG names in AWS console, verify they start with `pytorch-gpu-dev-gpu-nodes-{gpu_type}`

#### Kubernetes Client Errors
- **Symptom**: "Failed to setup Kubernetes client"
- **Cause**: IRSA not configured correctly or EKS permissions missing
- **Fix**: Verify service account has correct IAM role annotation

#### Database Connection Errors
- **Symptom**: "Failed to initialize connection pool"
- **Cause**: PostgreSQL not accessible or credentials incorrect
- **Fix**: 
  - Verify PostgreSQL is running: `kubectl get pods -n gpu-controlplane -l app=postgres`
  - Check credentials secret: `kubectl get secret -n gpu-controlplane postgres-credentials`
  - Test connectivity from within cluster

#### Job Running Too Long
- **Symptom**: Job exceeds 5 minute timeout
- **Cause**: Large number of nodes or slow Kubernetes API
- **Fix**: Consider increasing `activeDeadlineSeconds` or optimizing queries

### No Jobs Running

- Check if CronJob is suspended: `kubectl get cronjob -n gpu-controlplane availability-updater -o yaml | grep suspend`
- Check schedule syntax: `kubectl describe cronjob -n gpu-controlplane availability-updater`
- Verify service account and RBAC: `kubectl get sa,clusterrole,clusterrolebinding -n gpu-controlplane | grep availability`

---

## Migration Notes

### Architectural Decision

**Note**: The original migration plan proposed creating a separate `gpu_availability` table. However, the implementation adds availability columns directly to the existing `gpu_types` table. This approach:
- ✅ Reduces complexity (single table instead of two)
- ✅ Maintains data consistency (no JOIN required)
- ✅ Simplifies queries for the API service
- ✅ Groups static config with dynamic availability in one place

### Changes from Lambda

1. **Execution Model**: EventBridge trigger → Kubernetes CronJob (scheduled)
2. **State Management**: DynamoDB → PostgreSQL (availability data stored in `gpu_types` table)
3. **Scheduling**: CloudWatch Events → Kubernetes CronJob (every 5 minutes)
4. **Trigger Logic**: Event-driven (single GPU type) → Schedule-driven (all GPU types)
5. **Connection Management**: Lambda globals → CronJob connection pooling

### Key Code Changes

1. Replaced all `datetime.utcnow()` with `datetime.now(UTC)`
2. Replaced all `time.time()` with `datetime.now(UTC).timestamp()`
3. Replaced all DynamoDB calls with PostgreSQL queries via `availability_db.py`
4. Transformed Lambda `handler(event, context)` into `main()` function
5. Added connection pool init/cleanup in main()
6. Used shared utilities from `terraform-gpu-devservers/shared/`
7. Removed Lambda context dependencies (no `context.aws_request_id`)
8. Changed from event-driven to scheduled execution (scans all GPU types)

### Bug Fixes

- **Timezone Handling**: Fixed naive datetime usage (now uses `datetime.now(UTC)`)
- **Connection Pooling**: Added proper pool initialization and cleanup
- **Error Handling**: Improved error handling and logging
- **Kubernetes Client**: Added singleton pattern for K8s client reuse

---

## Development

### Local Testing

```bash
# Build Docker image locally
cd terraform-gpu-devservers
docker build -f availability-updater-service/Dockerfile -t availability-updater:test .

# Run with test environment variables (requires AWS credentials and K8s access)
docker run --rm \
  -e POSTGRES_HOST=localhost \
  -e POSTGRES_PASSWORD=test \
  -e AWS_REGION=us-east-2 \
  -e EKS_CLUSTER_NAME=pytorch-gpu-dev-cluster \
  -e AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID \
  -e AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY \
  -e AWS_SESSION_TOKEN=$AWS_SESSION_TOKEN \
  availability-updater:test
```

### Code Structure

```
availability-updater-service/
├── Dockerfile              # Container image definition
├── requirements.txt        # Python dependencies
├── README.md              # This file
└── updater/
    ├── __init__.py
    └── main.py            # Main updater logic
```

### Key Functions

- `run_availability_update()` - Main orchestration function
- `update_gpu_availability_for_type()` - Update single GPU type
- `check_schedulable_gpus_for_type()` - Query K8s for available GPUs
- `is_node_ready_and_schedulable()` - Check node status
- `get_available_gpus_on_node()` - Count free GPUs on node

---

## Algorithm Details

### GPU Availability Calculation

1. **Query ASGs**: Find all ASGs matching `pytorch-gpu-dev-gpu-nodes-{gpu_type}*`
2. **Calculate Total**: `running_instances * gpus_per_instance`
3. **Query Kubernetes**: Get actual GPU requests from all pods on GPU nodes
4. **Calculate Available**: `total_gpus - used_gpus`
5. **Find Full Nodes**: Count nodes where `available_gpus == total_gpus`
6. **Calculate Max Reservable**:
   - High-end GPUs (H100, H200, A100, B200): Up to 4 nodes * 8 GPUs = 32 GPUs
   - Other GPUs: Single node max
   - CPU nodes: 1 slot per reservation

### CPU Node Handling

CPU-only nodes (gpus_per_instance=0) use special logic:
- Each node supports 3 user slots
- Counts `gpu-dev-*` pods on each node
- Available slots = `max_users_per_node - used_slots`
- Max reservable = 1 (single CPU node per reservation)

### Multinode Support

High-end GPU types support multinode reservations:
- GPU types: `h100`, `h200`, `b200`, `a100`
- Max nodes per reservation: 4
- Max GPUs per reservation: 32 (4 nodes * 8 GPUs)
- Requires full nodes (all GPUs free)
- Falls back to single node max if no full nodes available

---

## Related Documentation

- **Migration Plan**: `AVAILABILITY_UPDATER_MIGRATION_PLAN.md`
- **Timezone Standard**: `TIMEZONE_STANDARD.md`
- **SQL Security**: `SQL_SECURITY_PATTERNS.md`
- **Shared Utilities**: `shared/README.md`
- **Database Usage**: `shared/DB_USAGE.md`

---

## Support

For issues or questions:
- Check logs with kubectl commands above
- Review migration documentation
- Check database state with psql queries
- Examine Terraform state for configuration issues

---

**End of README**

