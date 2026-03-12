# Reservation System

## End-to-End Flow

### 1. CLI Sends Request

```
gpu-dev reserve --gpu-type h100 --gpu-count 4 --hours 8 --name my-server
```

1. `authenticate_user()` validates AWS credentials + GitHub SSH key match
2. Interactive prompts fill missing options (GPU type, count, hours, Jupyter, disk)
3. Generates `reservation_id` (UUID) and `operation_id`
4. Sends JSON message to SQS queue `pytorch-gpu-dev-reservation-queue`
5. Creates DynamoDB reservation entry with status `pending`
6. Begins polling DynamoDB for status updates

### 2. Lambda Processes SQS Message

`handler()` receives SQS event (batch_size=1):

1. **Validation** (`validate_reservation_request()`):
   - Required fields: `user_id`, `gpu_count`
   - Valid GPU types: t4, l4, a10g, t4-small, a100, h100, h200, b200, cpu-arm, cpu-x86
   - Valid GPU counts: 1, 2, 4, 8, 16 (16 = multinode 2x8)
   - Duration <= 240 hours (GPU types), unlimited for CPU types
   - CLI version >= 0.3.8
   - Maintenance mode check

2. **GPU Availability Check** (`check_gpu_availability()`):
   - Queries K8s API for schedulable GPUs of requested type
   - Uses `GpuType` node label + pod resource requests to calculate available GPUs

3. **If GPUs available** -> `allocate_gpu_resources()`:
   - Determines target AZ via K8s node query
   - Creates/restores persistent disk
   - Creates K8s pod + services
   - Sets up DNS + ALB
   - Updates reservation to `active`

4. **If GPUs NOT available** -> queued:
   - Calculates queue position and ETA
   - Sets status to `queued`
   - Scheduled Lambda will retry every 1 minute

### 3. Resource Allocation (`allocate_gpu_resources()`)

This is the core orchestration function (line 2449, ~650 lines):

1. **Target AZ determination**: `get_target_az_for_reservation()` finds nodes with available GPUs
2. **Persistent disk setup** (if not `no_persistent_disk`):
   - `create_disk_from_snapshot_or_empty()` either:
     - Creates new empty EBS volume in target AZ
     - Restores from latest snapshot (with AZ migration if needed)
   - Marks disk as `in_use` in DynamoDB
3. **K8s resource creation**: `create_kubernetes_resources()`:
   - Creates pod with GPU resources, EFA if applicable
   - Creates NodePort service for SSH
   - Creates headless service (for multinode DNS)
   - Creates Jupyter service if requested
4. **Wait for pod ready**: `wait_for_pod_ready()` polls pod status (timeout: 840s)
5. **DNS setup**: `create_dns_record()` creates CNAME pointing to ALB
6. **SSH domain mapping**: `store_domain_mapping()` stores subdomain -> node_ip:port in DynamoDB
7. **ALB setup** (if Jupyter): Creates target group + listener rule
8. **Connection info update**: `update_reservation_connection_info()` stores SSH command, VS Code/Cursor links

### 4. CLI Receives Status Updates

CLI polls DynamoDB every 2 seconds. Status progression:

```
pending -> preparing -> active
pending -> queued -> preparing -> active
pending -> failed
```

Status messages displayed with live spinner:
- `pending` -- "Reservation submitted"
- `preparing` -- "Setting up persistent disk...", "Creating pod...", "Waiting for GPU node..."
- `queued` -- "Position #3 in queue, ETA ~15 min"
- `active` -- Connection info displayed
- `failed` -- Error message displayed

### 5. Connection

After `active` status, CLI displays:

```
SSH:     ssh dev@my-server.devservers.io
VS Code: code --remote ssh-remote+dev@my-server.devservers.io /home/dev
Cursor:  cursor://vscode-remote/ssh-remote+my-server.devservers.io/home/dev
```

SSH config file created at `~/.gpu-dev/{id[:8]}-sshconfig`:
```
Host gpu-dev-{id[:8]}
    HostName my-server.devservers.io
    User dev
    ForwardAgent yes
    ProxyCommand gpu-dev-ssh-proxy %h %p
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
```

### 6. SSH Connection Path

```
Client SSH -> gpu-dev-ssh-proxy (WebSocket) -> wss://ssh.devservers.io/tunnel/{hostname}
  -> ALB (TLS termination) -> ECS Fargate SSH proxy (port 8081)
  -> TCP connection to node_ip:node_port
  -> NodePort Service -> Pod SSH (port 22)
```

### 7. Expiry

Every minute, `reservation_expiry` Lambda:
1. Scans for active reservations approaching expiry
2. Sends wall messages at 30/15/5 minutes before expiry
3. Creates warning files (`/home/dev/WARN_EXPIRES_IN_*MIN.txt`)
4. On expiry:
   - Captures disk contents (tree listing) and uploads to S3
   - Creates EBS snapshot
   - Deletes K8s pod + services
   - Cleans up DNS, domain mapping, ALB
   - Sets status to `expired`

