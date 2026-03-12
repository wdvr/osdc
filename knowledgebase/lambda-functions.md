# Lambda Functions

## Overview

Three Lambda functions handle the reservation lifecycle. All share a common K8s authentication layer via `shared/k8s_client.py`.

## Shared Utilities (`lambda/shared/`)

### k8s_client.py
- **Purpose**: EKS authentication for all Lambda functions
- **Auth mechanism**: Presigns STS `GetCallerIdentity` URL, base64url-encodes it as `k8s-aws-v1.<token>` bearer token
- **Token caching**: Module-scope cache survives warm starts; 14-minute effective TTL, refreshes when <60s remain
- **File**: `/terraform-gpu-devservers/lambda/shared/k8s_client.py`
- **Key function**: `setup_kubernetes_client()` returns `client.ApiClient` with auto-refresh hook

### k8s_resource_tracker.py
- **Class**: `K8sGPUTracker`
- **Purpose**: Real-time GPU capacity queries from K8s node/pod API
- **Methods**:
  - `get_gpu_capacity_info()` -- total/available/used GPUs per node
  - `get_pending_gpu_reservations()` -- pods pending due to GPU constraints
  - `estimate_wait_time(requested_gpus, active_reservations)` -- ETA based on expiry times
- **File**: `/terraform-gpu-devservers/lambda/shared/k8s_resource_tracker.py`

### snapshot_utils.py
- **File**: `/terraform-gpu-devservers/lambda/shared/snapshot_utils.py`
- **Functions**:
  - `safe_create_snapshot(volume_id, user_id, ...)` -- dedup-aware snapshot creation (checks for pending snapshots first)
  - `create_pod_shutdown_snapshot(volume_id, user_id)` -- wrapper for shutdown flow
  - `update_disk_snapshot_completed(user_id, disk_name)` -- decrements pending count, clears `is_backing_up`
  - `cleanup_old_snapshots(user_id, keep_count=3, max_age_days=7, max_deletions_per_run=10)` -- keeps newest, deletes old
  - `cleanup_all_user_snapshots(max_users_per_run=20)` -- scheduled cleanup across all users
  - `get_latest_snapshot(user_id, volume_id=None, include_pending=False)` -- filters out soft-deleted (has `delete-date` tag)
  - `capture_disk_contents(pod_name, namespace, user_id, disk_name, snapshot_id)` -- runs `du -sh` + `tree -a -L 3` in pod via K8s exec, uploads to S3
  - `get_snapshot_contents(snapshot_id=None, s3_path=None)` -- fetches contents from S3

### dns_utils.py
- **File**: `/terraform-gpu-devservers/lambda/shared/dns_utils.py`
- **Functions**:
  - `generate_random_name()` -- `adjective_animal` format (e.g., `brave_wolf`)
  - `sanitize_name(name)` -- DNS-safe, max 63 chars
  - `is_reserved_name(name)` -- blocks `www`, `api`, `admin`, `root`, `mail`, `ftp`, `ns`, `ns1`, `ns2`; `test` reserved in prod
  - `generate_unique_name(preferred_name)` -- checks DynamoDB mappings table for conflicts
  - `create_dns_record(subdomain, target_ip, target_port)` -- creates CNAME to ALB + TXT record for port
  - `delete_dns_record(subdomain, target_ip, target_port)` -- removes A + TXT records
  - `store_domain_mapping(subdomain, target_ip, target_port, reservation_id, expires_at)` -- writes to DynamoDB `ssh_domain_mappings` table
  - `delete_domain_mapping(subdomain)` -- removes from DynamoDB

### alb_utils.py
- **File**: `/terraform-gpu-devservers/lambda/shared/alb_utils.py`
- **Functions**:
  - `create_jupyter_target_group(reservation_id, pod_name, instance_id, jupyter_port)` -- creates ALB target group (name: `jupyter-{id[:8]}`)
  - `create_alb_listener_rule(subdomain, target_group_arn, priority)` -- host-header routing rule
  - `store_alb_mapping(reservation_id, ...)` -- saves to `alb_target_groups` DynamoDB table
  - `delete_alb_mapping(reservation_id)` -- deletes rule, then target group (2s sleep between), then DynamoDB record
  - `get_instance_id_from_pod(k8s_client, pod_name)` -- extracts EC2 instance ID from node's `provider_id`

