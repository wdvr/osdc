# Helm Migration: Architecture, Status, and Gaps

## Overview

The `feat/helm-migration` branch transforms OSDC from a Lambda/SQS/DynamoDB architecture to a Kubernetes-native, Helm-deployable system. The goal is cloud-agnostic deployment on any K8s cluster (EKS, GKE, k3d, etc.).

## Architecture Change

### Before (main branch)
```
CLI → AWS SQS → Lambda (reservation_processor) → K8s API → pods
                Lambda (availability_updater)   → DynamoDB
                Lambda (reservation_expiry)     → DynamoDB + K8s cleanup
CLI polls DynamoDB directly for status
```

### After (feat/helm-migration)
```
CLI → API Service (FastAPI, K8s Deployment) → PGMQ (PostgreSQL)
      ← Reservation Processor (K8s Deployment, polls PGMQ) → K8s Jobs → pods
      ← Availability Updater (K8s CronJob, every 5min) → PostgreSQL
      ← Reservation Expiry (K8s CronJob, every 5min) → PostgreSQL
CLI talks to API over HTTP/REST
```

### Key Replacements

| AWS Service | Replaced With | Location |
|-------------|---------------|----------|
| Lambda (reservation_processor) | K8s Deployment (FastAPI poller + K8s Jobs) | `reservation-processor-service/` |
| Lambda (reservation_expiry) | K8s CronJob | `reservation-expiry-service/` |
| Lambda (availability_updater) | K8s CronJob | `availability-updater-service/` |
| SQS | PGMQ (PostgreSQL Message Queue) | PostgreSQL extension |
| DynamoDB (6 tables) | PostgreSQL (9 schema migrations) | `database/schema/` |
| Direct DynamoDB polling | REST API (FastAPI) | `api-service/` |
| ECR | Any container registry | Configurable in values |

## Helm Chart Structure

**Chart**: `charts/gpu-dev-server/` (version 0.1.0)

### Templates

| Component | Template | Purpose |
|-----------|----------|---------|
| Namespaces | `templates/namespaces.yaml` | gpu-dev-controlplane + gpu-dev-workloads |
| PostgreSQL Primary | `templates/postgres/statefulset-primary.yaml` | Main database |
| PostgreSQL Replica | `templates/postgres/statefulset-replica.yaml` | Read replica |
| PostgreSQL Config | `templates/postgres/configmap.yaml` | Primary + replica + init configs |
| PostgreSQL Secret | `templates/postgres/secret.yaml` | Credentials |
| PostgreSQL RBAC | `templates/postgres/rbac.yaml` | Service accounts |
| PostgreSQL Services | `templates/postgres/service.yaml` | Networking |
| DB Migration Job | `templates/database-migration-job.yaml` | Schema migrations |
| API Service | `templates/api-service/deployment.yaml` | FastAPI REST API |
| API Service | `templates/api-service/service.yaml` | ClusterIP + Public |
| API ConfigMap | `templates/api-service/configmap.yaml` | Configuration |
| API ServiceAccount | `templates/api-service/serviceaccount.yaml` | Identity |
| Reservation Processor | `templates/reservation-processor/deployment.yaml` | Polls PGMQ |
| Processor ConfigMap | `templates/reservation-processor/configmap.yaml` | Configuration |
| Processor RBAC | `templates/reservation-processor/rbac.yaml` | K8s API access |
| Availability Updater | `templates/availability-updater/cronjob.yaml` | Every 5min |
| Reservation Expiry | `templates/reservation-expiry/cronjob.yaml` | Every 5min |
| Registry Caches | `templates/registry/*.yaml` | GHCR, DockerHub, Native |
| Image Prepuller | `templates/image-prepuller/daemonset.yaml` | Pre-pull GPU images |
| StorageClass | `templates/storage-class.yaml` | gp3 storage |
| RBAC | `templates/rbac/gpu-dev-rbac.yaml` | Workload RBAC |
| BuildKit SA | `templates/rbac/buildkit-sa.yaml` | Build service account |

### Values Files

| File | Purpose |
|------|---------|
| `values.yaml` | Base defaults (cloud-agnostic) |
| `values-aws.yaml` | AWS-specific overrides (EBS, ALB, Route53) |
| `values-gcp.yaml` | GCP-specific overrides (stub) |
| `values-local.yaml` | k3d local dev (no GPUs, no replicas, no cron) |

### Dependencies

- `nvidia-device-plugin` chart (0.14.0, bundled as .tgz), conditionally enabled

## Database Schema

PostgreSQL with PGMQ extension. Schema at `charts/gpu-dev-server/database/schema/`:

| Migration | Purpose |
|-----------|---------|
| `001_users_and_keys.sql` | API users and key authentication |
| `002_reservations.sql` | Reservation records |
| `003_disks.sql` | Persistent disk tracking |
| `004_gpu_types.sql` | GPU type configurations |
| `005_domain_mappings.sql` | SSH domain-to-reservation mappings |
| `006_alb_target_groups.sql` | ALB target group cleanup tracking |
| `007_pgmq_queues.sql` | PGMQ queues (gpu_reservations, disk_operations) |
| `008_add_expiry_tracking.sql` | OOM/warning tracking columns |
| `009_add_availability_to_gpu_types.sql` | Real-time availability columns |

Fixture: `001_initial_gpu_types.sql` - GPU type seed data.

## Cloud Provider Abstraction

### Provider Interface

`providers/base.py` defines abstract `CloudProvider` and `AuthProvider` classes.

### Implementations

| Provider | File | Status |
|----------|------|--------|
| AWS | `providers/aws.py` (403 lines) | **Complete** - wraps boto3 |
| GCP | `providers/gcp.py` (192 lines) | **Stub** - all NotImplementedError |
| Custom | `providers/custom.py` (409 lines) | **Stub** - all NotImplementedError |

