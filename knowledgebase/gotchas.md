# Gotchas and Known Issues

## Infrastructure

### NEVER Terminate EC2 Instances Directly

On 2026-03-09, an agent accidentally terminated 10 EC2 instances including 6 pet H100 instances from another team's capacity reservations. **Never run `aws ec2 terminate-instances`**, `stop-instances`, `delete-*`, or any destructive AWS CLI commands. Use Terraform for infrastructure changes.

### Capacity Reservation ASG Keys Must Be Stable

Each capacity reservation creates an ASG with a stable `key` field (e.g., `h100-cr0`, `h200-cr1`). Removing an entry by key does NOT shift other ASG names. If you remove `h100-cr0`, `h100-cr1` stays as `h100-cr1`. This prevents accidental instance termination.

### wait_for_capacity_timeout = "0"

All GPU ASGs set `wait_for_capacity_timeout = "0"`. Without this, Terraform would fail if ASG instances don't become healthy within the timeout, which happens often with GPU nodes that take 5-10 minutes to bootstrap.

### Terraform State Locking

S3 backend uses DynamoDB table `tfstate-lock-gpu-devservers` for state locking. If a `tf apply` is interrupted, you may need to force-unlock: `tf force-unlock <lock-id>`.

## NVIDIA / GPU

### Profiling Config Must Be Set BEFORE Driver Install

The `NVreg_RestrictProfilingToAdminUsers=0` modprobe config MUST be written to `/etc/modprobe.d/nvprof.conf` BEFORE `dnf install nvidia-driver`, because the driver install auto-loads kernel modules. If set after, the modules are already loaded with default settings and profiling won't work until reboot.

**Location**: `/terraform-gpu-devservers/templates/al2023-user-data.sh` line 19

### NVIDIA_DRIVER_CAPABILITIES Does NOT Support "profile"

The NVIDIA device plugin does not support the `profile` capability. Using `NVIDIA_DRIVER_CAPABILITIES=compute,profile,utility` causes pod creation to fail with "unsupported capabilities". Use only `compute,utility` and rely on `CAP_SYS_ADMIN` for profiling access.

### Fabric Manager Required for NVSwitch GPUs

A100-SXM4, H100, H200, B200 all use NVLink/NVSwitch for inter-GPU communication within a node. Without fabric manager running on the host, CUDA initialization fails with error 802. The bootstrap script handles this for gpu_type in (a100, b200, h200, h100).

### ibstat PATH Issue

Fabric manager expects `ibstat` in `/usr/bin/`, but Amazon Linux 2023 installs it to `/usr/sbin/`. The bootstrap script creates a symlink: `ln -sf /usr/sbin/ibstat /usr/bin/ibstat`.

## Lambda

### Decimal Type from DynamoDB

DynamoDB returns numbers as Python `Decimal` type. Multiplying `Decimal * float` raises `TypeError`. Fix: convert to `int()` early. This was fixed in `get_pod_resource_limits()` and `get_pod_resource_requests()` at line 3034 and 3117.

### Volume Detachment Timeout

When a user creates a second reservation while one is active, the EBS volume may still be attached. The Lambda waits up to 60 seconds for detachment. If it times out and `no_persistent_disk` is not set, the reservation fails. The exception handler at line 2275 must clear `persistent_volume_id = None` to prevent the volume from being attached anyway.

### Lambda 15-Minute Timeout

All three Lambdas have 15-minute timeouts. Long-running operations (snapshot creation, large disk copy) can approach this limit. `max_deletions_per_run=10` for snapshot cleanup prevents timeout.

### K8s Token Expiry

The STS bearer token for K8s API has ~15-minute TTL. Lambda uses a module-scope cache with 14-minute effective TTL and refreshes 60 seconds early. For long-running operations, the `refresh_api_key_hook` ensures automatic token refresh.

### SQS Visibility Timeout

Set to 1000 seconds (16.7 minutes). Must be longer than Lambda timeout (900 seconds) to prevent duplicate processing. If Lambda fails, the message becomes visible again after 1000 seconds.

## CLI

