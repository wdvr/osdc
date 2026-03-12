# OSDC Knowledgebase

Comprehensive documentation for the Open Developer Cloud (OSDC) codebase -- a GPU reservation and development server platform. Covers both the current AWS-native architecture (main branch) and the Helm-based cloud-agnostic migration (feat/helm-migration).

## Helm Migration (Active Work)

| Document | Description |
|----------|-------------|
| [helm-migration.md](helm-migration.md) | **Architecture, status, and gaps analysis for the Helm migration** |
| [one-pager.md](one-pager.md) | **Decision document: effort estimates, ciforge comparison, recommendation** |

## Current Architecture (main branch)

| Document | Description |
|----------|-------------|
| [architecture.md](architecture.md) | End-to-end system architecture, component interactions, data flows |
| [repo-structure.md](repo-structure.md) | Every directory and file explained |
| [terraform.md](terraform.md) | All Terraform/OpenTofu modules, resources, variables, outputs |
| [lambda-functions.md](lambda-functions.md) | Every Lambda function: triggers, env vars, handler logic |
| [cli-tool.md](cli-tool.md) | The `gpu-dev` CLI: commands, config, SQS message formats |
| [kubernetes.md](kubernetes.md) | EKS setup, GPU operator, node groups, pod specs, services |
| [reservation-system.md](reservation-system.md) | End-to-end reservation flow: CLI to SSH access |
| [networking.md](networking.md) | VPC, subnets, security groups, EFA, SSH proxy, ALB |
| [storage.md](storage.md) | EFS, EBS persistent disks, snapshots, S3 disk contents |
| [monitoring.md](monitoring.md) | DCGM, Prometheus, Grafana, profiling node setup |
| [ami-and-bootstrap.md](ami-and-bootstrap.md) | AMI selection, user-data scripts, NVIDIA driver setup |
| [workflows-and-ci.md](workflows-and-ci.md) | GitHub Actions, PyPI publishing, deployment process |
| [gotchas.md](gotchas.md) | Known issues, workarounds, things that bite you |
| [authentication.md](authentication.md) | GitHub SSH keys, AWS IAM, SSO, authorization model |
| [multi-node.md](multi-node.md) | NCCL, EFA, multi-GPU communication, multinode reservations |

## Quick Reference

- **CLI install**: `pip install gpu-dev`
- **CLI version**: `0.3.9` (in `/pyproject.toml`)
- **Lambda version**: `0.3.9` (in `lambda.tf` env var `LAMBDA_VERSION`)
- **Terraform backend**: S3 bucket `terraform-gpu-devservers`, region `us-east-2`
- **Workspaces**: `default` (test, us-west-1) and `prod` (us-east-2)
- **Prefix**: `pytorch-gpu-dev` (all resource names)
- **EKS cluster**: `pytorch-gpu-dev-cluster`
- **Namespace**: `gpu-dev` (user pods), `monitoring`, `management`, `kube-system`, `gpu-operator`
- **Domain**: `devservers.io` (prod), `test.devservers.io` (test)
