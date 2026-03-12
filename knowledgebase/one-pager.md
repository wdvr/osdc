# OSDC Helm Migration: Decision One-Pager

## Goal

Make OSDC **fully cloud-agnostic** -- everything contained within K8s. No AWS Lambda, SQS, DynamoDB, or any cloud-specific services in the core path. Deployable via `helm install` on any K8s cluster (EKS, GKE, k3d, bare metal).

## Current State of the Helm Branch

The `feat/helm-migration` branch (55 commits, ~35K lines) has done the heavy lifting:

| Component | Before (main) | After (helm branch) | Status |
|-----------|--------------|---------------------|--------|
| Queue | AWS SQS | PGMQ (PostgreSQL) | Done |
| State store | AWS DynamoDB (6 tables) | PostgreSQL (9 migrations) | Done |
| Reservation processor | AWS Lambda | K8s Deployment + Jobs | Done |
| Expiry handler | AWS Lambda | K8s CronJob | Done |
| Availability updater | AWS Lambda | K8s CronJob | Done |
| API layer | CLI → SQS/DynamoDB direct | CLI → FastAPI REST API | Done |
| Auth | AWS STS | AWS STS + local dev bypass | Partial |
| Persistent storage | AWS EBS + snapshots | Cloud provider abstraction | Partial |
| Container registry | AWS ECR | Configurable | Done |
| DNS / SSH proxy | AWS Route53 + ECS Fargate | Not migrated (by design) | N/A |
| Monitoring | TF-managed Prometheus/Grafana | Not in chart | Missing |

## K3d Testing Results (Validated)

Tested the full pipeline on local k3d:

| Step | Result |
|------|--------|
| `helm lint` | Pass (0 failures) |
| `helm template` (29 resources) | Pass |
| k3d cluster creation | Pass |
| Docker image builds (api, processor, dev-pod) | Pass (all cached) |
| Helm install | Pass (after node labeling -- see gotcha below) |
| PostgreSQL StatefulSet | Running (with 1 restart) |
| DB schema migration Job | Completed |
| API Service | Running, /health returns healthy |
| Reservation Processor | Running, polling PGMQ |
| Local dev login (dummy creds) | Works, returns API key |
| GPU availability API | Works, returns all GPU types from DB |
| Submit reservation | Works, queued in PGMQ |
| Processor picks up message | Works, creates K8s Job |
| Worker processes reservation | Works, correctly fails with "no nodes for GPU type" |

**Gotcha found**: `values-local.yaml` sets `nodeSelector: {}` but the YAML merge doesn't override the base `values.yaml` `nodeSelector: {NodeType: cpu}` for all components. The `setup.sh` works around this by labeling the node first. This needs fixing in the chart (conditional nodeSelector in templates).

**Gotcha found**: Proxy environments (HTTP_PROXY set) break k3d -- kubectl can't reach `0.0.0.0`. Need `NO_PROXY=0.0.0.0,localhost,127.0.0.1` or unset proxy. `setup.sh` should handle this.

**Gotcha found**: API maps `gpu_type: cpu-arm` to `a100` in the job submission -- likely a default/fallback bug in the API's GPU type resolution.

## What's Still Needed for Cloud-Agnostic

### Phase 1: Remove remaining AWS imports (3-5 days)

| Item | Issue | Fix |
|------|-------|-----|
| `shared/alb_utils.py` line 11 | `import boto3` at module level, crashes on non-AWS | Lazy import or conditional |
| `shared/disk_reconciler.py` | `from botocore.exceptions import ClientError` at module level | Lazy import |
| `api-service/app/main.py` | `import aioboto3` at module level | Lazy import, already has LOCAL_DEV_USER bypass |
| `reservation_handler.py` line 1019 | `trigger_availability_update()` calls `boto3.client("lambda")` | Remove dead code |
| `shared/__init__.py` | Imports from `alb_utils` unconditionally | Conditional import |

### Phase 2: Auth without AWS (1-2 weeks)

Current state: Login requires AWS STS credentials. Local dev bypasses this with `LOCAL_DEV_USER` env var.

**Options for cloud-agnostic auth:**

| Approach | Effort | Notes |
|----------|--------|-------|
| **OIDC (recommended)** | 1 week | Standard K8s pattern. API validates JWT tokens from any OIDC provider (Okta, Auth0, Keycloak, x2p). Helm chart takes `oidc.issuerUrl` and `oidc.clientId` as values. |
| API key only | 2-3 days | Already works (login returns API key). Just need a non-AWS login flow -- e.g., GitHub OAuth, or admin-generated keys. |
| mTLS / client certs | 1 week | Heavy but secure. Each user gets a client cert. |
| Token from external IdP | 3-5 days | API accepts any Bearer token and validates against configurable endpoint. |

### Phase 3: Storage without AWS (1-2 weeks)

