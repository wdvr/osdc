# Storage

## Overview

Three storage layers:
1. **EBS Persistent Disks** -- per-user, per-disk-name, snapshot-based, cross-AZ migration
2. **EFS Shared Storage** -- ccache sharing + per-user personal storage
3. **S3** -- disk contents listings, Terraform state

## EBS Persistent Disks

### Concept

Each user has named disks (e.g., `default`, `pytorch-dev`, `nccl-test`). Disks exist as EBS volumes while a reservation is active and as EBS snapshots between reservations. When a new reservation starts, the latest snapshot is restored to a new EBS volume in the correct AZ.

### Lifecycle

```
create_disk_from_snapshot_or_empty()
  |
  ├── No existing snapshot -> Create empty 500 GB gp3 volume
  |
  └── Existing snapshot found -> Restore from snapshot
       |
       ├── Same AZ -> Create volume directly
       └── Different AZ -> Create volume in target AZ from snapshot
```

### Volume Specifications

- **Size**: 500 GB gp3 (default for new volumes)
- **Type**: gp3
- **Tags**: `gpu-dev-user`, `disk_name`, `ActiveVolume=true`
- **AZ**: Matches the node where pod is scheduled

### Snapshot Management

**Creation triggers**:
- Reservation cancellation (`process_cancellation_request()`)
- Reservation expiry (`reservation_expiry` Lambda)

**Snapshot flow** (in `snapshot_utils.py`):
1. `capture_disk_contents()` -- runs `du -sh` + `tree -a -L 3` in pod, uploads to S3
2. `safe_create_snapshot()` -- checks for pending snapshots (dedup), creates new one with tags
3. Tags: `gpu-dev-user`, `disk_name`, `gpu-dev-snapshot-type`, `created_at`, `snapshot_content_s3`, `disk_size`

**Cleanup** (`cleanup_old_snapshots()`):
- Keeps 3 newest snapshots per user per disk
- Deletes snapshots older than 7 days (beyond keep count)
- Max 10 deletions per run (prevents Lambda timeout)

**Soft deletion**:
- `delete_disk` action sets `is_deleted=true` in DynamoDB
- Tags snapshots with `delete-date` (30 days from now)
- `sync_disk_deleted_snapshots()` in expiry Lambda handles actual deletion

### Key Functions

In `/terraform-gpu-devservers/lambda/reservation_processor/index.py`:

- `create_disk_from_snapshot_or_empty()` (line 5511) -- main disk provisioning entry point
- `needs_ebs_migration()` (line 342) -- checks if volume needs cross-AZ migration
- `migrate_ebs_across_az()` (line 572) -- snapshot + restore in new AZ, deletes old volume
- `create_or_find_persistent_disk_in_az()` (line 5755) -- creates or finds volume by tag
- `restore_ebs_from_existing_snapshot()` (line 701) -- creates volume from snapshot
- `attach_persistent_disk_to_node()` (line 5972) -- attaches EBS to EC2 instance
- `mark_disk_in_use()` (line 5462) -- updates DynamoDB `in_use` field

### ActiveVolume Tag

Single source of truth for which EBS volume is the "current" one for a user+disk. Only ONE volume per user per disk should have `ActiveVolume=true`. The code handles edge cases:
- Multiple active volumes: uses oldest, removes tag from others
- No active volumes: falls back to legacy behavior (finds volumes without tag, tags the oldest)

### DynamoDB Disks Table

**Table**: `pytorch-gpu-dev-disks`
**Hash key**: `user_id`, **Range key**: `disk_name`
**PITR**: Enabled

See [reservation-system.md](reservation-system.md) for full schema.

### CLI Disk Commands

In `/cli-tools/gpu-dev-cli/gpu_dev_cli/disks.py`:

- `list_disks()` -- queries DynamoDB, checks in-use status from both disks table AND reservations table
- `create_disk()` -- sends SQS message, Lambda creates DynamoDB entry
- `delete_disk()` -- soft delete with 30-day retention
- `clone_disk()` -- copies latest snapshot to new disk name
- `list_disk_content()` -- fetches latest snapshot contents from S3
- `unlock_disk()` -- clears stale `in_use` lock via SQS
- `rename_disk()` -- updates `disk_name` tag on all snapshots

### No Persistent Disk Flag

When user has a disk in use by another reservation and confirms "continue without persistent disk":
- CLI sets `no_persistent_disk=True` in SQS message
- Lambda skips ALL persistent disk logic (line 2087-2090)
- Pod uses EmptyDir volume instead

## EFS (Elastic File System)

### Shared ccache

- **Resource**: `aws_efs_file_system.ccache_shared`
- **Terraform**: `/terraform-gpu-devservers/efs.tf`
- **Throughput**: Elastic
- **Mount targets**: One per AZ (all public subnets)
- **Security group**: NFS (2049) from GPU Dev SG
- **Pod mount path**: `/shared/ccache`
- **Purpose**: Shared compiler cache across all users and reservations

### Per-User Personal EFS

- Created dynamically by `create_or_find_user_efs()` (line 758)
- Searches for existing EFS tagged `gpu-dev-user={user_id}`
- Creates new one if not found
- **Pod mount path**: `/shared/personal`
- **Purpose**: Persistent personal storage (dotfiles, configs)

## S3

### Disk Contents Bucket

- **Resource**: `{workspace}-disk-contents-{random_id}`
- **Terraform**: `/terraform-gpu-devservers/s3-disk-contents.tf`
- **Versioned**: Yes
- **Public access**: Blocked
- **Contents**: Disk listing files (`{user_id}/{disk_name}/{snapshot_id}-contents.txt`)
- **Metadata**: user_id, disk_name, snapshot_id, pod_name, capture_time, disk_size

### Terraform State

- **Bucket**: `terraform-gpu-devservers`
- **Region**: us-east-2
- **Key**: `runners/terraform.tfstate`
- **Locking**: DynamoDB table `tfstate-lock-gpu-devservers`

## Volume Mounts in Pods

| Mount Path | Source | Purpose |
|------------|--------|---------|
| `/workspace` | EBS PersistentVolume (or EmptyDir) | User workspace, persistent across sessions |
| `/shared/ccache` | Shared ccache EFS | Compiler cache |
| `/shared/personal` | Per-user EFS | Personal persistent storage |
| `/dev/shm` | EmptyDir (sizeLimit: 64Gi) | Shared memory for NCCL |
| `/home/dev/.ssh` | Init container output | SSH authorized_keys |
