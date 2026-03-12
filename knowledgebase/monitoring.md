# Monitoring

## Overview

GPU monitoring uses DCGM Exporter -> Prometheus -> Grafana stack, deployed via `kube-prometheus-stack` Helm chart. Optional Grafana Cloud remote write for external dashboards.

## Terraform

- **File**: `/terraform-gpu-devservers/monitoring.tf`

## Components

### StorageClass

- **Name**: `gp3`
- **Provisioner**: `ebs.csi.aws.com`
- **Parameters**: `type=gp3`
- **Default**: Yes (`storageclass.kubernetes.io/is-default-class: "true"`)

### kube-prometheus-stack (Helm)

- **Chart**: `kube-prometheus-stack`
- **Repository**: `https://prometheus-community.github.io/helm-charts`
- **Namespace**: `monitoring`
- **Prometheus**:
  - Storage: 50 Gi PVC (gp3)
  - Retention: 15 days
  - Node selector: `NodeType=cpu`
- **Grafana**:
  - Service: NodePort 30080
  - Admin password: from `var.grafana_admin_password`
  - Node selector: `NodeType=cpu`

### NVIDIA GPU Operator

Deployed via Helm in `gpu-operator` namespace (see [kubernetes.md](kubernetes.md)):
- **DCGM**: Enabled (runs as DaemonSet on GPU nodes)
- **DCGM Exporter**: Enabled, exposes GPU metrics to Prometheus
- **Anti-affinity**: Both DCGM and DCGM Exporter have anti-affinity for nodes with label `gpu.monitoring/profiling-dedicated=true`

### Grafana Cloud (Optional)

Remote write configuration in `/terraform-gpu-devservers/grafana-cloud.auto.tfvars`:
- Prometheus remote write endpoint
- Username and password for authentication
- Only enabled when credentials are provided

## Dashboards

### NVIDIA DCGM Dashboard

- Pre-configured from Grafana community (ID 12239)
- Shows per-GPU metrics from DCGM Exporter

### Custom GPU Overview Dashboard

- Defined as JSON in `monitoring.tf`
- Panels:
  - GPU Utilization (per GPU, per node)
  - GPU Memory Usage
  - GPU Temperature
  - GPU Power Draw

## Profiling Node Setup

### Architecture

- DCGM Exporter runs on ALL GPU nodes EXCEPT profiling-dedicated nodes
- Profiling-dedicated nodes: reserved for Nsight profiling (ncu/nsys)
- DCGM and Nsight conflict because both need exclusive GPU access

### Profiling Node Labeler CronJob

- **Schedule**: Every 5 minutes
- **Defined in**: `/terraform-gpu-devservers/kubernetes.tf`
- **Logic**: For each GPU type that needs a profiling node, finds first unlabeled ready node, applies labels:
  - `gpu.monitoring/profiling-dedicated=true`
  - `nvidia.com/gpu.deploy.dcgm-exporter=false`

### Node-Level Profiling Config

Set in `/terraform-gpu-devservers/templates/al2023-user-data.sh` line 19, BEFORE driver installation:

```bash
echo "options nvidia NVreg_RestrictProfilingToAdminUsers=0" > /etc/modprobe.d/nvprof.conf
```

This allows non-root users (i.e., the `dev` user in pods) to use NVIDIA profiling tools.

### Pod-Level Profiling Config

In pod spec (Lambda `create_pod()` function):
- Linux capability: `SYS_ADMIN` (required for ncu/nsys GPU profiling)
- Environment: `NVIDIA_DRIVER_CAPABILITIES=compute,utility` (NOT `profile` -- unsupported by device plugin)

## Accessing Grafana

```bash
# Get any node IP
kubectl get nodes -o wide

# Access at: http://<node-ip>:30080
# Credentials: admin / <grafana_admin_password variable>
```

## Metrics Available

DCGM Exporter provides metrics like:
- `DCGM_FI_DEV_GPU_UTIL` -- GPU utilization %
- `DCGM_FI_DEV_FB_USED` -- GPU framebuffer memory used
- `DCGM_FI_DEV_FB_FREE` -- GPU framebuffer memory free
- `DCGM_FI_DEV_GPU_TEMP` -- GPU temperature
- `DCGM_FI_DEV_POWER_USAGE` -- GPU power consumption
- `DCGM_FI_DEV_SM_CLOCK` -- SM clock frequency
- `DCGM_FI_DEV_MEM_CLOCK` -- Memory clock frequency

## Troubleshooting

```bash
# Check DCGM pods (should NOT be on profiling nodes)
kubectl get pods -n gpu-operator -l app=nvidia-dcgm-exporter -o wide

# Verify Prometheus is scraping DCGM
kubectl port-forward -n monitoring svc/kube-prometheus-stack-prometheus 9090:9090
# Then: http://localhost:9090 -> query DCGM_FI_DEV_GPU_UTIL

# Check Grafana pods
kubectl get pods -n monitoring -l app.kubernetes.io/name=grafana

# Check GPU Operator status
kubectl get pods -n gpu-operator
```
