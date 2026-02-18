# Agent notes

the first part of this doc is the devs description of the repo. Everything under the 'AGENT SECTION' is for you, the agent, to update state, tricky things, what we're working on and more.
This will help both you, the agent, but also other agents down the road that share the responsibility of this repo management to navigate the repo.

## Agent restrictions

- NEVER run `tofu apply` or any destructive OpenTofu commands
- You can run read-only OpenTofu commands like `tofu plan`, `tofu state show`, etc.
- You can run AWS CLI commands for read-only resource fetching and analysis
- User will handle all infrastructure deployments themselves
- Note: We use OpenTofu (not Terraform), so user runs `tofu apply` locally (tf is aliased to tofu)
- we use k for kubectl and have kubens configured to namespace gpu-dev

## Development style

We like compact code, comments when needed, but only if they add value. For example, a variable called 'number_of_threads' does not need a comment that is contains number of threads.
We like tested code.

For frontend code we use yarn, yarn format, yarn tsc. yarn dev to run code, but leave it up to the dev to run that one.
For infrastructure, we use OpenTofu (`tofu`), never run `tofu apply` directly - the user handles deployments. You can run read-only commands like `tofu plan`.

**Python Code Style:**

- Always put imports at the top of the file, never inside functions or methods
- Group imports in standard order: standard library, third-party, local imports
- Use absolute imports when possible

## Content

