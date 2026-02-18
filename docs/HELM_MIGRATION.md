# Helm Migration Guide

## Overview

All Kubernetes resources for the GPU Dev Server control plane have been migrated from individual OpenTofu `kubernetes_*` resources to a single Helm chart (`charts/gpu-dev-server/`). OpenTofu still manages AWS infrastructure (EKS, IAM, VPC, ALB, etc.) and deploys the Helm chart via `helm_release.gpu_dev_server`.

## What Changed

### Before (TF-managed K8s)
- Each K8s resource (Deployment, Service, ConfigMap, StatefulSet, etc.) was a separate `kubernetes_*` resource in `.tf` files
- Changes required `tofu apply` for every K8s resource update
- No templating or environment-specific overrides

### After (Helm-managed K8s)
- All K8s resources live in `charts/gpu-dev-server/templates/`
- Single `helm_release.gpu_dev_server` in `helm-gpu-dev-server.tf` deploys everything
- Values override via `set` blocks in TF, or `values-local.yaml` for local dev
- Environment-specific configuration via `values.yaml` defaults + TF overrides

## Resources That Moved to Helm

| Resource | TF Source | Helm Template |
|----------|-----------|---------------|
| Namespaces | `kubernetes.tf` | `templates/namespaces.yaml` |
| PostgreSQL Primary/Replica | `kubernetes.tf` | `templates/postgres/` |
| API Service Deployment | `api-service.tf` | `templates/api-service/` |
| Reservation Processor | `reservation-processor-service.tf` | `templates/reservation-processor/` |
| Availability Updater CronJob | `availability-updater-service.tf` | `templates/availability-updater/` |
| Reservation Expiry CronJob | `reservation-expiry-service.tf` | `templates/reservation-expiry/` |
| Registry Caches | `kubernetes.tf` | `templates/registry/` |
| Image Prepuller DaemonSet | `kubernetes.tf` | `templates/image-prepuller/` |
| Storage Class | `kubernetes.tf` | `templates/storage-class.yaml` |
| GPU Dev RBAC | `kubernetes.tf` | `templates/rbac/gpu-dev-rbac.yaml` |
| BuildKit SA | `docker-build.tf` | `templates/rbac/buildkit-sa.yaml` |
| Database Migrations | `kubernetes.tf` | `templates/database-migration-job.yaml` |

## Resources Still in OpenTofu

These resources remain TF-managed because they're AWS-specific infrastructure:

- EKS cluster, VPC, subnets, security groups (`eks.tf`, `main.tf`)
- IAM roles and policies (IRSA for service accounts)
- ASG node groups (GPU and CPU)
- ALB + target groups + listener rules (`alb.tf`)
- Route53 DNS zones and records (`route53.tf`)
- ACM certificates
- ECR repositories
- S3 buckets
- EFS filesystems
- Docker image builds (`docker-build.tf`)
- SSH proxy on ECS (`ssh-proxy-service.tf`)

## How the helm_release Works

```hcl
# terraform-gpu-devservers/helm-gpu-dev-server.tf
resource "helm_release" "gpu_dev_server" {
  name      = "gpu-dev-server"
  chart     = "${path.module}/../charts/gpu-dev-server"
  namespace = "gpu-controlplane"

  # AWS-specific values injected via set blocks
  set { name = "cloudProvider.name"; value = "aws" }
  set { name = "cloudProvider.aws.eksClusterName"; value = aws_eks_cluster.gpu_dev_cluster.name }
  # ... more set blocks for secrets, IAM roles, registry URLs, etc.
}
```

TF injects AWS-specific values (IAM role ARNs, EFS IDs, ECR URLs, passwords) into the Helm chart via `set` blocks, keeping the chart itself cloud-agnostic.

## Migration Steps (for new TF state)

If the old `kubernetes_*` resources still exist in TF state, remove them before applying:

```bash
# Remove old TF-managed K8s resources from state
tofu state rm kubernetes_service_account.buildkit
tofu state rm kubernetes_service.api_service_public
tofu state rm aws_cloudfront_distribution.api_service

# Apply to deploy via Helm
tofu apply
```

## Local Development

The Helm chart supports local development via k3d:

```bash
cd local && ./setup.sh
# Uses: helm upgrade --install gpu-dev-server ./charts/gpu-dev-server -f values-local.yaml
```

See [LOCAL_DEVELOPMENT.md](LOCAL_DEVELOPMENT.md) for details.
