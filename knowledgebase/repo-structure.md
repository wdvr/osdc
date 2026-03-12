# Repository Structure

## Top-Level

```
osdc/
  CLAUDE.md                          # Agent instructions + project state (very detailed)
  pyproject.toml                     # Package config: gpu-dev v0.3.9, entry points
  .gitignore
  PROGRESS.md                        # Progress tracking
  TODO.md                            # Task list
  PR_DESCRIPTION.md                  # PR template
  post.md                            # Blog post draft
  osdc-deck.zip                      # Presentation deck
```

## CLI Tool (`cli-tools/gpu-dev-cli/`)

```
cli-tools/gpu-dev-cli/
  README.md                          # CLI usage documentation
  ZERO_CONFIG_SETUP.md               # Zero-config setup guide
  minimal-iam-policy.json            # Minimum IAM policy for CLI users
  gpu_dev_cli/
    __init__.py                      # Version from importlib.metadata
    cli.py                           # Click CLI entry point (~3700 lines)
                                     #   Commands: reserve, list, cancel, show, connect,
                                     #   get-ssh-config, help, avail, status, config, edit, disk
    config.py                        # Config class: ~/.config/gpu-dev/config.json
                                     #   Environments: test (us-west-1) and prod (us-east-2)
    auth.py                          # AWS auth + GitHub SSH key validation
    reservations.py                  # ReservationManager class, SQS message sending, polling
    disks.py                         # Disk management: list, create, delete, clone, contents
    interactive.py                   # Questionary-based interactive prompts
    ssh_proxy.py                     # WebSocket SSH ProxyCommand (gpu-dev-ssh-proxy)
    name_generator.py                # DNS name sanitization
```

## CLI Support Scripts

```
cli-tools/scripts/
  clear_stale_disk_locks.py          # Admin script to clear stale disk locks in DynamoDB
```

## Terraform Module (`terraform-gpu-devservers/`)

```
terraform-gpu-devservers/
  main.tf                            # VPC, subnets, security groups, placement groups
                                     #   Workspace configs (default/prod), capacity reservations
                                     #   GPU type definitions, subnet assignments
  variables.tf                       # Input variables with defaults
  outputs.tf                         # CLI config output, resource IDs
  backend.tf                         # S3 backend: terraform-gpu-devservers bucket
  eks.tf                             # EKS cluster, node IAM roles, launch templates, ASGs
                                     #   AMI data sources, CPU node group
  lambda.tf                          # Reservation processor Lambda + IAM + build
  expiry.tf                          # Expiry Lambda + IAM + build + CloudWatch schedule
  availability.tf                    # Availability updater Lambda + EventBridge triggers
  queue.tf                           # SQS queue + DLQ + DynamoDB tables
                                     #   Tables: reservations, disks, operations
  kubernetes.tf                      # K8s aws-auth, namespace, RBAC, GPU operator (Helm),
                                     #   EFA device plugin, image prepuller DaemonSet,
                                     #   profiling node labeler CronJob
  monitoring.tf                      # Prometheus + Grafana (kube-prometheus-stack Helm),
                                     #   Grafana Cloud remote write, custom dashboards
  efs.tf                             # EFS security group, shared ccache filesystem + mount targets
  ecr.tf                             # ECR repo for dev image, Docker build + push
  docker-build.tf                    # ECR for custom user images, pull-through cache (Docker Hub),
                                     #   OIDC provider, BuildKit IRSA role + service account
  alb.tf                             # Application Load Balancer for Jupyter and SSH proxy,
                                     #   ALB target group tracking table
  ssh-proxy.tf                       # SSH proxy ECS service: ECR repo, task def, service, SGs
  ssh-proxy-service.tf               # SSH domain mappings DynamoDB table + IAM policies
  route53.tf                         # Route53 zones, NS delegation, ACM wildcard cert
  git-cache.tf                       # In-cluster git cache: PVC, deployment (nginx + updater),
                                     #   ClusterIP service
  s3-disk-contents.tf                # S3 bucket for disk contents (ls -R output)
  grafana-cloud.auto.tfvars          # Grafana Cloud credentials (contains real values!)
  .terraform.lock.hcl                # Provider lock file
  errored.tfstate                    # Errored state file (should be gitignored)
  switch-to.sh                       # Workspace switching helper
  pyproject.toml                     # Lambda linting config
  README.md                          # Module documentation
```

## Templates

```
terraform-gpu-devservers/templates/
  al2023-user-data.sh                # GPU node bootstrap: NVIDIA driver 580, EFA, fabric manager,
                                     #   efa-nv-peermem, nodeadm, hugepages, image pre-pull
  al2023-cpu-user-data.sh            # CPU node bootstrap: nodeadm only
  user-data.sh                       # Legacy template (unused?)
  user-data-self-managed.sh          # Legacy template (unused?)
```

