# Multi-Node GPU Communication

## Overview

OSDC supports multi-node GPU reservations (16+ GPUs across 2+ nodes) with NCCL communication over EFA. Currently uses host-staged SENDRECV protocol; GPU Direct RDMA (GDR) is not yet working.

## Supported Configurations

| GPU Type | Max Nodes | Max GPUs | EFA Interfaces/Node |
|----------|-----------|----------|---------------------|
| h100 | 4 | 32 | 32 |
| h200 | 4 | 32 | 32 |
| b200 | 4 | 32 | 32 |
| a100 | 4 | 32 | 4 |

Other GPU types (t4, l4, a10g) support only single-node reservations.

## Reservation Flow

### 1. CLI Request

```bash
gpu-dev reserve --gpu-type h100 --gpu-count 16 --hours 8
```

GPU count > 8 triggers multinode path (16 = 2 nodes x 8 GPUs).

### 2. Master Reservation

`process_multinode_reservation_request()` (line 1228):

1. Creates master reservation with `is_multinode=true`, `total_nodes=N`
2. Creates N child reservations, each with:
   - `parent_reservation_id` = master ID
   - `node_index` (0-based)
   - `gpu_count` = 8 (per node)
3. Sends individual `multinode_node` SQS messages for each child

### 3. Per-Node Processing

`process_multinode_individual_node()` (line 1482):

1. Allocates resources for one node (same as single-node flow)
2. Creates pod with multinode-specific config:
   - Full EFA allocation (all interfaces)
   - NCCL environment variables
   - Headless service for DNS
3. After pod ready, checks if all nodes are ready

### 4. Coordination

`coordinate_multinode_reservation()` (line 1336):

1. Uses DynamoDB-based distributed lock (`acquire_multinode_lock()`)
2. Waits for all N pods to be running
3. Sets up inter-node SSH via `setup_multinode_ssh()`
4. Updates master reservation with all connection info

### 5. SSH Setup

`setup_multinode_ssh()` (line 1806):

Configures passwordless SSH between all pods:
1. Generates SSH key pair in each pod
2. Collects all public keys
3. Distributes authorized_keys to all pods
4. Creates SSH config with hostnames for each node
5. Verifies connectivity between all nodes

## NCCL Configuration

### Working Configuration (SENDRECV Protocol)

Set as environment variables in pod spec via `get_nccl_env_vars()` (line 3766):

| Variable | Value | Purpose |
|----------|-------|---------|
| `NCCL_SOCKET_IFNAME` | `^lo,docker` | Exclude loopback and docker interfaces |
| `NCCL_IB_HCA` | `^mlx` | Exclude Mellanox IB (use EFA) |
| `NCCL_ALGO` | `ring,tree` | Allow both algorithms |
| `FI_PROVIDER` | `efa` | Use EFA fabric interface |
| `FI_EFA_USE_DEVICE_RDMA` | `0` | Disable GDR (not working) |
| `NCCL_NET_GDR_LEVEL` | `0` | Disable GDR |
| `OFI_NCCL_PROTOCOL` | `SENDRECV` | Host-staged EFA protocol |

### Network Interface Notes

- H100/H200/B200 nodes use `enp71s0`/`enp72s0`, NOT `eth0`
- `NCCL_SOCKET_IFNAME=^lo,docker` uses exclusion pattern to auto-detect correct interface
- Setting `NCCL_SOCKET_IFNAME=eth0` causes NCCL hangs

## EFA Setup

### Host Level (User Data)

In `/terraform-gpu-devservers/templates/al2023-user-data.sh`:

1. EFA installer v1.47.0 (`-y -g --skip-limit-conf --no-verify`)
2. EFA kernel module reload
3. `efa-nv-peermem` built from `amzn/amzn-drivers` source
4. Hugepages: 5128 x 2MB = ~10 GB allocated before nodeadm

### Pod Level

- EFA resources allocated: `vpc.amazonaws.com/efa: {efa_count}`
- Hugepages allocated: `hugepages-2Mi: 5120Mi`
- Only for full-node (8 GPU) reservations with `efa_count > 0`
- EFA device plugin DaemonSet (v0.3.3) exposes EFA interfaces to K8s

### Placement Groups

- One cluster placement group per GPU type
- Ensures all nodes in same rack for lowest latency
- Configured in launch templates via Terraform

## Benchmark Results

From 2x p5.48xlarge (16 GPUs total):

| Algorithm | Avg Bus BW | Peak Bus BW |
|-----------|------------|-------------|
| Ring | ~9.5 GB/s | ~13.4 GB/s |
| Tree | ~21.4 GB/s | ~33.6 GB/s |
| Ring+Tree (auto) | ~21.0 GB/s | ~33.6 GB/s |
| Single-node NVLink | ~34 GB/s | (reference) |

NCCL auto-selects tree algorithm for large messages (~2x faster than ring).

## GPU Direct RDMA (GDR) Status

**NOT WORKING** -- future optimization target.

### Current Blockers

| Issue | Detail |
|-------|--------|
| `fi_mr_regattr` failure | Returns EFAULT for flush buffer (even host memory) |
| EFA device version | Version 6 (above aws-ofi-nccl blocklist threshold 1-3) |
| EFA kernel driver | 2.17.2a (need 2.17.3+ for P2P with NVIDIA 580 drivers) |
| nvidia-peermem | Module not found for kernel 6.12.68 |
| efa-nv-peermem | Built from source, may not load on all kernels |

### To Enable GDR in Future

1. Update EFA kernel driver to 2.17.3+ (supports P2P with NVIDIA 580 open drivers)
2. Ensure `efa-nv-peermem` module loads successfully
3. Set `FI_EFA_USE_DEVICE_RDMA=1`, `NCCL_NET_GDR_LEVEL=3`
4. Remove `OFI_NCCL_PROTOCOL=SENDRECV`

### Expected GDR Performance

~300-370 GB/s bus bandwidth (vs ~33 GB/s current with SENDRECV).

## Distributed Lock

`acquire_multinode_lock()` (line 1542):
- Uses DynamoDB conditional write with TTL
- Prevents race conditions when multiple Lambda invocations try to coordinate simultaneously
- Lock key: master reservation ID
- TTL: 300 seconds
- `release_multinode_lock()` deletes the lock item

## Error Handling

- `fail_all_multinode_reservations()`: If any node fails, marks all child + master reservations as failed
- `queue_all_multinode_reservations()`: If insufficient GPUs, queues all child reservations
- Individual node failures don't automatically fail the entire multinode setup -- the coordination function handles partial failures
