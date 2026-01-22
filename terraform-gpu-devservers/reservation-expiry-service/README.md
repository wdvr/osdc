# Reservation Expiry Service

**Status**: Migrated from Lambda to Kubernetes CronJob  
**Version**: 1.0  
**Last Updated**: 2026-01-21

---

## Overview

The Reservation Expiry Service is a Kubernetes CronJob that manages the lifecycle of GPU reservations by:

- **Warning users** about expiring reservations at 30, 15, and 5 minutes before expiry
- **Expiring reservations** that have exceeded their time limit
- **Cleaning up pods and resources** for expired, failed, or cancelled reservations
- **Managing snapshots** for persistent disks during pod cleanup
- **Detecting stuck reservations** in preparing, queued, or pending states
- **Tracking OOM events** in running pods

This service replaced the original Lambda function `lambda/reservation_expiry` as part of the DynamoDB → PostgreSQL migration.

---

## Architecture

### Execution Model

- **Type**: Kubernetes CronJob
- **Schedule**: Every 5 minutes (`*/5 * * * *`)
- **Concurrency**: Forbid (no overlapping runs)
- **Timeout**: 10 minutes (`activeDeadlineSeconds: 600`)
- **Namespace**: `gpu-controlplane`

### Key Components

1. **Expiry Detection**: Scans active reservations and checks if they've exceeded their time limit
2. **Warning System**: Sends multi-level warnings to users via pod exec (creates files in `/home/dev`)
3. **Pod Cleanup**: Deletes pods, services, DNS records, and ALB mappings
4. **Snapshot Management**: Creates shutdown snapshots, syncs completed snapshots, cleans up old snapshots
5. **Stuck Reservation Handling**: Detects and fails/cancels reservations stuck in transient states
6. **OOM Detection**: Monitors pods for Out-of-Memory events and records them

---

## Database Integration

The service uses PostgreSQL instead of DynamoDB:

- **Reservations**: `shared/reservation_db.py` for CRUD operations
- **Disks**: `shared/disk_db.py` for disk management
- **Connection Pooling**: `shared/db_pool.py` for efficient connections

### Key Queries

- `list_reservations_by_status(status, limit)` - Get reservations by status
- `update_reservation(reservation_id, updates)` - Update reservation fields
- `get_disk(user_id, disk_name)` - Get disk information
- `mark_disk_not_in_use(user_id, disk_name)` - Free up disk after pod deletion

---

## Environment Variables

### Required

- `POSTGRES_HOST` - PostgreSQL host (injected by Terraform)
- `POSTGRES_PORT` - PostgreSQL port (default: 5432)
- `POSTGRES_USER` - PostgreSQL username
- `POSTGRES_PASSWORD` - PostgreSQL password (from secret)
- `POSTGRES_DB` - PostgreSQL database name
- `AWS_REGION` - AWS region
- `EKS_CLUSTER_NAME` - EKS cluster name for Kubernetes client

### Optional

- `WARNING_MINUTES` - Minutes before expiry to start warnings (default: 30)
- `GRACE_PERIOD_SECONDS` - Grace period after expiry before cleanup (default: 120)
- `AVAILABILITY_UPDATER_FUNCTION_NAME` - Lambda function to trigger after cleanup (optional)

---

## IAM Permissions

The service requires the following AWS permissions via IRSA:

- **STS**: `GetCallerIdentity` (for Kubernetes client setup)
- **EKS**: `DescribeCluster` (for cluster access)
- **EC2**: Volume and snapshot management (create, delete, describe, tag)
- **Lambda**: `InvokeFunction` (for availability updater)
- **S3**: Read/write to disk contents bucket

### Kubernetes RBAC

The service has cluster-wide permissions for:

- **Pods**: get, list, watch, delete (for cleanup)
- **Services**: get, list, watch, delete (for NodePort cleanup)
- **Events**: get, list, watch (for monitoring)
- **Nodes**: get, list, watch (for status checks)

---

## Deployment

### Build and Deploy

```bash
cd terraform-gpu-devservers

# Build and push Docker image
tofu apply -target=null_resource.reservation_expiry_build

# Deploy CronJob
tofu apply -target=kubernetes_cron_job_v1.reservation_expiry

# Verify deployment
kubectl get cronjob -n gpu-controlplane reservation-expiry
kubectl get jobs -n gpu-controlplane -l app=reservation-expiry
```

### Manual Trigger (for testing)

