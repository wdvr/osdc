# Architecture

## Overview

OSDC is a GPU reservation platform that lets PyTorch developers reserve GPU instances via a CLI. The system creates Kubernetes pods on EKS with allocated GPUs, provides SSH access through a WebSocket proxy, and manages persistent storage through EBS snapshots and EFS.

## End-to-End Flow

```
Developer                      AWS
  |                             |
  |  gpu-dev reserve            |
  |  (CLI sends SQS message)   |
  |  ========================> SQS Queue
  |                             |
  |                        Lambda (reservation_processor)
  |                             |  - Validates request
  |                             |  - Checks GPU availability via K8s API
  |                             |  - Creates/restores persistent disk (EBS)
  |                             |  - Creates K8s pod + services
  |                             |  - Creates DNS record (Route53)
  |                             |  - Creates ALB target group (for Jupyter)
  |                             |  - Stores domain mapping (DynamoDB)
  |                             |  - Updates reservation status (DynamoDB)
  |                             |
  |  CLI polls DynamoDB         |
  |  <======================== Reservation status + connection info
  |                             |
  |  ssh dev@name.devservers.io |
  |  ========================> ALB (TLS termination)
  |                             |-> ECS Fargate (SSH proxy)
  |                             |   -> WebSocket tunnel
  |                             |      -> NodePort Service
  |                             |         -> Pod (SSH on port 22)
  |                             |
  |  (reservation expires)      |
  |                        Lambda (reservation_expiry)
  |                             |  - Warns user 30/15/5 min before
  |                             |  - Snapshots EBS volume
  |                             |  - Deletes pod + services
  |                             |  - Deletes DNS record
  |                             |  - Cleans up ALB target group
  |                             |  - Updates DynamoDB
```

## Components

### 1. CLI Tool (`gpu-dev`)
- **Location**: `/cli-tools/gpu-dev-cli/`
- **Published**: PyPI as `gpu-dev` package
- **Entry points**: `gpu-dev` (main CLI), `gpu-dev-ssh-proxy` (SSH ProxyCommand)
- **Communication**: Sends JSON messages to SQS, polls DynamoDB for status

### 2. SQS Queue
- **Resource**: `pytorch-gpu-dev-reservation-queue`
- **Purpose**: Async message queue between CLI and Lambda
- **Config**: Long polling (20s), visibility timeout 1000s, DLQ after 3 failures
- **Message types**: reservation, cancellation, extend, jupyter enable/disable, add_user, disk operations

### 3. Lambda: Reservation Processor
- **Resource**: `pytorch-gpu-dev-reservation-processor`
- **File**: `terraform-gpu-devservers/lambda/reservation_processor/index.py` (8729 lines)
- **Triggers**: SQS (event source mapping) + CloudWatch schedule (every 1 minute)
- **Runtime**: Python 3.13, 15 min timeout, 2GB memory
- **Responsibilities**:
  - Process new reservations (create pods, services, disks)
  - Process cancellations (cleanup resources)
  - Process extend requests
  - Process Jupyter enable/disable
  - Process disk management actions
  - Queue management (ETA updates, position tracking)
  - Multinode coordination

### 4. Lambda: Reservation Expiry
- **Resource**: `pytorch-gpu-dev-reservation-expiry`
- **File**: `terraform-gpu-devservers/lambda/reservation_expiry/index.py`
- **Trigger**: CloudWatch schedule (every 1 minute)
- **Responsibilities**:
  - Warn users before expiry (30, 15, 5 minutes via wall messages in pods)
  - Clean up expired reservations (snapshot disk, delete pod, cleanup DNS/ALB)
  - Clean up stale queued/pending reservations
  - Sync disk deletion status to EC2 snapshots

### 5. Lambda: Availability Updater
- **Resource**: `pytorch-gpu-dev-availability-updater`
- **File**: `terraform-gpu-devservers/lambda/availability_updater/index.py`
- **Triggers**: ASG capacity change events + CloudWatch schedule (every 1 minute)
- **Responsibilities**: Query K8s API for real GPU availability, update DynamoDB

### 6. EKS Cluster
- **Resource**: `pytorch-gpu-dev-cluster`
- **Node types**: GPU nodes (self-managed ASGs per GPU type), CPU management nodes
- **GPU Operator**: NVIDIA GPU Operator v25.3.3 (Helm)
- **Namespaces**: `gpu-dev` (user pods), `monitoring`, `management`, `kube-system`, `gpu-operator`

### 7. SSH Proxy (ECS Fargate)
- **Purpose**: WebSocket-to-TCP tunnel for SSH access
- **Architecture**: ALB -> ECS Fargate tasks -> NodePort on K8s nodes -> Pod SSH
- **Components**: Python asyncio server, 2 instances for HA
- **Domain**: `ssh.devservers.io` / `ssh.test.devservers.io`

### 8. DynamoDB Tables
| Table | Hash Key | Purpose |
|-------|----------|---------|
| `pytorch-gpu-dev-reservations` | `reservation_id` | All reservation state |
| `pytorch-gpu-dev-disks` | `user_id` + `disk_name` | Disk metadata tracking |
| `pytorch-gpu-dev-operations` | `operation_id` | Async operation results for CLI polling |
| `pytorch-gpu-dev-gpu-availability` | `gpu_type` | Real-time GPU availability |
| `pytorch-gpu-dev-ssh-domain-mappings` | `domain_name` | subdomain -> node_ip:port mappings |
| `pytorch-gpu-dev-alb-target-groups` | `reservation_id` | ALB target group tracking |

### 9. Docker Image
- **Base**: `pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel`
- **ECR**: `pytorch-gpu-dev-gpu-dev-image`
- **Includes**: SSH server, Jupyter, Claude Code, EFA stack, NCCL tests, oh-my-zsh, ccache
- **Pre-pulled**: DaemonSet on all GPU nodes

## AWS Services Used

| Service | Purpose |
|---------|---------|
| EKS | Kubernetes cluster for GPU pods |
| EC2 (ASGs) | Self-managed GPU and CPU nodes |
| SQS | Async message queue |
| Lambda | Reservation processing, expiry, availability |
| DynamoDB | State management (6 tables) |
| EFS | Shared ccache storage, per-user personal storage |
| EBS | Persistent disk volumes (per-user, snapshot-based) |
| ECR | Container image registry (dev image, SSH proxy, custom images) |
| Route53 | DNS records for pod access |
| ACM | Wildcard TLS certificates |
| ALB | HTTPS termination for Jupyter and SSH proxy |
| ECS Fargate | SSH WebSocket proxy service |
| CloudWatch | Lambda logs, scheduled triggers |
| S3 | Disk contents listings, Terraform state |
| NAT Gateway | Internet for multi-EFA private subnet nodes |

## Workspace Architecture

Two Terraform workspaces:

| Workspace | Region | Domain | GPU Types |
|-----------|--------|--------|-----------|
| `default` (test) | us-west-1 | test.devservers.io | t4, t4-az2, cpu-arm, cpu-x86, t4-small, h100 |
| `prod` | us-east-2 | devservers.io | b200, h200, h100, a100, t4, l4, a10g, cpu-arm, cpu-x86 |
