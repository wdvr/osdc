# OSDC Helm Migration: Decision One-Pager

## Context

OSDC (GPU dev server reservation system) currently runs on AWS with deep coupling to Lambda, SQS, DynamoDB, EBS, ECS, Route53, and ALB. The `feat/helm-migration` branch has significant work toward a Helm-deployable, cloud-agnostic version. The ciforge repo has mature K8s infrastructure (EKS + Karpenter + ARC) that could potentially be leveraged.

## Question 1: What's the effort to finish the helm branch?

### Current State
The `feat/helm-migration` branch (55 commits, ~35K lines added) has replaced the core architecture:
- Lambda → K8s Deployments/CronJobs
- SQS → PGMQ (PostgreSQL)
- DynamoDB → PostgreSQL
- Direct AWS SDK calls → REST API + cloud provider abstraction

CLI tests pass (14/14), and local k3d setup scripts exist.

### Remaining Work

| Milestone | Effort | What's Needed |
|-----------|--------|---------------|
| **AWS-only Helm deploy** | **1-2 weeks** | Fix module-level boto3 imports, add PG headless services, remove dead Lambda calls, test on real EKS |
| **+ Monitoring** | **+1 week** | Port Prometheus/Grafana/DCGM stack to Helm templates |
| **+ Cloud-agnostic CLI** | **+1-2 weeks** | Remove AWS STS dependency, add API-key or token auth |
| **+ GCP support** | **+2-3 weeks** | Implement GCP provider (volumes, snapshots, networking) |
| **Full cloud-agnostic** | **4-6 weeks total** | All of the above + integration tests + chart tests |

### K3d Local Testing Results

Attempted automated k3d testing. The `local/setup.sh` script creates the cluster and builds images, but **kubectl cannot reach the k3d API server when an HTTP/HTTPS proxy is active** (common in corp environments). The proxy intercepts connections to `0.0.0.0` and returns HTTP errors. Fix: add `0.0.0.0,localhost,127.0.0.1` to `NO_PROXY`. This is a documentation gap -- `local/setup.sh` should either detect proxies and warn, or set `NO_PROXY` automatically.

The Helm chart itself lints and templates cleanly. Full end-to-end k3d validation still needed in a proxy-free environment.

### Key Risks
1. **reservation_handler.py is 8,202 lines** - Monolith carried from Lambda, hard to debug
2. **No integration tests** for the new PGMQ-based flow
3. **k3d local setup** has proxy issues (see above) and hasn't been validated end-to-end
4. **CLI still requires AWS credentials** even in "cloud-agnostic" mode

## Question 2: Should we use ciforge's K8s infrastructure?

### What Ciforge Offers

Ciforge manages production EKS clusters for PyTorch CI with:
- **Karpenter** for dynamic GPU node provisioning (T4, A10G, L4, B200)
- **NVIDIA device plugin** (v0.14.5, cloud-agnostic DaemonSet)
- **Node performance tuning** (static CPU manager, NUMA, GPU persistence)
- **Harbor** pull-through cache (6 registries, S3-backed)
- **Modular deploy system** (clusters.yaml + justfile + modules)
- **GPU AMI selection** (pre-installed NVIDIA drivers, no bootstrap driver install)
- **Guaranteed QoS enforcement** (requests == limits validation)

### Comparison

| Aspect | OSDC Current | OSDC Helm Branch | Ciforge |
|--------|-------------|------------------|---------|
| Node provisioning | Fixed ASGs | Fixed ASGs (via TF) | Karpenter (dynamic) |
| GPU driver install | User-data script (~60 lines) | Same | Pre-installed AMI |
| Image caching | None | Registry templates (disabled) | Harbor (production) |
| Monitoring | GPU Operator + custom | Not ported yet | NVIDIA plugin only |
| Deploy method | terraform apply | terraform + helm_release | just deploy (modular) |
| Config management | TF variables per workspace | Helm values per env | clusters.yaml (single source) |

### Directly Reusable Components (drop-in)