```bash
# Create a one-off job from the CronJob
kubectl create job -n gpu-controlplane --from=cronjob/reservation-expiry test-$(date +%s)

# Watch logs
kubectl logs -n gpu-controlplane -l app=reservation-expiry --tail=50 -f
```

### Suspend/Resume

```bash
# Suspend (stop running)
kubectl patch cronjob reservation-expiry -n gpu-controlplane -p '{"spec":{"suspend":true}}'

# Resume
kubectl patch cronjob reservation-expiry -n gpu-controlplane -p '{"spec":{"suspend":false}}'
```

---

## Monitoring

### Metrics to Monitor

- **Job Success Rate**: Should be ~100%
- **Job Duration**: Should be <60 seconds (max 10 minutes)
- **Expired Reservations**: Number of reservations cleaned up per run
- **Failed Jobs**: Should be 0 or very rare

### Check Logs

```bash
# Get recent jobs
kubectl get jobs -n gpu-controlplane -l app=reservation-expiry --sort-by=.metadata.creationTimestamp

# View logs from latest job
LATEST_JOB=$(kubectl get jobs -n gpu-controlplane -l app=reservation-expiry --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
kubectl logs -n gpu-controlplane job/$LATEST_JOB

# Check for errors
kubectl logs -n gpu-controlplane -l app=reservation-expiry | grep ERROR
```

### Job History

The CronJob keeps the last 3 successful and 3 failed jobs for debugging.

---

## Troubleshooting

### Job Failing

```bash
# Describe the CronJob
kubectl describe cronjob -n gpu-controlplane reservation-expiry

# Check failed jobs
kubectl get jobs -n gpu-controlplane -l app=reservation-expiry --field-selector status.successful!=1

# Get logs from failed job
kubectl logs -n gpu-controlplane job/<failed-job-name>
```

### Job Running Too Long

- Check for slow PostgreSQL queries
- Check for stuck snapshot operations
- Review pod cleanup logic for hanging operations
- Consider increasing `activeDeadlineSeconds` if legitimate work takes >10 minutes

### Database Connection Errors

- Verify PostgreSQL is running: `kubectl get pods -n gpu-controlplane -l app=postgres`
- Check credentials secret: `kubectl get secret -n gpu-controlplane postgres-credentials`
- Test connectivity from within cluster

### No Jobs Running

- Check if CronJob is suspended: `kubectl get cronjob -n gpu-controlplane reservation-expiry -o yaml | grep suspend`
- Check schedule syntax: `kubectl describe cronjob -n gpu-controlplane reservation-expiry`
- Verify service account and RBAC: `kubectl get sa,clusterrole,clusterrolebinding -n gpu-controlplane | grep expiry`

---

## Migration Notes

### Changes from Lambda

1. **Execution Model**: Lambda invocation → Kubernetes Job (batch execution)
2. **State Management**: DynamoDB → PostgreSQL
3. **Scheduling**: CloudWatch Events → Kubernetes CronJob
4. **Connection Management**: Lambda reused global clients → CronJob uses connection pooling

### Key Code Changes

1. Replaced all `datetime.utcnow()` with `datetime.now(UTC)`
2. Replaced all DynamoDB calls with PostgreSQL queries
3. Transformed Lambda `handler()` into `main()` function
4. Added connection pool init/cleanup in main()
5. Used shared utilities from `terraform-gpu-devservers/shared/`

---

## Development

### Local Testing

```bash
# Build Docker image locally
cd terraform-gpu-devservers
docker build -f reservation-expiry-service/Dockerfile -t expiry:test .

# Run with test environment variables
docker run --rm \
  -e POSTGRES_HOST=localhost \
  -e POSTGRES_PASSWORD=test \
  -e AWS_REGION=us-east-2 \
  expiry:test
```

### Code Structure

```
reservation-expiry-service/
├── Dockerfile              # Container image definition
├── requirements.txt        # Python dependencies
├── README.md              # This file
└── expiry/
    ├── __init__.py
    └── main.py            # Main expiry logic
```

---

## Related Documentation

- **Migration Plan**: `RESERVATION_EXPIRY_MIGRATION_PLAN.md`
- **Quick Start**: `EXPIRY_MIGRATION_AI_QUICKSTART.md`
- **Timezone Standard**: `TIMEZONE_STANDARD.md`
- **SQL Security**: `SQL_SECURITY_PATTERNS.md`
- **Shared Utilities**: `shared/README.md`

---

## Support

For issues or questions:
- Check logs with kubectl commands above
- Review migration documentation
- Check database state with `psql` queries
- Examine Terraform state for configuration issues

---

**End of README**

