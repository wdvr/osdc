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

- **terraform-gpu-devservers/** - Main infrastructure: EKS cluster, PostgreSQL/PGMQ, API service, job processor, and all Kubernetes resources
  - **api-service/** - FastAPI REST API with AWS IAM authentication
  - **reservation-processor-service/** - K8s job processor that polls PGMQ and manages GPU pod lifecycle
  - **availability-updater-service/** - CronJob that tracks GPU availability
  - **reservation-expiry-service/** - CronJob that handles reservation expiry and warnings
  - **shared/** - Shared Python utilities (db_pool, k8s_client, snapshot_utils, etc.)
  - **database/** - Database schema and initialization scripts
  - **migrations/** - Database migration scripts
  - **templates/** - Node bootstrap user-data scripts
- **cli-tools/gpu-dev-cli/** - Python CLI for creating/listing/cancelling GPU reservations

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

### High Priority - Bug Fixes
- **Fix extend command warning cleanup** - When using `--extend`, the system doesn't remove the WARN_EXPIRES_IN_5MIN.txt file and doesn't reset the expiry warning tracking. Need to clear warning state or track history elsewhere.

### High Priority - Usability
- **FQDN for devservers** - Set up proper domain names for development server access
- **Improve debugging and observability** - Add better CLI feedback for pod status, container logs, and error details:
  - Real-time pod startup logs during `gpu-dev reserve`
  - Container error messages when pods fail
  - Image pull status and errors
  - More detailed error messages with troubleshooting hints
- **Interactive CLI for cancel/edit** - Make `gpu-dev cancel` and `gpu-dev edit` interactive when no reservation ID specified - show list with arrow selection
- **Default reservation edit/cancel** - Auto-select reservation if user only has one active

### Medium Priority - Features
- **Custom Docker image scaffold** - Create Dockerfile with pre-installed packages (Jupyter, etc.)
- **Jupyter notebook integration** - Add `--jupyter` flag to enable Jupyter notebook and TensorBoard access
- **Add user collaboration feature** - Add `--add-user <github_name>` flag to allow users to add someone to the server
- **Add Docker CI image run** - Allow `gpu-dev ci-debug <testurl>` to download and run CI docker images
- **Add Docker-in-Docker** - Add `--docker` flag at reserve time, use dind if feasible

### Medium Priority - Performance/Capacity
- **Increase /dev/shm for NCCL** - Bump /dev/shm space from 64MB for NCCL requirements
- **Scale up T4 instances** - Add 3 more T4 nodes (g4dn.12xlarge) to cluster
- **Scale up L4 instances** - Add 3 more L4 nodes (g6.12xlarge) to cluster
- **Add on-demand H100/H200/B200 capacity** - Add at least 2 nodes each of H100, H200, and B200 as on-demand capacity

### Lower Priority - Validation & Testing
- **Validate CUDA version** - Add CUDA version validation and display in container startup
- **Validate NVIDIA driver version** - Display and validate NVIDIA driver version
- **Test wall messages** - Verify that wall message functionality works correctly
- **Validate if expiration works as expected** - Test and verify pod cleanup and reservation expiry process
- **Add tests for everything** - Implement comprehensive test suite for all components
- **Add CloudWatch logs for pods** - Store pod logs in CloudWatch for better debugging and monitoring

### Lower Priority - Enhancements
- **Set HuggingFace cache location** - Set HF_HOME to /tmp or /workspace to prevent filling home directories
- **Add verbose CLI output** - More detailed status and progress information for debugging
- **Add nvcuvid.so support** - Enable NCU (NVIDIA Nsight Compute) support with nvcuvid.so library
- **Add ghstack** - Install ghstack tool for GitHub stack management
- **Simplify code + clean up** - Refactor and clean up codebase for maintainability

### Future Features
- Multi-server (16 GPU) reservations
- GitHub organization/team verification
- Usage monitoring and quotas
- Multi-node communication for distributed training

### Notes
- **Max reservation time**: 48 hours (initial 24h + one 24h extension allowed)

## System Architecture

**Infrastructure (us-east-2):**

- **Current**: 2x p4d.24xlarge instances (8 A100 GPUs each = 16 total GPUs)
- **Test**: 2x g4dn.12xlarge instances (4 T4 GPUs each = 8 total GPUs)
- **Future**: 2x p5.48xlarge instances (8 H100 GPUs each = 16 total GPUs) when capacity available
- EKS cluster with GPU-optimized node groups
- NVIDIA device plugin for GPU resource exposure
- Single AZ deployment with cluster placement groups

**Reservation System:**

- **API Service**: Public REST API with AWS IAM authentication and CloudFront HTTPS
- **PostgreSQL + PGMQ**: Database for all state and message queue for job processing
- **Job Processor Pod**: Continuously polls PGMQ and manages pod lifecycle
- **GPU Dev Pods**: K8s pods with GPU allocation (1/2/4/8/16 GPUs)
- **SSH Access**: NodePort services for direct pod access

**Control Plane Infrastructure (gpu-controlplane namespace):**

- PostgreSQL primary-replica with PGMQ extension
- API Service (FastAPI) with CloudFront HTTPS and LoadBalancer
- Job Processor Pod for reservation management
- Registry pull-through cache for ghcr.io images
- SSH Proxy service

**Authentication & Access:**

- **API Authentication**: AWS IAM STS → time-limited API keys (2 hours)
- **SSH Authentication**: GitHub public key fetching and injection
- **SSH Access**: Copy-pasteable commands with NodePort

**CLI Tool:**

- Python CLI with config at `~/.config/gpu-dev/config.json`
- Commands: `reserve`, `list`, `cancel`, `extend`, `config`, `connect`, `status`, `avail`, `login`
- Authentication: AWS credentials → API key (automatic refresh)
- Real-time polling until reservation is ready
