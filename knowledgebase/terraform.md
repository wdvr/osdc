# Terraform / OpenTofu

## Overview

All infrastructure is defined in `/terraform-gpu-devservers/`. Uses OpenTofu (aliased as `tf`). State stored in S3 with DynamoDB locking.

**Important**: Never run `tf apply` directly -- the user handles deployments.

## Backend

```hcl
# backend.tf
bucket         = "terraform-gpu-devservers"
key            = "runners/terraform.tfstate"
region         = "us-east-2"
dynamodb_table = "tfstate-lock-gpu-devservers"
```

## Workspaces

| Workspace | Region | Environment | Domain |
|-----------|--------|-------------|--------|
| `default` | us-west-1 | test | test.devservers.io |
| `prod` | us-east-2 | prod | devservers.io |

Configuration is selected via `local.current_config = local.workspace_configs[terraform.workspace]`.

## Providers

- `hashicorp/aws` ~> 5.0
- `hashicorp/kubernetes` ~> 2.23
- `hashicorp/helm` ~> 2.12
- `hashicorp/tls` ~> 4.0
- `hashicorp/random` ~> 3.4

All K8s/Helm providers authenticate via `aws eks get-token`.

## Key Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `prefix` | `pytorch-gpu-dev` | Resource name prefix |
| `vpc_cidr` | `10.0.0.0/16` | VPC CIDR |
| `subnet_cidr` | `10.0.0.0/20` | Primary subnet |
| `key_pair_name` | `pet-instances-skeleton-key-v2` | EC2 SSH key pair |
| `reservation_timeout_hours` | `8` | Default reservation timeout |
| `max_reservation_hours` | `240` | Maximum allowed reservation |
| `grafana_admin_password` | `admin` | Grafana password (sensitive) |
| `domain_name` | null (uses workspace config) | Domain for SSH access |

## Files and What They Create

### main.tf
- **VPC**: `aws_vpc.gpu_dev_vpc` (10.0.0.0/16)
- **Internet Gateway**: `aws_internet_gateway.gpu_dev_igw`
- **Subnets** (3 public + 3 private per AZ):
  - Primary: `10.0.0.0/20` (AZ[0])
  - Secondary: `10.0.16.0/20` (AZ[1])
  - Tertiary: `10.0.32.0/20` (AZ[2], conditional)
  - Private mirrors: `10.0.48.0/20`, `10.0.64.0/20`, `10.0.80.0/20`
- **NAT Gateway**: For multi-EFA private subnet instances
- **Security Groups**: Control plane SG, GPU dev SG (with EFA self-referencing rules)
- **Placement Groups**: One per GPU type with `use_placement_group=true`
- **Locals**: GPU type configs, capacity reservation flattening, subnet assignments

### eks.tf
- **EKS Cluster**: `aws_eks_cluster.gpu_dev_cluster`
- **IAM Roles**: Cluster role (EKSClusterPolicy), Node role (WorkerNode, CNI, ECR, EBS, Bedrock)
- **Addons**: vpc-cni, aws-ebs-csi-driver
- **AMI Data Sources**: EKS-optimized AL2023 (x86_64 + arm64), Deep Learning Base
- **Launch Templates**: One per GPU type + capacity reservation combo
  - 4TB gp3 root volume
  - EFA network interfaces (single or multi-card)
  - Capacity reservation specification
  - Placement group (if applicable)
- **ASGs**: One per `gpu_asg_configs` entry (GPU type x capacity reservation)
  - `wait_for_capacity_timeout = "0"` to prevent TF failures
  - Rolling instance refresh
- **CPU Nodes**: Separate launch template + ASG for c5.4xlarge management nodes

### lambda.tf
- **Lambda**: `reservation_processor` (Python 3.13, 15min timeout, 2GB)
- **IAM**: Role + policy (SQS, DynamoDB, EKS, EC2, EFS, ECR, Lambda invoke)
- **SQS Trigger**: Event source mapping (batch_size=1)
- **CloudWatch Schedule**: Every 1 minute for queue management
- **Build**: `null_resource` builds zip with pip install for linux/x86_64

### expiry.tf
- **Lambda**: `reservation_expiry` (Python 3.13, 15min timeout, 1GB)
- **IAM**: Role + policy (DynamoDB, EKS, EC2, S3, Lambda invoke)
- **CloudWatch Schedule**: Every 1 minute

### availability.tf
- **DynamoDB Table**: `gpu_availability` (hash: gpu_type)
- **Lambda**: `availability_updater` (Python 3.11, 5min timeout)
- **EventBridge**: ASG launch/terminate events + every 1 minute schedule