1. **Node performance tuning DaemonSet** - CPU governor, NUMA, GPU persistence mode
2. **NVIDIA device plugin manifest** - Lighter than full GPU Operator
3. **StorageClass (gp3 encrypted)** - Standard gp3 with encryption
4. **GPU AMI pattern** - Use `amazon-eks-node-al2023-x86_64-nvidia-*` instead of manual driver install
5. **Guaranteed QoS validation** - Ensure GPU pod requests == limits
6. **IMDSv2 enforcement** - Security hardening

### Karpenter Migration (biggest win, biggest effort)

**Pros:**
- Dynamic scaling → no pre-provisioned idle GPU nodes
- Built-in capacity reservation support
- Automatic consolidation of empty/underutilized nodes
- Spot instance support with interruption handling
- No ASG management overhead
- Already proven for GPU workloads in ciforge

**Cons:**
- OSDC uses "pet" instances (persistent state, long-running) vs Karpenter's "cattle" model
- Consolidation policies need careful tuning to avoid disrupting active reservations
- Requires IRSA, SQS queue, EventBridge rules (more AWS infra)
- Migration complexity: ASG → Karpenter is non-trivial

**Effort: 2-3 weeks** (using ciforge's terraform module as starting point)

### Can OSDC Deploy ON ciforge's Clusters?

**Short answer: Not directly.** The workload models are fundamentally different:
- Ciforge runs ephemeral CI jobs (minutes)
- OSDC runs persistent GPU sessions (hours/days)
- Different RBAC requirements (OSDC pods need SSH, Jupyter, root access)
- Different networking (OSDC needs NodePort/ALB for SSH, ciforge uses none)
- Different storage (OSDC needs persistent EBS, ciforge uses ephemeral)

**However**, sharing the same EKS cluster with separate namespaces/NodePools IS feasible:
- Shared Karpenter controller, separate NodePools
- Shared base infra (Harbor, monitoring), separate workload namespaces
- Effort: **3-4 weeks** to co-locate on ciforge cluster
- Risk: Blast radius (OSDC issues could affect CI, and vice versa)

## Recommendation

### Path A: Finish Helm Branch Standalone (Recommended)
- **Effort**: 4-6 weeks for full cloud-agnostic, 1-2 weeks for AWS Helm deploy
- **Adopt from ciforge**: Node performance tuning, GPU AMIs, QoS validation, device plugin
- **Consider later**: Karpenter migration when ASG management becomes painful
- **Pros**: Independent, lower risk, incremental migration
- **Cons**: Doesn't leverage ciforge's mature infra

### Path B: Deploy on Ciforge Cluster
- **Effort**: 3-4 weeks (assuming Helm branch is finished)
- **Pros**: Shared infrastructure, Karpenter, Harbor, monitoring for free
- **Cons**: Coupled to ciforge, shared blast radius, need to coordinate changes, harder to offer to non-PyTorch teams

### Path C: Adopt Ciforge Patterns, Separate Cluster
- **Effort**: 6-8 weeks (Helm branch + Karpenter + ciforge patterns)
- **Pros**: Best of both worlds - proven patterns, independent deployment
- **Cons**: Most work upfront

### Bottom Line

**Start with Path A**, cherry-pick ciforge components (node tuning, GPU AMIs, QoS). This gets Helm working in 1-2 weeks for AWS. Then evaluate Karpenter adoption as a separate initiative when you have data on how ASG management scales.

## Immediate Cost Savings (Unrelated but Urgent)

The live env recon found:
- **72 orphaned EBS volumes** (~69 TB) = **~$5,653/month wasted**
- **238 EBS snapshots** (~231 TB) = potentially **~$11,828/month**
- **2 orphaned legacy EKS clusters** still running = unnecessary compute cost
- **H100 capacity reservation** (2x p5.48xlarge, expires Apr 8) not consumed = reserved but idle
- **5 legacy instances** in old ASGs not managed by terraform

**Action**: Clean up orphaned volumes/snapshots and decommission legacy clusters before they cost more than the migration.