### boto3 Import Pattern

Most files use lazy imports:
```python
# Good (lazy)
def some_method(self):
    import boto3
    client = boto3.client("ec2")
```

**BUT** these files still have module-level AWS imports (will crash on non-AWS):
- `shared/alb_utils.py` - `import boto3` at line 11
- `shared/disk_reconciler.py` - `from botocore.exceptions import ClientError`
- `api-service/app/main.py` - `import aioboto3`
- `cli-tools/gpu-dev-cli/gpu_dev_cli/config.py` - `import boto3`
- `cli-tools/gpu-dev-cli/gpu_dev_cli/kubeconfig.py` - `import boto3`

## Local Development (k3d)

### Files

| File | Purpose |
|------|---------|
| `local/setup.sh` | Creates k3d cluster, builds images, deploys chart |
| `local/build-images.sh` | Builds 3 Docker images, loads into k3d |
| `local/dev-pod-image/Dockerfile` | CPU-only dev pod |
| `local/teardown.sh` | Destroys k3d cluster |
| `local/manifests/ingress.yaml` | Traefik ingress for API |
| `charts/gpu-dev-server/values-local.yaml` | Local overrides |

### What values-local.yaml Disables

- PostgreSQL replica
- Registry caches
- Availability updater CronJob
- Reservation expiry CronJob
- NVIDIA device plugin
- Image prepuller
- BuildKit

### Known Limitations (per docs/LOCAL_DEVELOPMENT.md)

- No GPUs (k3d has no GPU passthrough)
- No persistent disks (no EBS)
- No DNS/SSH proxy/domain routing
- No image builds (no ECR)
- No EFS shared storage
- Single node only

## What Works

Per `progress.md` and code analysis:

- Full CLI test suite (14/14 passed)
- Reservation create/list/show/cancel flow
- API authentication (AWS STS + local dev bypass)
- PGMQ message queuing and processing
- PostgreSQL schema migration
- Reservation processor polling
- Availability data display
- Disk operations (list, create, delete)
- Helm chart deployment via Terraform `helm_release`
- Docker images build successfully

## What's Missing / Broken

### Critical (blocks non-AWS deployment)

| Issue | Impact | Effort |
|-------|--------|--------|
| `alb_utils.py` module-level boto3 import | Crashes reservation processor on non-AWS | 2-4 hours |
| Dead `trigger_availability_update()` Lambda call in reservation_handler.py | Calls deleted Lambda function | 1 hour |
| CLI hardcoded to AWS STS auth | Cannot use CLI without AWS credentials | 1-2 weeks |

### High Priority (missing functionality)

| Issue | Impact | Effort |
|-------|--------|--------|
| No monitoring stack in chart | No Prometheus/Grafana/DCGM dashboards | 1 week |
| PostgreSQL missing headless services | StatefulSet DNS resolution broken | 2-4 hours |
| No EFA device plugin template | No high-perf networking | 1-2 days |
| GCP provider is all stubs | Cannot deploy on GKE | 2-3 weeks |

### Medium Priority (quality/maintenance)

| Issue | Impact | Effort |
|-------|--------|--------|
| Schema files duplicated in 2 locations | Maintenance burden | 1-2 hours |
| reservation_handler.py is 8,202 lines | Unmaintainable monolith | 1-2 weeks |
| No Helm chart tests | No validation | 2-3 days |
| No integration tests for PGMQ flow | Only unit tests | 1-2 weeks |
| GPU_CONFIG in 3+ places | Inconsistency risk | 1-2 days |
| Registry templates disabled in helm_release | TF still manages registries | 2-3 days |

## Effort Estimates

| Scope | Effort |
|-------|--------|
| AWS-only Helm deploy (fix critical bugs, add headless services) | **1-2 weeks** |
| + Monitoring stack | **+1 week** |
| + Cloud-agnostic CLI | **+1-2 weeks** |
| + GCP provider | **+2-3 weeks** |
| Full cloud-agnostic parity | **4-6 weeks total** |

## Terraform Integration

The chart is deployed via `helm_release` resource in `helm-gpu-dev-server.tf`. Terraform still manages:
- AWS infrastructure (VPC, EKS, IAM, ASGs)
- Docker image builds (via `null_resource`)
- Registry caches (not yet migrated to chart)
- `aws-auth` ConfigMap

## Key Files Reference

### Helm Chart
- `charts/gpu-dev-server/Chart.yaml`
- `charts/gpu-dev-server/values.yaml` / `values-aws.yaml` / `values-local.yaml` / `values-gcp.yaml`
- `charts/gpu-dev-server/templates/` (all templates)

### Python Services
- `terraform-gpu-devservers/api-service/app/main.py` (2405 lines)
- `terraform-gpu-devservers/reservation-processor-service/processor/` (poller.py, worker.py, reservation_handler.py, job_manager.py)
- `terraform-gpu-devservers/reservation-expiry-service/expiry/main.py`
- `terraform-gpu-devservers/availability-updater-service/updater/main.py`

### Shared Modules
- `terraform-gpu-devservers/shared/` (db_pool.py, reservation_db.py, disk_db.py, snapshot_utils.py, alb_utils.py, dns_utils.py, disk_reconciler.py)

### Cloud Providers
- `terraform-gpu-devservers/providers/` (base.py, aws.py, gcp.py, custom.py)

### Local Dev
- `local/setup.sh`, `local/build-images.sh`, `local/teardown.sh`

### Documentation
- `docs/HELM_MIGRATION.md`, `docs/LOCAL_DEVELOPMENT.md`
- `feature_parity.md`, `multicloud.md`, `progress.md`, `bugs.md`
