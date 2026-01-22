# Reservation Expiry Service - Kubernetes CronJob
# Replaces Lambda function - runs every 5 minutes to check expiring reservations

# ============================================================================
# ECR Repository for Reservation Expiry Service
# ============================================================================

resource "aws_ecr_repository" "reservation_expiry_service" {
  name                 = "${var.prefix}-reservation-expiry"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.prefix}-reservation-expiry"
    Environment = local.current_config.environment
  }
}

resource "aws_ecr_lifecycle_policy" "reservation_expiry_service" {
  repository = aws_ecr_repository.reservation_expiry_service.name

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
# Build and Push Reservation Expiry Docker Image
# ============================================================================

locals {
  # Hash reservation expiry files to detect changes (including shared utilities)
  reservation_expiry_files = fileset("${path.module}/reservation-expiry-service", "**/*.py")
  
  reservation_expiry_hash = md5(join("", concat(
    [for file in local.reservation_expiry_files : filemd5("${path.module}/reservation-expiry-service/${file}")],
    [for file in local.shared_files : filemd5("${path.module}/shared/${file}")],
    [filemd5("${path.module}/reservation-expiry-service/Dockerfile")],
    [filemd5("${path.module}/reservation-expiry-service/requirements.txt")]
  )))

  reservation_expiry_image_tag  = "v1-${substr(local.reservation_expiry_hash, 0, 8)}"
  reservation_expiry_image_uri  = "${aws_ecr_repository.reservation_expiry_service.repository_url}:${local.reservation_expiry_image_tag}"
  reservation_expiry_latest_uri = "${aws_ecr_repository.reservation_expiry_service.repository_url}:latest"
}

resource "null_resource" "reservation_expiry_build" {
  triggers = {
    expiry_hash = local.reservation_expiry_hash
    ecr_repo    = aws_ecr_repository.reservation_expiry_service.repository_url
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e

      echo "Building and pushing reservation expiry Docker image..."

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

      # Build from terraform-gpu-devservers directory (parent of reservation-expiry-service)
      # This allows Docker to access both reservation-expiry-service/ and shared/
      cd ${path.module}

      # Login to ECR
      echo "Logging into ECR..."
      aws ecr get-login-password --region ${local.current_config.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.reservation_expiry_service.repository_url}

      # Build image with correct platform from parent directory
      # Use -f to specify Dockerfile location and set build context to current directory
      echo "Building Docker image for platform: $PLATFORM"
      docker build --platform=$PLATFORM \
        -f reservation-expiry-service/Dockerfile \
        -t ${local.reservation_expiry_image_uri} \
        .

      # Also tag as latest
      docker tag ${local.reservation_expiry_image_uri} ${local.reservation_expiry_latest_uri}

      # Push both tags
      echo "Pushing Docker image..."
      docker push ${local.reservation_expiry_image_uri}
      docker push ${local.reservation_expiry_latest_uri}

      echo "Reservation expiry image successfully built and pushed!"
      echo "Image URI: ${local.reservation_expiry_image_uri}"
    EOF

    working_dir = path.module
  }

  depends_on = [
    aws_ecr_repository.reservation_expiry_service,
    aws_ecr_lifecycle_policy.reservation_expiry_service
  ]
}

# ============================================================================
# IAM Role for Reservation Expiry Service (IRSA)
# ============================================================================

# IAM role for reservation expiry service to access AWS resources
resource "aws_iam_role" "reservation_expiry_role" {
  name = "${var.prefix}-reservation-expiry-role"

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
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:${kubernetes_namespace.controlplane.metadata[0].name}:reservation-expiry-sa"
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "${var.prefix}-reservation-expiry-role"
    Environment = local.current_config.environment
  }
}

# IAM policy for STS (needed for Kubernetes client setup)
resource "aws_iam_role_policy" "reservation_expiry_sts" {
  name = "sts-access"
  role = aws_iam_role.reservation_expiry_role.id

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
resource "aws_iam_role_policy" "reservation_expiry_eks" {
  name = "eks-access"
  role = aws_iam_role.reservation_expiry_role.id

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

# IAM policy for EC2 (needed for volume/snapshot management)
resource "aws_iam_role_policy" "reservation_expiry_ec2" {
  name = "ec2-access"
  role = aws_iam_role.reservation_expiry_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateVolume",
          "ec2:DeleteVolume",
          "ec2:DescribeVolumes",
          "ec2:CreateSnapshot",
          "ec2:DeleteSnapshot",
          "ec2:DescribeSnapshots",
          "ec2:CreateTags",
          "ec2:DescribeInstances",
          "ec2:DescribeAvailabilityZones"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM policy for Lambda (needed to trigger availability updater)
resource "aws_iam_role_policy" "reservation_expiry_lambda" {
  name = "lambda-access"
  role = aws_iam_role.reservation_expiry_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "lambda:InvokeFunction"
        ]
        Resource = "*"  # Can be restricted to specific Lambda ARN if needed
      }
    ]
  })
}

# IAM policy for S3 (needed for disk content backups)
resource "aws_iam_role_policy" "reservation_expiry_s3" {
  name = "s3-access"
  role = aws_iam_role.reservation_expiry_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:ListBucket"
        ]
        Resource = [
          "${aws_s3_bucket.disk_contents.arn}",
          "${aws_s3_bucket.disk_contents.arn}/*"
        ]
      }
    ]
  })
}