### buildkit_job.py
- **File**: `/terraform-gpu-devservers/lambda/reservation_processor/buildkit_job.py`
- **Purpose**: Creates K8s Jobs for Dockerfile builds using BuildKit
- **Deduplication**: Uses context hash to avoid rebuilding identical images

---

## 1. Reservation Processor

- **Resource name**: `pytorch-gpu-dev-reservation-processor`
- **File**: `/terraform-gpu-devservers/lambda/reservation_processor/index.py` (8729 lines)
- **Runtime**: Python 3.13
- **Memory**: 2048 MB
- **Timeout**: 15 minutes (900 seconds)
- **Triggers**: SQS event source mapping (batch_size=1) + CloudWatch schedule (every 1 minute)
- **Terraform**: `/terraform-gpu-devservers/lambda.tf`

### Environment Variables

| Variable | Source |
|----------|--------|
| `RESERVATIONS_TABLE` | DynamoDB table name |
| `EKS_CLUSTER_NAME` | EKS cluster name |
| `REGION` | AWS region |
| `MAX_RESERVATION_HOURS` | From `var.max_reservation_hours` (240) |
| `DEFAULT_TIMEOUT_HOURS` | From `var.reservation_timeout_hours` (8) |
| `QUEUE_URL` | SQS queue URL |
| `PRIMARY_AVAILABILITY_ZONE` | First AZ in region |
| `GPU_DEV_CONTAINER_IMAGE` | ECR image URI |
| `EFS_SECURITY_GROUP_ID` | EFS SG ID |
| `EFS_SUBNET_IDS` | Comma-separated subnet IDs |
| `CCACHE_SHARED_EFS_ID` | Shared ccache EFS ID |
| `ECR_REPOSITORY_URL` | ECR repo URL |
| `LAMBDA_VERSION` | Current version (0.3.9) |
| `MIN_CLI_VERSION` | Minimum CLI version (0.3.8) |
| `OPERATIONS_TABLE` | Operations DynamoDB table |
| `DISKS_TABLE_NAME` | Disks DynamoDB table |
| `DISK_CONTENTS_BUCKET` | S3 bucket for disk contents |
| `DOMAIN_NAME` | Domain (devservers.io / test.devservers.io) |
| `HOSTED_ZONE_ID` | Route53 hosted zone |
| `JUPYTER_ALB_ARN` | ALB ARN |
| `JUPYTER_ALB_LISTENER_ARN` | ALB HTTPS listener ARN |
| `JUPYTER_ALB_DNS` | ALB DNS name |
| `ALB_TARGET_GROUPS_TABLE` | ALB target groups DynamoDB table |
| `ALB_VPC_ID` | VPC ID |
| `SSH_DOMAIN_MAPPINGS_TABLE` | SSH domain mappings DynamoDB table |

### GPU_CONFIG (Single Source of Truth)

```python
GPU_CONFIG = {
    "t4":       {"instance_type": "g4dn.12xlarge",   "max_gpus": 4, "cpus": 48,  "memory_gb": 192,  "efa_count": 0},
    "l4":       {"instance_type": "g6.12xlarge",      "max_gpus": 4, "cpus": 48,  "memory_gb": 192,  "efa_count": 1},
    "a10g":     {"instance_type": "g5.12xlarge",      "max_gpus": 4, "cpus": 48,  "memory_gb": 192,  "efa_count": 1},
    "t4-small": {"instance_type": "g4dn.2xlarge",     "max_gpus": 1, "cpus": 8,   "memory_gb": 32,   "efa_count": 0},
    "g5g":      {"instance_type": "g5g.2xlarge",      "max_gpus": 2, "cpus": 8,   "memory_gb": 32,   "efa_count": 0},
    "a100":     {"instance_type": "p4d.24xlarge",     "max_gpus": 8, "cpus": 96,  "memory_gb": 1152, "efa_count": 4},
    "h100":     {"instance_type": "p5.48xlarge",      "max_gpus": 8, "cpus": 192, "memory_gb": 2048, "efa_count": 32},
    "h200":     {"instance_type": "p5e.48xlarge",     "max_gpus": 8, "cpus": 192, "memory_gb": 2048, "efa_count": 32},
    "b200":     {"instance_type": "p6-b200.48xlarge", "max_gpus": 8, "cpus": 192, "memory_gb": 2048, "efa_count": 32},
    "cpu-arm":  {"instance_type": "c7g.8xlarge",      "max_gpus": 0, "cpus": 32,  "memory_gb": 64,   "efa_count": 0},
    "cpu-x86":  {"instance_type": "c7i.8xlarge",      "max_gpus": 0, "cpus": 32,  "memory_gb": 64,   "efa_count": 0},
}
```