## Lambda Functions

```
terraform-gpu-devservers/lambda/
  reservation_processor/
    index.py                         # Main handler (8729 lines): reservation CRUD,
                                     #   pod creation, disk management, multinode, queue mgmt
    buildkit_job.py                   # BuildKit K8s Job creation for Dockerfile builds
    requirements.txt                 # kubernetes, boto3, pyyaml
  reservation_processor.zip          # Built Lambda package

  reservation_expiry/
    index.py                         # Expiry handler: warnings, cleanup, snapshot sync
    requirements.txt                 # kubernetes, boto3
  reservation_expiry.zip             # Built Lambda package

  availability_updater/
    index.py                         # Availability handler: K8s GPU queries, DynamoDB updates
    requirements.txt                 # kubernetes, boto3
  availability_updater.zip           # Built Lambda package

  shared/
    __init__.py                      # Exports: setup_kubernetes_client, K8sGPUTracker
    k8s_client.py                    # EKS auth: STS presigned URL -> k8s-aws-v1 bearer token
    k8s_resource_tracker.py          # K8sGPUTracker: real-time GPU capacity from K8s API
    snapshot_utils.py                # EBS snapshot creation/restoration, disk contents capture
    dns_utils.py                     # Route53 record management, name generation
    alb_utils.py                     # ALB target group/listener rule management
    requirements.txt                 # kubernetes, boto3

  migration/
    tag_largest_snapshots.py         # One-off migration script
```

## Docker Image

```
terraform-gpu-devservers/docker/
  Dockerfile                         # Based on pytorch/pytorch:2.9.1-cuda12.8-cudnn9-devel
                                     #   CUDA 13.0, EFA 1.47.0, NCCL tests, Jupyter, Claude Code
  .dockerignore
  ssh_config                         # sshd_config
  shell_env                          # Common shell environment variables
  bashrc, bashrc_ext                 # Bash configuration
  bash_profile                       # Bash login profile
  zshrc, zshrc_ext                   # Zsh configuration with oh-my-zsh
  zprofile                           # Zsh login profile
  profile                            # Generic login profile
  motd_script                        # Message of the day script
  nproc_wrapper                      # Reports container CPU count instead of node CPUs
  setup-dotfiles-persistence         # Sets up dotfile backup/restore with EFS
  backup-dotfiles                    # Backs up dotfiles to EFS
  restore-dotfiles                   # Restores dotfiles from EFS
  list-dotfile-versions              # Lists dotfile backup versions
  restore-dotfiles-version           # Restores specific dotfile version
  dotfiles-shutdown-handler          # Pre-shutdown dotfile backup
  build-with-efa.sh                  # EFA build helper
```

## Docker Example

```
terraform-gpu-devservers/docker-example/
  Dockerfile                         # Example Dockerfile for custom images
  hello.txt                          # Example file
```

## SSH Proxy

```
terraform-gpu-devservers/ssh-proxy/
  Dockerfile                         # Python image for ECS Fargate
  proxy.py                           # WebSocket server: receives WS, connects TCP to NodePort
  requirements.txt                   # websockets, boto3
```

## Scripts

```
terraform-gpu-devservers/scripts/
  CLEANUP_GUIDE.md                   # Cleanup procedures
  detect_empty_volumes.sh            # Find empty EBS volumes
  ec2_avail_probe.sh                 # Probe EC2 instance availability
  inspect_user_data.sh               # Inspect launch template user data
```

## Migrations

```
terraform-gpu-devservers/migrations/
  migrate_disks_to_named.py          # Migrate disk tracking to named disk format
  backfill_snapshot_contents.py      # Backfill disk contents to S3
  backfill_snapshot_contents.py.bak  # Backup of above
  check_snapshots.py                 # Snapshot verification script
  run_backfill.sh                    # Backfill runner script
```

## Admin Tools

```
admin/
  generate_stats.py                  # Usage analytics: fetches DynamoDB, generates charts
  requirements.txt                   # pandas, matplotlib, seaborn, boto3
  README.md                          # Usage instructions
  output/                            # Generated charts (PNG) and HTML dashboard
```

## Documentation

```
docs/
  USER_GUIDE.md                      # Comprehensive user guide (Quick Start, SSH, Jupyter, etc.)
  devgpu-features.html               # Feature comparison page
  docker-mark-blue.svg               # Docker icon
  icons8-cursor-ai.svg               # Cursor AI icon
```

## GitHub

```
.github/workflows/
  publish.yml                        # PyPI publish on v* tags (uses trusted publishers)
  no-gitlinks.yml                    # Validates no gitlinks in repo
```
