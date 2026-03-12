# CLI Tool (`gpu-dev`)

## Overview

- **Package**: `gpu-dev` on PyPI
- **Version**: 0.3.9 (in `/pyproject.toml`)
- **Source**: `/cli-tools/gpu-dev-cli/gpu_dev_cli/`
- **Entry points**:
  - `gpu-dev` -- main CLI (`cli.py`)
  - `gpu-dev-ssh-proxy` -- SSH ProxyCommand helper (`ssh_proxy.py`)
  - `gpu-dev-mcp` -- MCP server entry point
- **Framework**: Click + Rich for output formatting
- **Config file**: `~/.config/gpu-dev/config.json`

## Installation

```bash
pip install gpu-dev
```

## Configuration (`config.py`)

### Config Class

- **File**: `/cli-tools/gpu-dev-cli/gpu_dev_cli/config.py`
- **Config path**: `~/.config/gpu-dev/config.json`
- **Legacy paths** (auto-migrated): `~/.gpu-dev-config`, `~/.gpu-dev-environment.json`

### Environments

| Environment | Region | Workspace | Description |
|-------------|--------|-----------|-------------|
| `prod` (default) | us-east-2 | prod | Production |
| `test` | us-west-1 | default | Test environment |

### Resource Naming

All resources use prefix `pytorch-gpu-dev`:
- Queue: `pytorch-gpu-dev-reservation-queue`
- Tables: `pytorch-gpu-dev-reservations`, `pytorch-gpu-dev-disks`, `pytorch-gpu-dev-operations`, `pytorch-gpu-dev-gpu-availability`
- Cluster: `pytorch-gpu-dev-cluster`

### AWS Session

Tries `gpu-dev` profile first, falls back to default session. Sets `AWS_DEFAULT_REGION` from config.

## Authentication (`auth.py`)

- **File**: `/cli-tools/gpu-dev-cli/gpu_dev_cli/auth.py`
- **Flow**:
  1. `authenticate_user()`: Tests AWS access via `get_caller_identity()` + queue URL lookup
  2. Requires `github_user` in config (set via `gpu-dev config set github_user <name>`)
  3. `validate_ssh_key_matches_github_user()`: Runs `ssh git@github.com`, parses `Hi <username>!` response, compares with configured username (case-insensitive)

## Commands

### `gpu-dev reserve`
- **Purpose**: Reserve GPU resources
- **Options**:
  - `--gpu-type` / `-t` -- GPU type (t4, l4, a10g, a100, h100, h200, b200, cpu-arm, cpu-x86)
  - `--gpu-count` / `-g` -- Number of GPUs (1, 2, 4, 8, 16)
  - `--hours` / `-h` -- Duration in hours (supports floats, e.g., 0.25 for 15 min)
  - `--name` / `-n` -- Preferred server name
  - `--disk` / `-d` -- Disk name to attach
  - `--no-disk` -- Skip persistent disk
  - `--jupyter` / `-j` -- Enable Jupyter Lab
  - `--dockerfile` -- Custom Dockerfile path
  - `--no-interactive` -- Skip interactive prompts
- **Flow**:
  1. Authenticates user (AWS + GitHub SSH key validation)
  2. Interactive prompts if options not provided (via `interactive.py`)
  3. Checks for existing active reservations (warns about disk conflicts)
  4. Sends SQS message with reservation details
  5. Polls DynamoDB for status updates with live spinner
  6. Displays connection info (SSH command, VS Code link, Cursor link)
  7. Creates SSH config file in `~/.gpu-dev/`

### `gpu-dev list`
- **Purpose**: List reservations
- **Options**: `--all` / `-a` (include historical), `--json` (JSON output)
- **Output**: Rich table with ID, name, status, GPU type, GPUs, created, expires

### `gpu-dev cancel`
- **Purpose**: Cancel a reservation
- **Options**: `--id` / `-i` (reservation ID, supports prefix match)
- **Interactive**: If no ID specified, shows selection prompt

### `gpu-dev show`
- **Purpose**: Show detailed reservation info
- **Options**: `--id` / `-i` (reservation ID)

### `gpu-dev connect`
- **Purpose**: Print SSH connection command
- **Options**: `--id` / `-i` (reservation ID)

### `gpu-dev get-ssh-config`
- **Purpose**: Print SSH config for a reservation

### `gpu-dev avail`
- **Purpose**: Show GPU availability
- **Output**: Table of GPU types with total/available/max reservable

### `gpu-dev status`
- **Purpose**: Show system status

### `gpu-dev edit`
- **Purpose**: Modify active reservation
- **Subactions**: extend, add-user, jupyter enable/disable
- **Options**: `--id` / `-i` (reservation ID)

### `gpu-dev config` (group)
- `gpu-dev config show` -- display current config
- `gpu-dev config set <key> <value>` -- set config value (e.g., `github_user`)
- `gpu-dev config env <test|prod>` -- switch environment