### 8. Cancellation

`gpu-dev cancel --id {prefix}`:
1. CLI sends cancellation message to SQS
2. Lambda validates ownership
3. Snapshot + cleanup (same as expiry)
4. Sets status to `cancelled`

## Queue Management

`process_scheduled_queue_management()` runs every 1 minute via CloudWatch:

1. Scans DynamoDB for reservations with status `queued`
2. Sorts by `requested_at` (FIFO)
3. For each queued reservation:
   - Checks GPU availability for requested type
   - If available: processes immediately via `allocate_gpu_resources()`
   - If not: updates queue position and ETA
4. Handles stale queued reservations (removes after timeout)

## DynamoDB Schema

### Reservations Table

**Table**: `pytorch-gpu-dev-reservations`
**Hash key**: `reservation_id` (string)

**GSIs**:
- `UserIndex`: hash=`user_id`
- `StatusIndex`: hash=`status`
- `StatusGpuTypeIndex`: hash=`status`, range=`gpu_type`
- `UserStatusIndex`: hash=`user_id`, range=`status`

**Key fields**:
| Field | Type | Description |
|-------|------|-------------|
| `reservation_id` | S | UUID |
| `user_id` | S | AWS username |
| `github_user` | S | GitHub username |
| `gpu_type` | S | GPU type |
| `gpu_count` | N | Number of GPUs |
| `status` | S | pending/preparing/queued/active/cancelled/expired/failed |
| `created_at` | S | ISO timestamp |
| `expires_at` | S | ISO timestamp |
| `duration_hours` | N | Requested duration |
| `name` | S | Server name |
| `disk_name` | S | Attached disk name |
| `no_persistent_disk` | BOOL | Skip persistent disk |
| `ebs_volume_id` | S | Attached EBS volume ID |
| `pod_name` | S | K8s pod name |
| `node_name` | S | K8s node name |
| `node_ip` | S | Node IP address |
| `node_port` | N | SSH NodePort |
| `ssh_command` | S | Full SSH command |
| `ssh_command_with_domain` | S | SSH via domain |
| `jupyter_url` | S | Jupyter Lab URL |
| `jupyter_token` | S | Jupyter auth token |
| `detailed_status` | S | Human-readable status message |
| `failure_reason` | S | Error details |
| `warning_sent` | S | Comma-separated warning IDs sent |
| `queue_position` | N | Position in queue |
| `estimated_wait_minutes` | N | Estimated wait time |

### Disks Table

**Table**: `pytorch-gpu-dev-disks`
**Hash key**: `user_id` (string)
**Range key**: `disk_name` (string)
**PITR**: Enabled

**Key fields**:
| Field | Type | Description |
|-------|------|-------------|
| `user_id` | S | Owner |
| `disk_name` | S | Disk identifier |
| `size_gb` | N | Volume size in GB |
| `disk_size` | S | Usage size (e.g., "1.2G") |
| `created_at` | S | ISO timestamp |
| `last_used` | S | ISO timestamp |
| `snapshot_count` | N | Total snapshots taken |
| `pending_snapshot_count` | N | In-progress snapshots |
| `is_backing_up` | BOOL | Snapshot in progress |
| `in_use` | BOOL | Currently attached |
| `attached_to_reservation` | S | Reservation ID if in use |
| `is_deleted` | BOOL | Soft-deleted |
| `delete_date` | S | When snapshots will be purged |
| `latest_snapshot_content_s3` | S | S3 path to contents listing |

### Operations Table

**Table**: `pytorch-gpu-dev-operations`
**Hash key**: `operation_id` (string)

Used for async operation tracking (disk create/delete/clone, jupyter enable/disable).

**Fields**: `operation_id`, `status` (completed/failed), `error`, `updated_at`

## Multinode Reservations

For 16+ GPUs (2+ nodes):

1. CLI sends `multinode_reservation` action
2. `process_multinode_reservation_request()` creates:
   - Master reservation (tracks overall state)
   - N child reservations (one per node)
3. Each child processes via `process_multinode_individual_node()`
4. `coordinate_multinode_reservation()` waits for all nodes ready
5. `setup_multinode_ssh()` configures passwordless SSH between all pods
6. DynamoDB-based distributed locking prevents race conditions

See [multi-node.md](multi-node.md) for details.

## Reservation Statuses

| Status | Description |
|--------|-------------|
| `pending` | Just created, waiting for processing |
| `preparing` | Lambda is setting up resources |
| `queued` | Insufficient GPUs, waiting in queue |
| `active` | Pod running, SSH accessible |
| `cancelled` | User cancelled, cleanup done |
| `expired` | Duration exceeded, cleanup done |
| `failed` | Error during setup |
