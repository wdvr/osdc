# Remote Deployment Guide (AWS)

## Prerequisites

- AWS account with EKS, EC2, IAM, Route53, ACM, S3, EFS permissions
- OpenTofu 1.8+ (`brew install opentofu`)
- Docker Desktop (for building service images)
- kubectl, helm, aws CLI

## Architecture

OpenTofu manages AWS infrastructure and deploys the Helm chart:

```
OpenTofu manages:                    Helm chart manages:
├── EKS cluster + node groups        ├── Namespaces
├── VPC, subnets, security groups    ├── PostgreSQL primary/replica
├── IAM roles (IRSA)                 ├── API Service deployment
├── ALB + listener rules             ├── Reservation Processor
├── Route53 DNS                      ├── CronJobs (availability, expiry)
├── ACM certificates                 ├── Registry caches
├── ECR repositories                 ├── Image prepuller
├── S3 buckets                       ├── RBAC (gpu-dev, buildkit)
├── EFS filesystems                  ├── Storage class
└── Docker image builds              └── Database migration job
```

## Deployment

### Initial Setup

```bash
cd terraform-gpu-devservers

# Initialize OpenTofu
tofu init

# Select workspace (default=test, prod=production)
tofu workspace select default  # or: tofu workspace new prod

# Create terraform.tfvars with required variables
cat > terraform.tfvars <<EOF
ghcr_username = "your-github-username"
ghcr_token    = "ghp_xxxxxxxxxxxx"
domain_name   = "test.devservers.io"
EOF

# Plan and apply
tofu plan
tofu apply
```

### Updating Services

After code changes, rebuild and redeploy:

```bash
# Rebuild API service image
tofu apply -target=null_resource.api_service_build

# Rebuild reservation processor image
tofu apply -target=null_resource.reservation_processor_build

# Update Helm chart (after modifying templates/values)
tofu apply -target=helm_release.gpu_dev_server

# Or apply everything
tofu apply
```

### Switching Environments

```bash
# Switch to test
./switch-to.sh test

# Switch to prod
./switch-to.sh prod
```

## Helm Values (AWS)

TF injects these AWS-specific values into the Helm chart:

| Value | Source |
|-------|--------|
| `cloudProvider.name` | `"aws"` |
| `cloudProvider.region` | `local.current_config.aws_region` |
| `cloudProvider.aws.eksClusterName` | `aws_eks_cluster...name` |
| `cloudProvider.aws.ecrRepositoryUrl` | ECR repository URL |
| `cloudProvider.aws.efsSecurityGroupId` | EFS security group |
| `cloudProvider.aws.ccacheSharedEfsId` | Shared ccache EFS ID |
| `apiService.service.type` | `"NodePort"` (for ALB routing) |
| `buildkit.serviceAccount.annotations` | IRSA role ARN |
| Various secrets | PostgreSQL password, registry creds |

## API Access

The API is served via ALB with HTTPS:

```bash
# URL format
https://api.<domain>  # e.g., https://api.test.devservers.io

# Health check
curl https://api.test.devservers.io/health

# Get URL from TF output
tofu output api_service_url
```

## Monitoring

```bash
# Pod status
kubectl get pods -n gpu-controlplane
kubectl get pods -n gpu-dev

# API logs
kubectl logs -n gpu-controlplane -l app=api-service -f

# Processor logs
kubectl logs -n gpu-controlplane -l app=reservation-processor -f

# Node status
kubectl get nodes -o wide
```