- **charts/gpu-dev-server/** - Helm chart for all K8s resources (cloud-agnostic)
  - **templates/** - K8s manifests (postgres, api-service, reservation-processor, cronjobs, rbac, registry, etc.)
  - **values.yaml** - Default values (AWS); **values-local.yaml** - Local k3d overrides
- **terraform-gpu-devservers/** - AWS infrastructure (EKS, IAM, VPC, ALB) + deploys Helm chart via `helm_release`
  - **api-service/** - FastAPI REST API with AWS IAM authentication
  - **reservation-processor-service/** - K8s job processor that polls PGMQ and manages GPU pod lifecycle
  - **availability-updater-service/** - CronJob that tracks GPU availability
  - **reservation-expiry-service/** - CronJob that handles reservation expiry and warnings
  - **shared/** - Shared Python utilities (db_pool, k8s_client, snapshot_utils, etc.)
  - **database/** - Database schema and initialization scripts
  - **migrations/** - Database migration scripts
  - **templates/** - Node bootstrap user-data scripts
- **cli-tools/gpu-dev-cli/** - Python CLI for creating/listing/cancelling GPU reservations
- **local/** - Local k3d development environment setup scripts
- **docs/** - Architecture docs (HELM_MIGRATION, LOCAL_DEVELOPMENT, REMOTE_DEPLOYMENT, LIMITATIONS)

# AGENT SECTION

**Profiling Node Labeling (manual, one-time setup after `tf apply`):**
```bash
# List H100 nodes and pick ONE for profiling
kubectl get nodes -l gpu-type=h100

# Label one H100 node as profiling-dedicated (DCGM will NOT run on this node)
kubectl label node <h100-node-name> gpu.monitoring/profiling-dedicated=true

# List B200 nodes and pick ONE for profiling
kubectl get nodes -l gpu-type=b200

# Label one B200 node as profiling-dedicated
kubectl label node <b200-node-name> gpu.monitoring/profiling-dedicated=true

# Verify labels
kubectl get nodes -l gpu.monitoring/profiling-dedicated=true
```

**Grafana Access:**
```bash
# Get any node IP
kubectl get nodes -o wide

# Access Grafana at: http://<node-ip>:30080
# Default credentials: admin / (value of grafana_admin_password variable)
```

**Available Dashboards:**
- NVIDIA DCGM Exporter Dashboard (pre-configured from Grafana community)
- GPU Overview (custom dashboard with utilization, memory, temp, power)

**Troubleshooting:**
```bash
# Check DCGM pods are running (should NOT be on profiling nodes)
kubectl get pods -n gpu-operator -l app=nvidia-dcgm-exporter -o wide

# Verify Prometheus is scraping DCGM
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
# Then open http://localhost:9090 and query: DCGM_FI_DEV_GPU_UTIL

# Check Grafana pods
kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana
```

## Node Management (Jan 2026)

**Architecture:**
- Nodes created via OpenTofu-managed Auto Scaling Groups (ASGs) with Launch Templates
- GPU ASGs: Fixed size (min = max = desired from config), one per GPU type
- CPU ASG: min=1, max=4, desired=2 for management workloads
- No dynamic autoscaling - ASG maintains fixed count, replaces unhealthy nodes

**User-data Scripts (terraform-gpu-devservers/templates/):**
- `al2023-user-data.sh` - Amazon Linux 2023 GPU nodes
- `al2023-cpu-user-data.sh` - Amazon Linux 2023 CPU nodes
- `user-data-self-managed.sh` - Ubuntu 22.04 nodes
- `user-data.sh` - Amazon Linux 2 nodes

**Registry Configuration in User-data:**
All templates configure containerd and Docker to trust the internal HTTP registry:
```bash
# containerd: /etc/containerd/certs.d/registry-ghcr.gpu-controlplane.svc.cluster.local:5000/hosts.toml
# Docker: /etc/docker/daemon.json with insecure-registries
```

**Node Replacement Commands:**
```bash
# Cordon all nodes
for node in $(kubectl get nodes -o name); do kubectl cordon $node; done

# Drain all nodes (bypass PDB)
for node in $(kubectl get nodes -o name); do
  kubectl drain $node --ignore-daemonsets --delete-emptydir-data --force --disable-eviction
done

# Force delete pods if needed
kubectl delete pods --all -n gpu-controlplane --force --grace-period=0
kubectl delete pods -n kube-system -l app=ebs-csi-controller --force --grace-period=0

# Trigger instance refresh
aws autoscaling start-instance-refresh --region us-west-1 \
  --auto-scaling-group-name pytorch-gpu-dev-cpu-nodes \
  --preferences '{"MinHealthyPercentage": 0, "InstanceWarmup": 300}'

# Monitor
kubectl get nodes -w
```

## Control Plane Infrastructure (Jan 2026)

**Namespace:** `gpu-controlplane`

**Components:**
1. **PostgreSQL Primary-Replica**
   - Image: `ghcr.io/pgmq/pg18-pgmq:v1.8.1` (via registry cache)
   - PGMQ extension enabled for message queuing
   - Services: `postgres-primary:5432` (read-write), `postgres-replica:5432` (read-only)
   - Storage: 100Gi gp3 PVC per instance
   - Credentials in `postgres-credentials` secret

2. **Registry Pull-Through Cache** (for ghcr.io)
   - Image: `registry:2` (from Docker Hub)
   - Service: `registry-ghcr:5000`
   - Proxies requests to ghcr.io with authentication
   - Credentials in `registry-ghcr-credentials` secret (GHCR_USERNAME, GHCR_TOKEN)
   - ConfigMap: `registry-ghcr-config` (config template)
   - Storage: 50Gi gp3 PVC

**OpenTofu Variables for ghcr.io auth:**
```hcl
# In tfvars (gitignored)
ghcr_username = "your-github-username"
ghcr_token    = "ghp_xxxxxxxxxxxx"  # PAT with read:packages scope
```

**Useful Commands:**
```bash
# Check control plane pods
kubectl get pods -n gpu-controlplane

# Connect to PostgreSQL
kubectl exec -it postgres-primary-0 -n gpu-controlplane -- psql -U gpudev -d gpudev

# Check registry logs
kubectl logs -n gpu-controlplane -l app=registry-cache

# Test PGMQ
kubectl exec -it postgres-primary-0 -n gpu-controlplane -- psql -U gpudev -d gpudev -c "SELECT pgmq.create('test_queue');"
```

## Recent Fixes (Oct 2025)

**Implemented fixes that are now part of the codebase:**

1. **NVIDIA Profiling Bootstrap** - Modprobe config (`NVreg_RestrictProfilingToAdminUsers=0`) now set before driver install at `templates/al2023-user-data.sh:19`

2. **NVIDIA Pod Profiling** - Pods use `CAP_SYS_ADMIN` capability and `NVIDIA_DRIVER_CAPABILITIES=compute,utility` for ncu/nsys support

3. **No Persistent Disk Flag** - `no_persistent_disk` flag flows from CLI → API → Job Processor to skip all disk logic when user opts out

4. **GPU Resource Allocation** - GPU counts explicitly converted to integers in Job Processor Pod

**Current State:**
- API Service: ✅ Deployed and functional
- PostgreSQL + PGMQ: ✅ Operational with all tables
- CLI: ✅ Uses API exclusively
- Job Processing: ✅ Job Processor Pod operational

## Remaining Tasks

### Helm Migration (In Progress)
- **Phase A: Complete AWS deployment** - Build GPU base image, validate end-to-end reservations
  - Run: `tofu taint null_resource.docker_build_and_push && tofu apply -target=null_resource.docker_build_and_push`
  - Test: `gpu-dev reserve --gpu-type t4 --gpus 1 --hours 1`
- **Phase C migration steps** - Run `tofu state rm` for CloudFront + old LB service, then `tofu apply`
- **Phase D validation** - Test full local dev flow: `./local/teardown.sh && ./local/setup.sh`
- **Phase G: Move SSH proxy to K8s** - Currently on ECS Fargate, should be a Helm-managed Deployment
- **Phase G: Abstract storage** - Move direct EC2/S3 calls to provider abstraction layer

### High Priority - Bug Fixes
- **Fix extend command warning cleanup** - `--extend` doesn't remove WARN_EXPIRES_IN_5MIN.txt or reset expiry tracking

### High Priority - Usability
- **Improve debugging and observability** - Better CLI feedback for pod status, logs, errors
- **Interactive CLI for cancel/edit** - Arrow-key selection when no reservation ID specified
- **Default reservation edit/cancel** - Auto-select if user has only one active

### Medium Priority - Features
- **Jupyter notebook integration** - `--jupyter` flag for Jupyter Lab + TensorBoard
- **Add user collaboration** - `--add-user <github_name>` to add SSH access for others
- **Add Docker CI image run** - `gpu-dev ci-debug <testurl>` for CI debugging

### Medium Priority - Performance/Capacity
- **Scale up nodes** - More T4, L4, H100/H200/B200 capacity
- **Increase /dev/shm** - Bump from 64MB for NCCL requirements

### Lower Priority
- **Add tests** - Comprehensive test suite for all components
- **CloudWatch logs for pods** - Pod log persistence
- **Set HF_HOME** - Prevent filling home directories
- **Simplify code** - Refactor and clean up

### Notes
- **Max reservation time**: 48 hours (initial 24h + one 24h extension)
- **Docker-in-Docker**: Now available via `--docker` flag (Phase E complete)

## System Architecture

**Infrastructure:**

- **Prod (us-east-2)**: 2x p4d.24xlarge instances (8 A100 GPUs each = 16 total GPUs)
- **Test (us-west-1)**: 2x g4dn.12xlarge instances (4 T4 GPUs each = 8 total GPUs)
- **Local**: k3d cluster (CPU-only, no GPUs)
- EKS cluster with GPU-optimized node groups
- NVIDIA device plugin for GPU resource exposure

**Deployment Model:**

- **Helm chart** (`charts/gpu-dev-server/`) manages all K8s resources (cloud-agnostic)
- **OpenTofu** manages AWS infra (EKS, IAM, ALB, Route53) and deploys Helm chart via `helm_release`
- `CLOUD_PROVIDER` env var gates AWS-specific Python code

**Reservation System:**

- **API Service**: REST API with AWS IAM auth, HTTPS via ALB at `api.<domain>`
- **PostgreSQL + PGMQ**: Database + message queue for job processing
- **Job Processor Pod**: Polls PGMQ, manages pod lifecycle
- **GPU Dev Pods**: K8s pods with GPU allocation (1/2/4/8/16 GPUs)
- **SSH Access**: NodePort services for direct pod access
- **Docker-in-Docker**: Optional DinD sidecar via `--docker` flag

**Control Plane (gpu-controlplane namespace, Helm-managed):**

- PostgreSQL primary-replica with PGMQ extension
- API Service (FastAPI) with NodePort → ALB HTTPS
- Reservation Processor Pod
- Registry pull-through caches (ghcr.io, Docker Hub, native)
- CronJobs (availability updater, reservation expiry)

**CLI Tool:**

- Python CLI with config at `~/.config/gpu-dev/config.json`
- Commands: `reserve`, `list`, `cancel`, `extend`, `config`, `connect`, `status`, `avail`, `login`
- `--docker` flag for Docker-in-Docker support
- Authentication: AWS credentials → API key (automatic refresh)