# ============================================================================
# Kubernetes Resources
# ============================================================================

# ServiceAccount for reservation expiry with IRSA annotation
resource "kubernetes_service_account" "reservation_expiry_sa" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "reservation-expiry-sa"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.reservation_expiry_role.arn
    }
    labels = {
      app = "reservation-expiry"
    }
  }
}

# ClusterRole for reservation expiry - needs to manage pods, nodes, services across all namespaces
resource "kubernetes_cluster_role" "reservation_expiry" {
  metadata {
    name = "reservation-expiry-role"
  }

  # Node access - for checking GPU availability and node status
  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list", "watch"]
  }

  # Pod access - for managing and monitoring reservation pods
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "pods/status", "pods/exec"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  # Service access - for deleting NodePort services for SSH access
  rule {
    api_groups = [""]
    resources  = ["services"]
    verbs      = ["get", "list", "watch", "delete"]
  }

  # PersistentVolumeClaim access - for managing EBS volumes
  rule {
    api_groups = [""]
    resources  = ["persistentvolumeclaims"]
    verbs      = ["get", "list", "watch", "delete"]
  }

  # Event access - for monitoring pod events
  rule {
    api_groups = [""]
    resources  = ["events"]
    verbs      = ["get", "list", "watch"]
  }
}

# ClusterRoleBinding for reservation expiry
resource "kubernetes_cluster_role_binding" "reservation_expiry" {
  metadata {
    name = "reservation-expiry-binding"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.reservation_expiry.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.reservation_expiry_sa.metadata[0].name
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }
}

# ConfigMap for reservation expiry configuration
resource "kubernetes_config_map" "reservation_expiry_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "reservation-expiry-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "reservation-expiry"
    }
  }

  data = {
    # AWS Configuration
    AWS_REGION           = local.current_config.aws_region
    REGION               = local.current_config.aws_region
    EKS_CLUSTER_NAME     = aws_eks_cluster.gpu_dev_cluster.name
    
    # Expiry Configuration
    WARNING_MINUTES       = "30"
    GRACE_PERIOD_SECONDS  = "120"
    
    # Optional: Lambda availability updater function name (if not migrated yet)
    # AVAILABILITY_UPDATER_FUNCTION_NAME = "availability-updater-function"
  }
}

# CronJob for reservation expiry (runs every 5 minutes)
resource "kubernetes_cron_job_v1" "reservation_expiry" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_stateful_set.postgres_primary,
    kubernetes_service.postgres_primary,
    kubernetes_job.database_schema_migration,  # Wait for schema
    kubernetes_deployment.api_service,         # Wait for API service to be ready
    null_resource.reservation_expiry_build,
  ]

  metadata {
    name      = "reservation-expiry"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "reservation-expiry"
    }
  }

  spec {
    schedule                      = "*/5 * * * *"  # Every 5 minutes
    concurrency_policy            = "Forbid"       # No overlapping runs
    successful_jobs_history_limit = 3
    failed_jobs_history_limit     = 3
    
    job_template {
      metadata {
        labels = {
          app = "reservation-expiry"
        }
        annotations = {
          # Force job replacement when code changes
          "reservation-expiry/content-hash" = local.reservation_expiry_hash
        }
      }
      
      spec {
        # ⏱️ CRITICAL: 10-minute timeout
        active_deadline_seconds = 600
        
        template {
          metadata {
            labels = {
              app = "reservation-expiry"
            }
          }
          
          spec {
            service_account_name = kubernetes_service_account.reservation_expiry_sa.metadata[0].name
            restart_policy       = "OnFailure"  # NOT Always!

            # Prefer running on CPU management nodes
            node_selector = {
              NodeType = "cpu"
            }

            # Tolerate CPU-only node taint
            toleration {
              key      = "node-role"
              operator = "Equal"
              value    = "cpu-only"
              effect   = "NoSchedule"
            }

            container {
              name              = "expiry"
              image             = local.reservation_expiry_latest_uri
              image_pull_policy = "Always"

              # Environment variables from ConfigMap
              env_from {
                config_map_ref {
                  name = kubernetes_config_map.reservation_expiry_config.metadata[0].name
                }
              }

              # Database connection parameters
              env {
                name  = "POSTGRES_HOST"
                value = "postgres-primary.${kubernetes_namespace.controlplane.metadata[0].name}.svc.cluster.local"
              }

              env {
                name  = "POSTGRES_PORT"
                value = "5432"
              }

              env {
                name  = "POSTGRES_USER"
                value = "gpudev"
              }

              env {
                name  = "POSTGRES_DB"
                value = "gpudev"
              }

              env {
                name = "POSTGRES_PASSWORD"
                value_from {
                  secret_key_ref {
                    name = kubernetes_secret.postgres_credentials.metadata[0].name
                    key  = "POSTGRES_PASSWORD"
                  }
                }
              }

              resources {
                requests = {
                  cpu    = "500m"
                  memory = "1Gi"
                }
                limits = {
                  cpu    = "2000m"
                  memory = "4Gi"
                }
              }
            }
          }
        }
      }
    }
  }
}

# ============================================================================
# Outputs
# ============================================================================

output "reservation_expiry_status" {
  description = "Reservation expiry CronJob status"
  value = {
    image      = local.reservation_expiry_latest_uri
    namespace  = kubernetes_namespace.controlplane.metadata[0].name
    cronjob    = "reservation-expiry"
    schedule   = "*/5 * * * *"
  }
}