### queue.tf
- **SQS Queue**: `gpu_reservation_queue` (visibility 1000s, long poll 20s, DLQ after 3)
- **SQS DLQ**: `gpu_reservation_dlq`
- **DynamoDB Tables**:
  - `reservations` (hash: reservation_id, GSIs: UserIndex, StatusIndex, StatusGpuTypeIndex, UserStatusIndex)
  - `disks` (hash: user_id, range: disk_name, PITR enabled)
  - `operations` (hash: operation_id)

### kubernetes.tf
- **aws-auth ConfigMap**: Maps node role + 3 Lambda roles to K8s users
- **Namespace**: `gpu-dev`
- **RBAC**: ServiceAccount + Role + RoleBinding for gpu-dev pods
- **EFA Device Plugin**: DaemonSet (v0.3.3)
- **GPU Operator**: Helm release v25.3.3 (driver disabled, toolkit+devicePlugin+dcgm+gfd+mig enabled)
- **Image Prepuller**: DaemonSet to pre-pull dev image on all GPU nodes
- **Profiling Labeler**: CronJob (every 5min) to label one node per GPU type for Nsight

### monitoring.tf
- **StorageClass**: gp3 for Prometheus
- **Namespace**: monitoring
- **kube-prometheus-stack**: Helm chart with 50Gi persistent storage, 15-day retention
- **Grafana**: NodePort 30080, admin password from variable
- **Grafana Cloud**: Optional remote write (credentials in grafana-cloud.auto.tfvars)
- **Custom Dashboard**: JSON-defined GPU overview dashboard

### efs.tf
- **Security Group**: NFS (2049) from GPU dev SG
- **Shared ccache EFS**: One filesystem, elastic throughput, mount targets in all AZs

### ecr.tf
- **ECR Repo**: `pytorch-gpu-dev-gpu-dev-image` (mutable tags, lifecycle: keep 5)
- **Docker Build**: `null_resource` builds linux/amd64, pushes hash-tagged + latest
- **Rollout**: After build, `kubectl rollout restart` the prepuller DaemonSet

### docker-build.tf
- **ECR Repo**: `gpu-dev-custom-images` (for user Dockerfiles, keep 10)
- **Pull-Through Cache**: Docker Hub via ECR
- **OIDC Provider**: For IRSA (BuildKit service account)
- **BuildKit Role**: IRSA role for in-cluster Docker builds

### alb.tf
- **ALB**: `jupyter_alb` (external, HTTPS + HTTP redirect)
- **Security Group**: 443/80 inbound from anywhere
- **Target Groups**: Default (404), per-reservation (created by Lambda)
- **Listener**: HTTPS on 443 with wildcard cert
- **DynamoDB Table**: `alb_target_groups` for tracking

### ssh-proxy.tf
- **ECR**: SSH proxy image repo
- **ECS Cluster**: `ssh-proxy`
- **Task Definition**: Fargate (256 CPU, 512 MB), ports 8080 (health) + 8081 (WebSocket)
- **ECS Service**: 2 instances, public subnets
- **Target Groups**: HTTP (8080) + WebSocket (8081)
- **ALB Rule**: `ssh.{domain}` -> WebSocket target group (priority 1)

### route53.tf
- **Hosted Zone**: Subdomain zone if needed (test.devservers.io)
- **ACM Certificate**: Wildcard `*.{domain}` with DNS validation
- **NS Delegation**: Optional records for test->prod delegation

### git-cache.tf
- **Namespace**: management
- **PVC**: 100Gi gp3
- **Deployment**: init-container seeds pytorch mirror, nginx serves HTTP, updater refreshes hourly
- **Service**: ClusterIP on port 8080

### s3-disk-contents.tf
- **S3 Bucket**: `{workspace}-disk-contents-{random}` (versioned, no public access)

## Capacity Reservations (prod)

```
a100: cr-01cc0f00f28b095af (1 instance) + 1 on-demand
h100: cr-0a3f49b96fe03ca04 (4 instances) + 2 on-demand
h200: cr-0f6d0766f5d3339e6 (2, may be expired) + cr-06c9c978dea756a26 (3) + 2 on-demand
b200: cr-0c366fb8339a10f69 (1) + cr-08e7fee0b8dc3de5e (3) + 2 on-demand
```

Each capacity reservation creates a separate ASG with a stable `key` field (e.g., `h100-cr0`, `h200-cr1`). Removing an entry by key does not shift other ASG names.

## Key Outputs

- `cli_config` -- region, queue_url, reservations_table, cluster_name, supported_gpu_types
- `gpu_dev_image_uri` -- ECR image URI
- `ssh_proxy_endpoint` -- `ssh.{domain}`
- `jupyter_access_url` -- `https://<subdomain>.{domain}`
