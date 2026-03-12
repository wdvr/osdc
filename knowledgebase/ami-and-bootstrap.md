# AMI and Bootstrap

## AMI Selection

Defined in `/terraform-gpu-devservers/eks.tf` via data sources:

### EKS-Optimized AL2023 (x86_64)

```hcl
data "aws_ami" "eks_al2023_x86" {
  filter { name = "name", values = ["amazon-eks-node-al2023-x86_64-standard-1.33-*"] }
}
```

Used by: most GPU types (t4, l4, a10g, t4-small, a100) and CPU nodes

### EKS-Optimized AL2023 (ARM64)

```hcl
data "aws_ami" "eks_al2023_arm64" {
  filter { name = "name", values = ["amazon-eks-node-al2023-arm64-standard-1.33-*"] }
}
```

Used by: cpu-arm nodes (c7g.8xlarge)

### Deep Learning Base (for Multi-EFA)

```hcl
data "aws_ami" "deep_learning_base" {
  filter { name = "name", values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Amazon Linux 2023)*"] }
}
```

Used by: h100, h200, b200 (GPU types with multi-EFA network interfaces)

## GPU Node Bootstrap

**File**: `/terraform-gpu-devservers/templates/al2023-user-data.sh`

### Execution Order

1. **Disable default nodeadm** (lines 8-12):
   ```bash
   systemctl disable nodeadm-config.service
   systemctl disable nodeadm-run.service
   ```

2. **NVIDIA profiling config** (line 19, BEFORE driver install):
   ```bash
   echo "options nvidia NVreg_RestrictProfilingToAdminUsers=0" > /etc/modprobe.d/nvprof.conf
   ```
   Must be before driver install because `dnf install nvidia-driver` auto-loads kernel modules.

3. **NVIDIA driver installation** (lines 22-23):
   ```bash
   dnf config-manager --add-repo https://developer.download.nvidia.com/compute/cuda/repos/amzn2023/x86_64/cuda-amzn2023.repo
   dnf install -y nvidia-driver nvidia-driver-cuda
   ```
   Installs NVIDIA driver 580.82.07 for CUDA 13 support.

4. **EFA driver update + efa-nv-peermem** (lines 26-59):
   - Only if EFA hardware detected (`/sys/class/infiniband` or `lspci | grep EFA`)
   - Installs build dependencies (cmake, kernel-devel, gcc, dkms)
   - Downloads and runs EFA installer v1.47.0 (`-y -g --skip-limit-conf --no-verify`)
   - Reloads EFA kernel module
   - Builds `efa-nv-peermem` from `amzn/amzn-drivers` GitHub repo for GPU Direct RDMA
   - Installs module to `/lib/modules/$(uname -r)/extra/`

5. **Fabric Manager** (lines 63-97):
   - Only for multi-GPU NVSwitch systems: a100, b200, h200, h100
   - Installs `infiniband-diags`, `nvidia-fabricmanager`, `nvlsm`
   - Creates symlink `/usr/bin/ibstat -> /usr/sbin/ibstat`
   - Loads `ib_umad` kernel module
   - Enables and starts `nvidia-fabricmanager.service`
   - Enables persistent mode: `nvidia-smi -pm 1`

6. **NVIDIA module loading** (lines 99-108):
   ```bash
   modprobe nvidia
   modprobe nvidia_uvm
   modprobe efa-nv-peermem || modprobe nvidia-peermem || echo "GDR disabled"
   nvidia-smi -pm 1  # Creates /dev/nvidia* device files
   ```

7. **nodeadm configuration** (lines 114-153):
   - Fetches cluster CA from AWS API
   - Writes nodeadm config YAML with:
     - Cluster name, API endpoint, CA cert
     - kubelet config: cpuManagerPolicy=static, systemReserved (2 CPU, 4Gi), kubeReserved (2 CPU, 4Gi)
     - Node labels: `NodeType=gpu`, `GpuType=${gpu_type}`, `nvidia.com/gpu.deploy.driver=false`
     - Optional profiling labels if `profiling_dedicated=true`

8. **EFA hugepages** (lines 143-151):
   ```bash
   echo 5128 > /proc/sys/vm/nr_hugepages
   echo "vm.nr_hugepages = 5128" > /etc/sysctl.d/90-efa-hugepages.conf
   ```
   Must happen BEFORE nodeadm so kubelet reports hugepages as allocatable.

9. **nodeadm init** (line 153):
   ```bash
   /usr/bin/nodeadm init --config-source file:///tmp/nodeadm-config.yaml
   ```

10. **Network tuning** (lines 156-162):
    ```bash
    net.core.rmem_default=262144000
    net.core.rmem_max=262144000
    net.core.wmem_default=262144000
    net.core.wmem_max=262144000
    ```

11. **Image pre-pull** (lines 165-168):
    - Initial pull of GPU dev container image via crictl
    - Cron job every 30 minutes to refresh ECR credentials and re-pull

### Template Variables

| Variable | Description |
|----------|-------------|
| `${gpu_type}` | GPU type string |
| `${region}` | AWS region |
| `${cluster_name}` | EKS cluster name |
| `${cluster_endpoint}` | EKS API endpoint |
| `${container_image}` | ECR image URI |
| `${profiling_dedicated}` | Boolean for profiling node |

## CPU Node Bootstrap

**File**: `/terraform-gpu-devservers/templates/al2023-cpu-user-data.sh`

Minimal bootstrap:
1. Disable default nodeadm services
2. Write nodeadm config with label `NodeType=cpu`
3. Run `nodeadm init`

No NVIDIA drivers, EFA, or fabric manager.

## Docker Image

**File**: `/terraform-gpu-devservers/docker/Dockerfile`
**Base**: `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel`
**ECR**: `pytorch-gpu-dev-gpu-dev-image`

### Key Components

| Component | Version | Purpose |
|-----------|---------|---------|
| CUDA Toolkit | 13.0 | GPU compute |
| EFA | 1.47.0 | Cross-node networking |
| NCCL Tests | Latest | Multi-GPU benchmarking |
| Jupyter Lab | Latest | Web-based IDE |
| Node.js | 20.x | For Claude Code |
| Claude Code | Latest | AI assistant |
| oh-my-zsh | Latest | Shell enhancement |

### User

- **Username**: `dev`
- **UID**: 1081
- **Home**: `/home/dev`
- **Shell**: zsh (with oh-my-zsh)
- **Sudo**: Passwordless

### Included Scripts

| Script | Purpose |
|--------|---------|
| `setup-dotfiles-persistence` | Sets up dotfile backup/restore with EFS |
| `backup-dotfiles` | Backs up dotfiles to EFS |
| `restore-dotfiles` | Restores dotfiles from EFS |
| `list-dotfile-versions` | Lists dotfile backup versions |
| `restore-dotfiles-version` | Restores specific version |
| `dotfiles-shutdown-handler` | Pre-shutdown backup |
| `nproc_wrapper` | Reports container CPU count instead of node |

### Build Process

Defined in `/terraform-gpu-devservers/ecr.tf`:
```bash
docker buildx build --platform linux/amd64 -t ${repo_url}:${hash} -t ${repo_url}:latest --push .
```

After build, restarts the prepuller DaemonSet:
```bash
kubectl rollout restart daemonset/gpu-dev-image-prepuller -n gpu-dev
```

### Custom User Images

Users can build custom images via BuildKit:
- ECR repo: `gpu-dev-custom-images` (keep 10 images)
- Docker Hub pull-through cache for base images
- OIDC provider for IRSA (BuildKit service account)
- Defined in `/terraform-gpu-devservers/docker-build.tf`