### Handler Routing (`handler()` at line 1110)

The handler receives events from two sources:
1. **SQS messages** -- contains `Records` array, each with `body` JSON
2. **CloudWatch scheduled events** -- `detail-type: "Scheduled Event"` triggers queue management

SQS message `action` field routes to:

| Action | Handler Function | Description |
|--------|-----------------|-------------|
| `reservation` | `process_reservation_request()` | New GPU reservation |
| `multinode_reservation` | `process_multinode_reservation_request()` | Multi-node GPU reservation |
| `multinode_node` | `process_multinode_individual_node()` | Individual node in multinode setup |
| `cancellation` | `process_cancellation_request()` | Cancel reservation |
| `extend` | `process_extend_reservation_action()` | Extend reservation duration |
| `jupyter` | `process_jupyter_action()` | Enable/disable Jupyter |
| `add_user` | `process_add_user_action()` | Add SSH user to pod |
| `create_disk` | `process_create_disk_action()` | Create empty disk entry |
| `delete_disk` | `process_delete_disk_action()` | Soft-delete disk (30-day retention) |
| `clone_disk` | `process_clone_disk_action()` | Clone disk snapshot |
| `clear_disk_lock` | `process_clear_disk_lock_action()` | Remove stale in_use lock |
| (scheduled) | `process_scheduled_queue_management()` | Process queued reservations |

### Key Functions

**Reservation Processing** (line 1923):
1. `validate_reservation_request()` -- checks required fields, GPU type validity, maintenance mode, GPU count limits (1/2/4/8/16), duration limits
2. `check_gpu_availability(gpu_type)` -- queries K8s API for schedulable GPUs of type
3. `create_reservation()` -- writes to DynamoDB with status `pending`
4. `allocate_gpu_resources()` -- orchestrates pod creation (line 2449):
   - Determines target AZ via `get_target_az_for_reservation()`
   - Creates/restores persistent disk via `create_disk_from_snapshot_or_empty()`
   - Creates K8s pod + services via `create_kubernetes_resources()`
   - Sets up DNS, ALB, domain mapping
   - Updates reservation with connection info

**Pod Creation** (`create_pod()` at line 3793):
- Sets resource limits based on GPU type (proportional CPU/memory per GPU)
- Configures EFA if `gpu_count == max_gpus && efa_count > 0`
- Sets NCCL environment variables for multi-GPU communication
- Adds `SYS_ADMIN` capability for GPU profiling
- Mounts persistent disk, ccache EFS, personal EFS
- Init container fetches GitHub SSH public keys
- Sets `NVIDIA_DRIVER_CAPABILITIES=compute,utility`

**Queue Management** (`process_scheduled_queue_management()` at line 7109):
- Runs every 1 minute via CloudWatch
- Scans for `queued` reservations
- Checks GPU availability for each
- Processes oldest-first (FIFO)
- Updates queue position and ETA

**Cancellation** (`process_cancellation_request()` at line 7354):
- Validates reservation ownership
- Creates shutdown snapshot (disk contents capture + EBS snapshot)
- Deletes K8s pod + services
- Cleans up DNS record, domain mapping, ALB target group
- Marks disk as not in use
- Updates reservation status to `cancelled`

**Multinode** (`process_multinode_reservation_request()` at line 1228):
- Creates master reservation + N child reservations
- Each child processes independently via `process_multinode_individual_node()`
- `coordinate_multinode_reservation()` waits for all nodes, sets up inter-node SSH
- Uses DynamoDB-based distributed lock (`acquire_multinode_lock()`)
- `setup_multinode_ssh()` configures passwordless SSH between all pods

### CLI Version Validation

