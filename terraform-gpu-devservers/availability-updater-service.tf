# Availability Updater Service - Kubernetes CronJob
# Replaces Lambda function - runs every 5 minutes to update GPU availability

# ============================================================================
# ECR Repository for Availability Updater Service
# ============================================================================

resource "aws_ecr_repository" "availability_updater_service" {
  name                 = "${var.prefix}-availability-updater"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.prefix}-availability-updater"
    Environment = local.current_config.environment
  }
}

resource "aws_ecr_lifecycle_policy" "availability_updater_service" {
  repository = aws_ecr_repository.availability_updater_service.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last 5 images"
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = 5
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# ============================================================================
# Build and Push Availability Updater Docker Image
# ============================================================================

locals {
  # Hash availability updater files to detect changes (including shared utilities)
  availability_updater_files = fileset("${path.module}/availability-updater-service", "**/*.py")

  availability_updater_hash = md5(join("", concat(
    [for file in local.availability_updater_files : filemd5("${path.module}/availability-updater-service/${file}")],
    [for file in local.shared_files : filemd5("${path.module}/shared/${file}")],
    [filemd5("${path.module}/availability-updater-service/Dockerfile")],
    [filemd5("${path.module}/availability-updater-service/requirements.txt")]
  )))

  availability_updater_image_tag  = "v1-${substr(local.availability_updater_hash, 0, 8)}"
  availability_updater_image_uri  = "${aws_ecr_repository.availability_updater_service.repository_url}:${local.availability_updater_image_tag}"
  availability_updater_latest_uri = "${aws_ecr_repository.availability_updater_service.repository_url}:latest"
}

resource "null_resource" "availability_updater_build" {
  triggers = {
    updater_hash = local.availability_updater_hash
    ecr_repo     = aws_ecr_repository.availability_updater_service.repository_url
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e

      echo "Building and pushing availability updater Docker image..."

      # Get current architecture
      ARCH=$(uname -m)
      echo "Detected architecture: $ARCH"

      # Set platform for Docker build (always build for linux/amd64 for EKS)
      if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
        PLATFORM="linux/amd64"
        echo "Building for linux/amd64 platform (cross-compilation from $ARCH)"
      else
        PLATFORM="linux/amd64"
        echo "Building for linux/amd64 platform"
      fi

      # Build from terraform-gpu-devservers directory (parent of availability-updater-service)
      # This allows Docker to access both availability-updater-service/ and shared/
      cd ${path.module}

      # Login to ECR
      echo "Logging into ECR..."
      aws ecr get-login-password --region ${local.current_config.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.availability_updater_service.repository_url}

      # Build image with correct platform from parent directory
      # Use -f to specify Dockerfile location and set build context to current directory
      echo "Building Docker image for platform: $PLATFORM"
      docker build --platform=$PLATFORM \
        -f availability-updater-service/Dockerfile \
        -t ${local.availability_updater_image_uri} \
        .

      # Also tag as latest
      docker tag ${local.availability_updater_image_uri} ${local.availability_updater_latest_uri}

      # Push both tags
      echo "Pushing Docker image..."
      docker push ${local.availability_updater_image_uri}
      docker push ${local.availability_updater_latest_uri}

      echo "Availability updater image successfully built and pushed!"
      echo "Image URI: ${local.availability_updater_image_uri}"
    EOF

    working_dir = path.module
  }

  depends_on = [
    aws_ecr_repository.availability_updater_service,
    aws_ecr_lifecycle_policy.availability_updater_service
  ]
}

# ============================================================================
# IAM Role for Availability Updater Service (IRSA)
# ============================================================================

# IAM role for availability updater service to access AWS resources
resource "aws_iam_role" "availability_updater_role" {
  name = "${var.prefix}-availability-updater-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.eks.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:${kubernetes_namespace.controlplane.metadata[0].name}:availability-updater-sa"
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "${var.prefix}-availability-updater-role"
    Environment = local.current_config.environment
  }
}

# IAM policy for STS (needed for Kubernetes client setup)
resource "aws_iam_role_policy" "availability_updater_sts" {
  name = "sts-access"
  role = aws_iam_role.availability_updater_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "sts:GetCallerIdentity"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM policy for EKS (needed to interact with cluster)
resource "aws_iam_role_policy" "availability_updater_eks" {
  name = "eks-access"
  role = aws_iam_role.availability_updater_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster"
        ]
        Resource = aws_eks_cluster.gpu_dev_cluster.arn
      }
    ]
  })
}

