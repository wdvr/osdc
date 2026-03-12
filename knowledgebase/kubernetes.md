# Kubernetes

## EKS Cluster

- **Name**: `pytorch-gpu-dev-cluster`
- **Version**: 1.33 (EKS-optimized AL2023 AMI)
- **Terraform**: `/terraform-gpu-devservers/eks.tf`
- **Providers**: `hashicorp/kubernetes` ~> 2.23, `hashicorp/helm` ~> 2.12
- **Authentication**: `aws eks get-token` for K8s/Helm providers

## Namespaces

| Namespace | Purpose |
|-----------|---------|
| `gpu-dev` | User pods and services |
| `monitoring` | Prometheus, Grafana |
| `management` | Git cache service |
| `kube-system` | Core K8s components |
| `gpu-operator` | NVIDIA GPU Operator |

## Node Groups

### GPU Nodes (Self-Managed ASGs)

Each GPU type + capacity reservation combination creates a separate ASG with a launch template.

**ASG naming**: `pytorch-gpu-dev-gpu-nodes-{gpu_type}-{key}`

| GPU Type | Instance Type | GPUs/Node | CPUs | Memory | EFA Interfaces |
|----------|--------------|-----------|------|--------|----------------|
| t4 | g4dn.12xlarge | 4 | 48 | 192 GB | 0 |
| l4 | g6.12xlarge | 4 | 48 | 192 GB | 1 |
| a10g | g5.12xlarge | 4 | 48 | 192 GB | 1 |
| t4-small | g4dn.2xlarge | 1 | 8 | 32 GB | 0 |
| a100 | p4d.24xlarge | 8 | 96 | 1152 GB | 4 |
| h100 | p5.48xlarge | 8 | 192 | 2048 GB | 32 |
| h200 | p5e.48xlarge | 8 | 192 | 2048 GB | 32 |
| b200 | p6-b200.48xlarge | 8 | 192 | 2048 GB | 32 |

**Launch Template Config**:
- Root volume: 4 TB gp3
- EFA network interfaces: Single or multi-card (up to 32 for h100/h200/b200)
- Capacity reservation specification when applicable
- Placement group for GPU types with `use_placement_group=true`
- `wait_for_capacity_timeout = "0"` to prevent TF failures
- AMI: EKS-optimized AL2023 (x86_64) for GPU, Deep Learning Base for multi-EFA types

### CPU Nodes (Self-Managed ASG)

- **Instance**: c5.4xlarge
- **Purpose**: Management workloads (git cache, monitoring)
- **Launch template**: Separate from GPU nodes
- **User data**: `/terraform-gpu-devservers/templates/al2023-cpu-user-data.sh`
- **Label**: `NodeType=cpu`

## Node Labels

Applied via nodeadm config in user-data:

| Label | Values | Purpose |
|-------|--------|---------|
| `NodeType` | `gpu`, `cpu` | Node type selector |
| `GpuType` | `t4`, `l4`, `a10g`, `a100`, `h100`, `h200`, `b200` | GPU type for pod scheduling |
| `nvidia.com/gpu.deploy.driver` | `false` | Prevents GPU Operator from installing drivers (host-installed) |
| `gpu.monitoring/profiling-dedicated` | `true` | Excludes node from DCGM (for Nsight profiling) |

## aws-auth ConfigMap

Maps IAM roles to K8s users:
- **Node role** -> `system:node:{{EC2PrivateDNSName}}` (groups: `system:bootstrappers`, `system:nodes`)
- **Lambda reservation processor role** -> user `reservation-processor`
- **Lambda expiry role** -> user `reservation-expiry`
- **Lambda availability updater role** -> user `availability-updater`

Defined in `/terraform-gpu-devservers/kubernetes.tf`.

## RBAC

- **ServiceAccount**: `gpu-dev-sa` in namespace `gpu-dev`
- **Role**: Full access to pods, services, configmaps, PVCs, PVs, events in `gpu-dev` namespace
- **RoleBinding**: Binds role to ServiceAccount + all 3 Lambda users

## GPU Operator (Helm)

- **Chart**: `nvidia/gpu-operator` v25.3.3
- **Namespace**: `gpu-operator`
- **Config** (`kubernetes.tf`):
  - Driver: **disabled** (installed on host via user-data)
  - Toolkit: **enabled** (CDI mode)
  - Device Plugin: **enabled**
  - DCGM: **enabled** (with profiling node anti-affinity)
  - DCGM Exporter: **enabled** (with profiling node anti-affinity)
  - GFD (GPU Feature Discovery): **enabled**
  - MIG Manager: **enabled**
  - Node Status Exporter: **disabled**

## EFA Device Plugin