`validate_cli_version()` at line 159 enforces minimum CLI version. Messages without `version` field are rejected. Current minimum: `0.3.8`.

### Retry Logic

`retry_with_backoff()` at line 85: exponential backoff for AWS API throttling errors (`Throttling`, `RequestLimitExceeded`, `TooManyRequestsException`, `ProvisionedThroughputExceededException`). Max 5 retries, initial 1s delay, max 32s delay.

---

## 2. Reservation Expiry

- **Resource name**: `pytorch-gpu-dev-reservation-expiry`
- **File**: `/terraform-gpu-devservers/lambda/reservation_expiry/index.py`
- **Runtime**: Python 3.13
- **Memory**: 1024 MB
- **Timeout**: 15 minutes
- **Trigger**: CloudWatch schedule (every 1 minute)
- **Terraform**: `/terraform-gpu-devservers/expiry.tf`

### Environment Variables

| Variable | Source |
|----------|--------|
| `RESERVATIONS_TABLE` | DynamoDB table name |
| `DISKS_TABLE_NAME` | Disks DynamoDB table |
| `EKS_CLUSTER_NAME` | EKS cluster name |
| `REGION` | AWS region |
| `DISK_CONTENTS_BUCKET` | S3 bucket for disk contents |

### Handler Flow

1. **Sync operations**: `sync_disk_deleted_snapshots()`, `sync_completed_snapshots()`
2. **Warning system**: Scans active reservations, sends wall messages at 30/15/5 minutes before expiry
3. **Expiry processing**: For expired reservations:
   - Captures disk contents via K8s exec
   - Creates EBS snapshot
   - Deletes K8s pod + services
   - Cleans up DNS, domain mapping, ALB
   - Updates reservation status to `expired`
4. **Stale cleanup**: Removes reservations stuck in `queued`/`pending` for too long

### Warning Files

Creates files in pod at `/home/dev/WARN_EXPIRES_IN_{30,15,5}MIN.txt` and sends `wall` messages. Tracks which warnings have been sent in DynamoDB `warning_sent` field.

---

## 3. Availability Updater

- **Resource name**: `pytorch-gpu-dev-availability-updater`
- **File**: `/terraform-gpu-devservers/lambda/availability_updater/index.py`
- **Runtime**: Python 3.11
- **Memory**: 512 MB (default)
- **Timeout**: 5 minutes
- **Triggers**: EventBridge ASG launch/terminate events + CloudWatch schedule (every 1 minute)
- **Terraform**: `/terraform-gpu-devservers/availability.tf`

### Environment Variables

| Variable | Source |
|----------|--------|
| `AVAILABILITY_TABLE` | DynamoDB table name |
| `SUPPORTED_GPU_TYPES` | JSON dict of GPU types |

### Handler Flow

1. Receives ASG capacity change event or scheduled trigger
2. Sets up shared K8s client once
3. Iterates ALL GPU types (not just the one that triggered)
4. For each GPU type:
   - Finds all ASGs matching `pytorch-gpu-dev-gpu-nodes-{gpu_type}*`
   - Calculates total capacity from running instances
   - **GPU nodes**: Queries K8s API for actual GPU allocations (`check_schedulable_gpus_for_type()`)
   - **CPU nodes**: Counts pod slots (3 users per node)
   - Calculates `full_nodes_available` (nodes with all GPUs free)
   - Calculates `max_reservable` (considering multinode for h100/h200/b200/a100, up to 4 nodes = 32 GPUs)
5. Updates `gpu_availability` DynamoDB table with:
   - `total_gpus`, `available_gpus`, `max_reservable`
   - `full_nodes_available`, `running_instances`, `desired_capacity`
   - `gpus_per_instance`, `last_updated`, `last_updated_timestamp`

---

## Build Process

All Lambda functions are built via `null_resource` in Terraform:

```bash
# reservation_processor build (lambda.tf)
cd lambda/reservation_processor
pip install -r requirements.txt --platform manylinux2014_x86_64 --only-binary=:all: -t .
cp -r ../shared .
zip -r ../reservation_processor.zip .

# Similar for reservation_expiry and availability_updater
```

The build triggers on changes to any `.py` or `requirements.txt` file in the Lambda directories.