### `gpu-dev disk` (group)
- `gpu-dev disk list` -- list user's disks with size, usage, backup status
- `gpu-dev disk create <name>` -- create new empty disk
- `gpu-dev disk delete <name>` -- soft-delete (30-day retention)
- `gpu-dev disk clone <source> <target>` -- clone disk from latest snapshot
- `gpu-dev disk contents <name>` -- show disk contents from latest snapshot
- `gpu-dev disk unlock <name>` -- clear stale in_use lock
- `gpu-dev disk rename <old> <new>` -- rename disk (updates snapshot tags)

### `gpu-dev help`
- **Purpose**: Show help for specific topics

## SQS Message Formats

### Reservation Request

```json
{
  "action": "reservation",
  "reservation_id": "<uuid>",
  "user_id": "<aws-username>",
  "github_user": "<github-username>",
  "gpu_count": 4,
  "gpu_type": "h100",
  "duration_hours": 8,
  "name": "my-server",
  "disk_name": "default",
  "no_persistent_disk": false,
  "jupyter": true,
  "dockerfile": null,
  "version": "0.3.9",
  "requested_at": "2025-01-01T00:00:00+00:00"
}
```

### Cancellation

```json
{
  "action": "cancellation",
  "reservation_id": "<uuid>",
  "user_id": "<aws-username>",
  "version": "0.3.9"
}
```

### Extend

```json
{
  "action": "extend",
  "reservation_id": "<uuid>",
  "user_id": "<aws-username>",
  "extend_hours": 4,
  "version": "0.3.9"
}
```

### Jupyter Enable/Disable

```json
{
  "action": "jupyter",
  "reservation_id": "<uuid>",
  "user_id": "<aws-username>",
  "jupyter_action": "enable|disable",
  "operation_id": "<uuid>",
  "version": "0.3.9"
}
```

### Add User

```json
{
  "action": "add_user",
  "reservation_id": "<uuid>",
  "user_id": "<aws-username>",
  "github_user_to_add": "<github-username>",
  "operation_id": "<uuid>",
  "version": "0.3.9"
}
```

### Disk Operations

```json
{
  "action": "create_disk|delete_disk|clone_disk|clear_disk_lock",
  "operation_id": "<uuid>",
  "user_id": "<aws-username>",
  "disk_name": "my-disk",
  "version": "0.3.9"
}
```

## SSH Config Management (`reservations.py`)

- **Directory**: `~/.gpu-dev/`
- **File format**: `{reservation_id[:8]}-sshconfig`
- **Include directive**: Adds `Include ~/.gpu-dev/*-sshconfig` to `~/.ssh/config` and `~/.cursor/ssh_config` (with user permission prompt, stored in `~/.gpu-dev/.ssh-config-permission`)
- **SSH config content**:
  ```
  Host <pod-name>
      HostName <subdomain>.devservers.io
      User dev
      ForwardAgent yes
      ProxyCommand gpu-dev-ssh-proxy %h %p
      StrictHostKeyChecking no
      UserKnownHostsFile /dev/null
  ```

## SSH Proxy (`ssh_proxy.py`)

- **File**: `/cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
- **Entry point**: `gpu-dev-ssh-proxy <target_host> <target_port>`
- **Protocol**: WebSocket (wss://) tunnel
- **Proxy hosts**: `ssh.devservers.io` (prod), `ssh.test.devservers.io` (test)
- **URL format**: `wss://{proxy_host}/tunnel/{target_host}`
- **Retry**: 3 attempts with exponential backoff (1s base, 5s max)
- **Non-retryable codes**: 400, 401, 403, 404, 4000, 4004
- **Proxy bypass**: Strips `HTTPS_PROXY`, `HTTP_PROXY`, `ALL_PROXY` env vars to avoid corporate proxy issues

## Disk Management (`disks.py`)

- **File**: `/cli-tools/gpu-dev-cli/gpu_dev_cli/disks.py`
- **In-use detection**: Checks TWO sources to prevent race conditions:
  1. Disks table `in_use` field (reliable for cleanup in progress)
  2. Reservations table (for in-progress reservations not yet started)
- **Legacy support**: For `default` disk, also checks reservations without `disk_name` field but with `ebs_volume_id`
- **Soft delete**: Sets `is_deleted=true` in DynamoDB, tags snapshots with `delete-date` (30 days from now)
- **Clone**: Copies latest snapshot, creates new DynamoDB entry

## Interactive Mode (`interactive.py`)

- **File**: `/cli-tools/gpu-dev-cli/gpu_dev_cli/interactive.py`
- **Library**: `questionary` for terminal-based prompts
- **Functions**: `select_gpu_type_interactive()`, `select_gpu_count_interactive()`, `select_duration_interactive()`, `select_jupyter_interactive()`, `select_reservation_interactive()`, `ask_name_interactive()`, `select_edit_action_interactive()`, `ask_github_username_interactive()`, `ask_extension_hours_interactive()`, `select_disk_interactive()`