- **DaemonSet**: `aws-efa-k8s-device-plugin` in `kube-system`
- **Image**: `602401143452.dkr.ecr.{region}.amazonaws.com/eks/aws-efa-k8s-device-plugin:v0.3.3`
- **Host network**: true
- **Tolerations**: All taints
- **Resource**: Exposes `vpc.amazonaws.com/efa` as allocatable resource

## Image Prepuller DaemonSet

- **Purpose**: Pre-pull the GPU dev container image on all GPU nodes
- **Node selector**: `NodeType=gpu`
- **Image**: Same as `GPU_DEV_CONTAINER_IMAGE` env var
- **Command**: `sleep infinity` (just keeps image cached)
- **Resources**: 10m CPU, 32Mi memory

## Profiling Node Labeler CronJob

- **Schedule**: Every 5 minutes
- **Purpose**: Labels one node per GPU type for Nsight profiling
- **Label applied**: `gpu.monitoring/profiling-dedicated=true`, `nvidia.com/gpu.deploy.dcgm-exporter=false`
- **Logic**: For each GPU type, finds the first unlabeled ready node, labels it

## Pod Specification (Created by Lambda)

Each reservation creates:

### Pod (`gpu-dev-{reservation_id[:8]}`)

- **Namespace**: `gpu-dev`
- **Node selector**: `GpuType={gpu_type}`
- **Init container**: Fetches GitHub SSH public keys via `https://github.com/{username}.keys`
- **Main container**:
  - Image: ECR GPU dev image
  - Resources:
    - GPU: `nvidia.com/gpu: {count}`
    - CPU/Memory: Proportional to GPU count (e.g., for h100 with 4 GPUs: 96 CPUs, 1024Gi memory)
    - EFA: `vpc.amazonaws.com/efa: {efa_count}` (only when using all GPUs and EFA available)
    - Hugepages: `hugepages-2Mi: 5120Mi` (when EFA enabled)
  - Capabilities: `SYS_ADMIN` (for GPU profiling)
  - Environment: `NVIDIA_DRIVER_CAPABILITIES=compute,utility`, NCCL env vars, CPU threading vars
  - Volume mounts: `/workspace` (persistent disk), `/shared/ccache` (shared ccache EFS), `/shared/personal` (per-user EFS)
  - `/dev/shm`: EmptyDir with `sizeLimit: 64Gi` (for NCCL)
- **Tolerations**: GPU-specific taints
- **Labels**: `app=gpu-dev`, `gpu-dev/reservation-id`, `gpu-dev/user-id`, `gpu-dev/gpu-type`

### NodePort Service

- **Port**: SSH (22) exposed as NodePort (30000-32767 range, dynamically allocated)
- **Selector**: Matches pod by name

### Headless Service

- **ClusterIP**: None
- **Purpose**: DNS resolution for multinode communication

### Jupyter Service (optional)

- **Port**: 8888 exposed as NodePort
- **Created when**: `--jupyter` flag or `jupyter enable` edit action

## Git Cache Service

- **Namespace**: `management`
- **PVC**: 100Gi gp3
- **Deployment**:
  - Init container: Seeds pytorch mirror via `git clone --mirror`
  - Nginx container: Serves HTTP on port 8080
  - Updater sidecar: `git remote update` every hour
- **Service**: ClusterIP on port 8080
- **Terraform**: `/terraform-gpu-devservers/git-cache.tf`

## Resource Allocation Formula

From `get_pod_resource_limits()` and `get_pod_resource_requests()` (line 3623, 3673):

```python
cpu_fraction = gpu_count / config["max_gpus"]
cpu_limit = int(config["cpus"] * cpu_fraction)
memory_limit = int(config["memory_gb"] * cpu_fraction * 1024)  # in Mi
```

For multinode (all 8 GPUs), uses 100% of node resources.

### EFA Allocation

From `_pod_uses_efa()` (line 3713):
- EFA is allocated only when `gpu_count == max_gpus` AND `efa_count > 0`
- This means only full-node reservations (8 GPUs) get EFA access
- For multinode, EFA is always allocated

## NCCL Environment Variables

From `get_nccl_env_vars()` (line 3766), set on all multi-GPU pods:
- `NCCL_SOCKET_IFNAME=^lo,docker` -- exclude loopback and docker interfaces
- `NCCL_IB_HCA=^mlx` -- exclude Mellanox IB (use EFA instead)
- `NCCL_ALGO=ring,tree` -- allow both algorithms
- `FI_PROVIDER=efa` -- use EFA fabric interface
- `FI_EFA_USE_DEVICE_RDMA=0` -- disable GDR (not yet working)
- `NCCL_NET_GDR_LEVEL=0` -- disable GDR
- `OFI_NCCL_PROTOCOL=SENDRECV` -- host-staged EFA protocol