# IAM policy for EC2 (needed for instance queries)
resource "aws_iam_role_policy" "availability_updater_ec2" {
  name = "ec2-access"
  role = aws_iam_role.availability_updater_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeInstances",
          "ec2:DescribeAvailabilityZones"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM policy for AutoScaling (needed for ASG queries)
resource "aws_iam_role_policy" "availability_updater_autoscaling" {
  name = "autoscaling-access"
  role = aws_iam_role.availability_updater_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "autoscaling:DescribeAutoScalingGroups"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM policy for EBS and snapshots (needed for disk reconciliation - read-only)
resource "aws_iam_role_policy" "availability_updater_ebs" {
  name = "ebs-access"
  role = aws_iam_role.availability_updater_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:DescribeVolumes",
          "ec2:DescribeSnapshots",
          "ec2:DescribeVolumesModifications"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM policy for disk quarantine feature (write operations)
resource "aws_iam_role_policy" "availability_updater_disk_quarantine" {
  name = "disk-quarantine-access"
  role = aws_iam_role.availability_updater_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DiskQuarantineTagging"
        Effect = "Allow"
        Action = [
          "ec2:CreateTags",
          "ec2:DeleteTags"
        ]
        Resource = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:volume/*"
      },
      {
        Sid    = "DiskQuarantineSnapshot"
        Effect = "Allow"
        Action = [
          "ec2:CreateSnapshot"
        ]
        Resource = [
          "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:volume/*",
          "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:snapshot/*"
        ]
      },
      {
        Sid    = "DiskQuarantineCleanup"
        Effect = "Allow"
        Action = [
          "ec2:DeleteVolume"
        ]
        Resource = "arn:aws:ec2:*:${data.aws_caller_identity.current.account_id}:volume/*"
      }
    ]
  })
}

# ============================================================================
# Kubernetes Resources for Availability Updater Service
# ============================================================================

# Service Account with IRSA annotation
resource "kubernetes_service_account" "availability_updater" {
  metadata {
    name      = "availability-updater-sa"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.availability_updater_role.arn
    }
  }

  depends_on = [
    aws_iam_role.availability_updater_role
  ]
}

# ClusterRole for Kubernetes API access
resource "kubernetes_cluster_role" "availability_updater" {
  metadata {
    name = "availability-updater-role"
  }

  # Node access for GPU availability checks
  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list", "watch"]
  }

  # Pod access for GPU request tracking
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/status"]
    verbs      = ["get", "list", "watch"]
  }
}

# ClusterRoleBinding to bind role to service account
resource "kubernetes_cluster_role_binding" "availability_updater" {
  metadata {
    name = "availability-updater-binding"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.availability_updater.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.availability_updater.metadata[0].name
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }
}

# ConfigMap for availability updater configuration
resource "kubernetes_config_map" "availability_updater" {
  metadata {
    name      = "availability-updater-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }

  data = {
    AWS_REGION       = local.current_config.aws_region
    EKS_CLUSTER_NAME = aws_eks_cluster.gpu_dev_cluster.name
    POSTGRES_HOST    = "postgres-primary.${kubernetes_namespace.controlplane.metadata[0].name}.svc.cluster.local"
    POSTGRES_PORT    = "5432"
    POSTGRES_USER    = "gpudev"
    POSTGRES_DB      = "gpudev"
  }
}

# CronJob for availability updater
resource "kubernetes_cron_job_v1" "availability_updater" {
  metadata {
    name      = "availability-updater"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "availability-updater"
    }
  }

  spec {
    # Run every 5 minutes at fixed clock times (00, 05, 10, 15, etc.)
    # This ensures predictable scheduling and shorter wait after deployments
    schedule = "0,5,10,15,20,25,30,35,40,45,50,55 * * * *"

    # Forbid concurrent runs to prevent race conditions during disk reconciliation
    concurrency_policy = "Forbid"

    # Keep last 3 successful and 3 failed jobs
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3

    job_template {
      metadata {
        labels = {
          app = "availability-updater"
        }
      }

      spec {
        # Job should complete within 10 minutes (increased to accommodate disk reconciliation)
        active_deadline_seconds = 600

        # Don't retry failed jobs (CronJob will run again in 5 minutes)
        backoff_limit = 0

        template {
          metadata {
            labels = {
              app = "availability-updater"
            }
          }

          spec {
            service_account_name = kubernetes_service_account.availability_updater.metadata[0].name
            restart_policy       = "Never"

            # Run on CPU nodes
            node_selector = {
              NodeType = "cpu"
            }

            container {
              name  = "updater"
              image = local.availability_updater_image_uri

              # Pull latest image always
              image_pull_policy = "Always"

              # Environment variables from ConfigMap
              env_from {
                config_map_ref {
                  name = kubernetes_config_map.availability_updater.metadata[0].name
                }
              }

              # Pod name for tracking (from downward API)
              env {
                name = "POD_NAME"
                value_from {
                  field_ref {
                    field_path = "metadata.name"
                  }
                }
              }

              # Database password from secret
              env {
                name = "POSTGRES_PASSWORD"
                value_from {
                  secret_key_ref {
                    name = kubernetes_secret.postgres_credentials.metadata[0].name
                    key  = "POSTGRES_PASSWORD"
                  }
                }
              }

              # Resource requests and limits
              resources {
                requests = {
                  cpu    = "250m"
                  memory = "512Mi"
                }
                limits = {
                  cpu    = "1000m"
                  memory = "2Gi"
                }
              }
            }
          }
        }
      }
    }
  }

  depends_on = [
    null_resource.availability_updater_build,
    kubernetes_service_account.availability_updater,
    kubernetes_cluster_role_binding.availability_updater,
    kubernetes_config_map.availability_updater,
    kubernetes_secret.postgres_credentials
  ]
}

# ============================================================================
# Outputs
# ============================================================================

output "availability_updater_service_status" {
  description = "Status of the availability updater service"
  value = {
    ecr_repository = aws_ecr_repository.availability_updater_service.repository_url
    image_tag      = local.availability_updater_image_tag
    image_uri      = local.availability_updater_image_uri
    cronjob_name   = kubernetes_cron_job_v1.availability_updater.metadata[0].name
    schedule       = kubernetes_cron_job_v1.availability_updater.spec[0].schedule
    namespace      = kubernetes_cron_job_v1.availability_updater.metadata[0].namespace
  }
}