| Feature | Current | Cloud-agnostic alternative |
|---------|---------|---------------------------|
| Persistent user disks | AWS EBS volumes + snapshots | K8s PVCs with any CSI driver (works on any cloud) |
| Snapshot/restore | AWS EBS snapshots | VolumeSnapshots (CSI standard, supported by most clouds) |
| Shared storage | AWS EFS | Any ReadWriteMany PV (NFS, CephFS, cloud file storage) |
| Disk contents listing | AWS S3 bucket | ConfigMap or PVC-based, or MinIO (S3-compatible in-cluster) |

The `providers/base.py` abstraction exists but only `providers/aws.py` is implemented. Need to either implement per-cloud providers OR simplify to use K8s-native storage primitives only (PVCs + VolumeSnapshots).

**Recommendation**: Use K8s-native storage only. PVCs work everywhere. VolumeSnapshots are standard. Don't implement GCP/Azure provider -- just use StorageClass + PVC.

### Phase 4: Monitoring (3-5 days)

Port `monitoring.tf` (989 lines) to Helm sub-chart or dependency:
- kube-prometheus-stack (Helm chart exists, widely used)
- DCGM Exporter (Helm chart exists from NVIDIA)
- Custom Grafana dashboards as ConfigMaps

### Phase 5: CLI cloud-agnostic (3-5 days)

| File | Issue | Fix |
|------|-------|-----|
| `cli-tools/gpu-dev-cli/gpu_dev_cli/config.py` | `import boto3` for STS identity | Use API key auth instead |
| `cli-tools/gpu-dev-cli/gpu_dev_cli/kubeconfig.py` | `import boto3` for EKS kubeconfig | Use standard kubeconfig |
| `cli-tools/gpu-dev-cli/gpu_dev_cli/auth.py` | AWS STS login flow | Support OIDC/API-key login |

### Phase 6: Polish (1 week)

- PostgreSQL headless services (needed for StatefulSet DNS)
- Fix nodeSelector merge issue in Helm templates
- Schema file deduplication (charts/ vs terraform-gpu-devservers/)
- Helm chart tests
- Integration tests for PGMQ flow
- Fix GPU type mapping bug in API
- Proxy detection in setup.sh

## Total Effort Estimate

| Milestone | Effort | Cumulative |
|-----------|--------|------------|
| Fix AWS imports (deploys on any K8s with AWS creds) | 3-5 days | 3-5 days |
| OIDC auth (no AWS needed at all) | 1 week | ~2 weeks |
| K8s-native storage (PVCs, no EBS SDK calls) | 1-2 weeks | ~3-4 weeks |
| Monitoring in chart | 3-5 days | ~4 weeks |
| CLI cloud-agnostic | 3-5 days | ~4-5 weeks |
| Polish + testing | 1 week | **~5-6 weeks** |

## Ciforge: Should We Use It?

### Verdict: Cherry-pick components, don't co-locate

**Ciforge is CI infrastructure** (ephemeral GitHub Actions runners). OSDC is **interactive GPU sessions** (persistent, SSH-accessible, hours/days). The workload models are fundamentally incompatible for sharing a cluster.

**But** ciforge has excellent components worth adopting:

| Component | Value for OSDC | Effort to adopt |
|-----------|---------------|-----------------|
| Node performance tuning DaemonSet | CPU governor + NUMA + GPU persistence = better perf | Drop-in, 1 hour |
| NVIDIA device plugin (v0.14.5) | Lighter than full GPU Operator | Drop-in, 1 hour |
| GPU AMI pattern | Pre-installed drivers, faster bootstrap | Config change, 2 hours |
| Guaranteed QoS validation | Prevents overcommit on GPU nodes | Add to CI, 1 day |
| StorageClass (gp3 encrypted) | Standard, already similar | Drop-in |
| Karpenter (future) | Dynamic GPU node scaling | 2-3 weeks, separate initiative |

### Karpenter: Worth It Later, Not Now

Karpenter would replace fixed ASGs with dynamic node provisioning. But:
- OSDC's "pet" workloads conflict with Karpenter's "cattle" model
- Need to tune consolidation to not kill active sessions
- Can be done independently of the Helm migration
- **Do it after Helm is working**, when ASG management becomes painful

## Recommendation

1. **Finish Helm migration (5-6 weeks)** using K8s-native primitives (PVCs, OIDC, PGMQ)
2. **Cherry-pick ciforge components** (node tuning, device plugin, GPU AMIs) -- free performance wins
3. **Evaluate Karpenter separately** once Helm is stable
4. **Don't co-locate on ciforge cluster** -- different workload models, shared blast radius

## Urgent: Cost Cleanup

| Issue | Monthly Cost |
|-------|-------------|
| 72 orphaned EBS volumes (~69 TB) | ~$5,653 |
| 238 EBS snapshots (~231 TB) | ~$11,828 |
| 2 orphaned legacy EKS clusters | ~$144 (control plane) + compute |
| 5 legacy EC2 instances (not TF-managed) | varies |
| H100 capacity reservation (expires Apr 8, unused) | reserved cost |

**Clean these up independently of the migration.**
