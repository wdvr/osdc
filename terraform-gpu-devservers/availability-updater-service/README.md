# Cluster State Reconciliation Service

**Status**: Kubernetes CronJob (expanded from availability-updater)  
**Version**: 2.0  
**Last Updated**: 2026-01-26

---

## Overview

The Cluster State Reconciliation Service is a Kubernetes CronJob that maintains consistency between AWS resources and the PostgreSQL database by:

### GPU Availability Tracking
- **Querying ASG capacity** for all GPU types across multiple Auto Scaling Groups
- **Checking Kubernetes API** for actual GPU allocation and node status
- **Calculating availability metrics** including total GPUs, available GPUs, and max reservable
- **Supporting multinode reservations** for high-end GPUs (H100, H200, A100, B200)
- **Handling CPU-only nodes** with special user slot tracking

### Disk State Reconciliation (NEW)
- **Syncing EBS volumes** from AWS to database
- **Reconciling disk metadata** (size, in-use status, snapshot counts)
- **Importing orphaned volumes** that exist in AWS but not in database
- **Handling deleted volumes** by marking them as unavailable
- **Ensuring single source of truth** with AWS as the authoritative source

Runs every 5 minutes to keep database state synchronized with AWS reality.

---

## Architecture

### Execution Model

- **Type**: Kubernetes CronJob
- **Schedule**: Every 5 minutes (`*/5 * * * *`)
- **Concurrency**: Forbid (prevents race conditions during disk reconciliation)
- **Timeout**: 10 minutes (`activeDeadlineSeconds: 600`)
- **Namespace**: `gpu-controlplane`
- **Execution Time**: ~3-5 minutes (1 min GPU + 2-4 min disk reconciliation)

### Key Components

**Phase 1: GPU Availability Update** (~30-60 seconds)
1. **ASG Query**: Scans all Auto Scaling Groups matching pattern `pytorch-gpu-dev-gpu-nodes-{gpu_type}*`
2. **Kubernetes Integration**: Queries node status and pod GPU requests via K8s API
3. **Multinode Support**: Calculates max reservable GPUs considering 4-node configurations
4. **CPU Node Handling**: Tracks user slots on CPU-only nodes (3 users per node)
5. **Database Updates**: Uses UPSERT to maintain current availability in `gpu_types` table

**Phase 2: Disk State Reconciliation** (~2-4 minutes)
1. **Volume Discovery**: Queries all EBS volumes with `gpu-dev-user` tag
2. **Snapshot Analysis**: Counts snapshots and detects in-progress backups
3. **State Comparison**: Compares AWS state with database records
4. **Drift Correction**: Updates database to match AWS reality
5. **Orphan Handling**: Imports untracked volumes and handles deleted volumes

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

- `POSTGRES_HOST` - PostgreSQL host (injected by OpenTofu)
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
- **EC2 EBS**: `DescribeVolumes`, `DescribeSnapshots`, `DescribeVolumesModifications` (for disk reconciliation)

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
- **Job Duration**: Should be 3-5 minutes (max 10 minutes)
- **GPU Types Updated**: Should match number of active GPU types
- **Disks Reconciled**: Should match number of EBS volumes with gpu-dev-user tag
- **Reconciliation Errors**: Should be 0
- **Drift Events**: Track frequency of database updates (indicates drift)
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
- **Symptom**: Job exceeds 10 minute timeout
- **Cause**: Large number of nodes, volumes, or slow AWS API
- **Fix**: Consider increasing schedule interval or optimizing queries

#### Disk Reconciliation Errors
- **Symptom**: "Error reconciling volume" or "Error importing volume"
- **Cause**: Missing tags, invalid data, or database constraints
- **Fix**: Check volume tags in AWS console, verify disk_name and gpu-dev-user are set

#### Orphaned Volumes
- **Symptom**: High "created" count in reconciliation stats
- **Cause**: Volumes created outside the system or database records lost
- **Fix**: Review imported volumes, verify they should be tracked

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

## Disk Reconciliation Logic

### Reconciliation Rules

The disk reconciliation phase ensures database state matches AWS EBS reality. It handles three scenarios:

#### 1. Volume in AWS but not in Database
**Rule**: Create database entry  
**Action**: Import volume with `is_deleted=False`, `operation_id=NULL`, `last_used=NULL`

```python
# Fields set during import:
- disk_name: from volume tag
- user_id: from gpu-dev-user tag
- ebs_volume_id: volume ID
- size_gb: from volume
- in_use: from attachment state
- is_deleted: False
- snapshot_count: counted from AWS
- is_backing_up: from pending snapshots
```

**Why**: Handles volumes created manually or database records lost due to system issues.

#### 2. Volume in Database but Deleted from AWS

**Rule**: Depends on `is_deleted` flag in database

**Case A: `is_deleted = False`** (active record)
- **Action**: Update `in_use=False`, `reservation_id=NULL`, keep other fields
- **Rationale**: Volume was manually deleted in AWS, preserve database record for audit trail
- **Impact**: User can see disk existed but is no longer available

**Case B: `is_deleted = True`** (already marked deleted)
- **Action**: No changes needed
- **Rationale**: Expected state - disk deletion is propagating normally

**Why**: Prevents accidental data loss and maintains audit history.

#### 3. Volume in Both AWS and Database
**Rule**: Sync state from AWS to database  
**Action**: Update all reconcilable fields

**Reconciled fields**:
- `ebs_volume_id`: Volume ID (in case missing)
- `size_gb`: Volume size
- `in_use`: Attachment state
- `reservation_id`: Cleared if not attached
- `snapshot_count`: Counted from snapshots
- `is_backing_up`: From pending snapshots
- `last_snapshot_at`: Latest snapshot timestamp

**Non-reconciled fields** (application-managed):
- `is_deleted`: Soft delete flag
- `operation_id`, `operation_status`, `operation_error`: Operation tracking
- `last_used`: Not tracked by AWS
- `latest_snapshot_content_s3`: S3 path, not in EBS metadata

### Reconciliation Statistics

Each run logs:
```
aws_volumes: Total volumes in AWS with gpu-dev-user tag
db_records: Total disk records in database
synced: Records that matched exactly (no updates)
updated: Records that needed state updates
created: New records imported from AWS
errors: Reconciliation failures
orphaned_db_active: Active DB records with no AWS volume
orphaned_db_deleted: Deleted DB records with no AWS volume
```

### Edge Cases

**Multiple Volumes for Same (user_id, disk_name)**:
- Reconciliation links to first volume found
- Manual intervention required to resolve duplicates

**Volume Missing Required Tags**:
- Skipped with warning log
- Volume must have both `disk-name`/`disk_name` and `gpu-dev-user` tags

**Snapshot Query Failures**:
- Snapshot count/status fields not updated
- Volume state still reconciled
- Error logged but reconciliation continues

**Database Constraint Violations**:
- Transaction rolled back
- Error logged
- Reconciliation continues with next volume

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
- Examine OpenTofu state for configuration issues

---

**End of README**