### SSH Proxy and Corporate Proxies

The `gpu-dev-ssh-proxy` strips all HTTP proxy environment variables (`HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY`) because corporate proxies can cause WebSocket handshake timeouts. This is done at the start of `tunnel_ssh()` in `ssh_proxy.py`.

### SSH Config Filenames and Special Characters

Multinode reservation names like "16x B200 multinode - Node 1/2" contain `/` which breaks filenames. SSH config files always use `{reservation_id[:8]}-sshconfig` format, not the name.

### Legacy Config Migration

Config was previously stored in `~/.gpu-dev-config` and `~/.gpu-dev-environment.json`. The `Config` class auto-migrates to `~/.config/gpu-dev/config.json` on first use.

## Networking

### EFA Cannot Cross Availability Zones

EFA only works within a single AZ. All multinode instances MUST be in the same AZ. The placement group ensures this, but if nodes end up in different AZs, NCCL will fail.

### EFA RDMA / GDR Not Working Yet

GPU Direct RDMA (GDR) via EFA is not yet functional:
- `fi_mr_regattr` returns EFAULT for flush buffer
- EFA kernel driver 2.17.3+ needed (has P2P support for NVIDIA 580 drivers)
- `efa-nv-peermem` module built from source but may not load
- Current workaround: `OFI_NCCL_PROTOCOL=SENDRECV` (host-staged, ~33 GB/s vs expected ~300 GB/s with GDR)

### OpenMPI Library Path

On EFA installer 1.47.0, OpenMPI libraries are in `/opt/amazon/openmpi/lib` (NOT `/opt/amazon/openmpi/lib64`). Using the wrong path causes `mpirun` to fail silently.

### NCCL Socket Interface

H100/H200/B200 nodes use `enp71s0`/`enp72s0` interfaces, NOT `eth0`. Setting `NCCL_SOCKET_IFNAME=eth0` will cause NCCL hangs. Use `NCCL_SOCKET_IFNAME=^lo,docker` (exclude pattern) instead.

## Storage

### Multiple Active Volumes Bug

If a user somehow ends up with multiple EBS volumes tagged `ActiveVolume=true`, the Lambda uses the oldest one and removes the tag from others. This is a defensive measure -- it should never happen in normal operation.

### Soft Delete 30-Day Retention

Disk deletion is soft: sets `is_deleted=true` in DynamoDB and tags snapshots with `delete-date` (30 days out). The `sync_disk_deleted_snapshots()` function in the expiry Lambda handles actual snapshot deletion after the retention period.

### Disk In-Use Check Race Condition

The `get_disk_in_use_status()` function checks TWO sources to prevent race conditions:
1. Disks table `in_use` field (reliable during cleanup)
2. Reservations table (catches in-progress reservations before Lambda sets `in_use`)

Without checking both, a user could delete a disk that's being set up by another reservation.

## Kubernetes

### Kubelet Auto-Start

After rebooting nodes, kubelet may not auto-start if `systemctl enable kubelet` wasn't called during bootstrap. The nodeadm init handles this, but if a node is manually rebooted (vs. terminated and recreated by ASG), kubelet needs manual restart.

### DCGM vs Nsight Profiling Conflict

DCGM Exporter and Nsight (ncu/nsys) both need exclusive GPU access. They cannot run on the same node. The profiling node labeler CronJob ensures one node per GPU type is dedicated to profiling (DCGM excluded via anti-affinity).

### Pod /dev/shm Size

Default `/dev/shm` is 64MB in Docker/containerd, too small for NCCL. The pod spec sets `emptyDir.sizeLimit: 64Gi` for the `/dev/shm` mount.

## Expiry

### Warning File Not Cleaned on Extend

When using `--extend`, the `WARN_EXPIRES_IN_5MIN.txt` file is not removed from the pod, and the expiry warning tracking in DynamoDB is not reset. Known issue documented in CLAUDE.md.

### Stale Queued Reservations

Reservations stuck in `queued` status for too long are cleaned up by the expiry Lambda. This catches cases where a GPU type is permanently unavailable.
