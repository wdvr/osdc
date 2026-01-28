# Reservation Processor Service - Kubernetes CronJob
# Replaces Lambda function - polls PGMQ and processes reservation requests

# ============================================================================
# ECR Repository for Reservation Processor Service
# ============================================================================

resource "aws_ecr_repository" "reservation_processor_service" {
  name                 = "${var.prefix}-reservation-processor"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.prefix}-reservation-processor"
    Environment = local.current_config.environment
  }
}

resource "aws_ecr_lifecycle_policy" "reservation_processor_service" {
  repository = aws_ecr_repository.reservation_processor_service.name

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
# Build and Push Reservation Processor Docker Image
# ============================================================================

locals {
  # Hash reservation processor files to detect changes (including shared utilities)
  reservation_processor_files = fileset("${path.module}/reservation-processor-service", "**/*.py")
  shared_files = fileset("${path.module}/shared", "**/*.py")
  
  reservation_processor_hash = md5(join("", concat(
    [for file in local.reservation_processor_files : filemd5("${path.module}/reservation-processor-service/${file}")],
    [for file in local.shared_files : filemd5("${path.module}/shared/${file}")],
    [filemd5("${path.module}/reservation-processor-service/Dockerfile")],
    [filemd5("${path.module}/reservation-processor-service/requirements.txt")]
  )))

  reservation_processor_image_tag  = "v1-${substr(local.reservation_processor_hash, 0, 8)}"
  # Use localhost:5000 for build (via port-forward), registry-native DNS for runtime
  reservation_processor_image_uri         = "localhost:5000/reservation-processor:${local.reservation_processor_image_tag}"
  reservation_processor_latest_uri        = "localhost:5000/reservation-processor:latest"
  # Runtime image URIs for Kubernetes (internal cluster DNS)
  reservation_processor_runtime_uri        = "${local.registry_native_dns}/reservation-processor:${local.reservation_processor_image_tag}"
  reservation_processor_runtime_latest_uri = "${local.registry_native_dns}/reservation-processor:latest"
}

resource "null_resource" "reservation_processor_build" {
  triggers = {
    processor_hash = local.reservation_processor_hash
    registry       = local.registry_native_dns
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e

      echo "==================================================================="
      echo "Building Reservation Processor Service"
      echo "==================================================================="

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

      # Setup port-forward to registry on unique port
      REGISTRY_PORT=5002
      echo ""
      echo "Setting up port-forward to registry on port $REGISTRY_PORT..."
      
      # Kill any existing port-forward on this port
      lsof -ti:$REGISTRY_PORT | xargs kill -9 2>/dev/null || true
      sleep 1
      
# Start kubectl port-forward in background (force IPv4 with 127.0.0.1)
kubectl port-forward --address 127.0.0.1 -n gpu-controlplane svc/registry-native $REGISTRY_PORT:5000 > /tmp/reservation-processor-port-forward.log 2>&1 &
      PORT_FORWARD_PID=$!
      echo "Started port-forward (PID: $PORT_FORWARD_PID)"
      
      # Wait for port-forward to be ready
      echo "Waiting for registry to be accessible..."
      for i in {1..30}; do
        if curl -sf --max-time 2 http://127.0.0.1:$REGISTRY_PORT/v2/ > /dev/null 2>&1; then
          echo "✓ Registry is accessible at 127.0.0.1:$REGISTRY_PORT"
          break
        fi
        if [ $i -eq 30 ]; then
          echo "ERROR: Registry not accessible after 30 seconds"
          kill $PORT_FORWARD_PID 2>/dev/null || true
          exit 1
        fi
        sleep 1
      done

      # Build and push (using 127.0.0.1:$REGISTRY_PORT for IPv4)
      echo ""
      echo "Building Docker image..."
      cd ${path.module}
      docker build --platform=$PLATFORM \
        -f reservation-processor-service/Dockerfile \
        -t 127.0.0.1:$REGISTRY_PORT/reservation-processor:${local.reservation_processor_image_tag} \
        .
      docker tag 127.0.0.1:$REGISTRY_PORT/reservation-processor:${local.reservation_processor_image_tag} 127.0.0.1:$REGISTRY_PORT/reservation-processor:latest

      echo "Pushing to registry..."
      docker push 127.0.0.1:$REGISTRY_PORT/reservation-processor:${local.reservation_processor_image_tag}
      docker push 127.0.0.1:$REGISTRY_PORT/reservation-processor:latest

      # Cleanup port-forward
      echo ""
      echo "Cleaning up port-forward..."
      kill $PORT_FORWARD_PID 2>/dev/null || true
      
      echo ""
      echo "✓ Reservation processor image successfully built and pushed!"
      echo "  Build port: $REGISTRY_PORT"
      echo "  Runtime URI: ${local.reservation_processor_runtime_uri}"
      echo "==================================================================="
    EOF

    working_dir = path.module
  }

  depends_on = [
    kubernetes_deployment.registry_native,
    kubernetes_service.registry_native
  ]
}

# ============================================================================
# IAM Role for Reservation Processor Service (IRSA)
# ============================================================================

# IAM role for reservation processor service to access AWS resources
resource "aws_iam_role" "reservation_processor_role" {
  name = "${var.prefix}-reservation-processor-role"

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
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:${kubernetes_namespace.controlplane.metadata[0].name}:reservation-processor-sa"
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "${var.prefix}-reservation-processor-role"
    Environment = local.current_config.environment
  }
}

# IAM policy for STS (needed for Kubernetes client setup)
resource "aws_iam_role_policy" "reservation_processor_sts" {
  name = "sts-access"
  role = aws_iam_role.reservation_processor_role.id

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
resource "aws_iam_role_policy" "reservation_processor_eks" {
  name = "eks-access"
  role = aws_iam_role.reservation_processor_role.id

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
resource "aws_iam_role_policy" "reservation_processor_ec2" {
  name = "ec2-access"
  role = aws_iam_role.reservation_processor_role.id

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

# IAM policy for ECR (needed for Docker builds)
resource "aws_iam_role_policy" "reservation_processor_ecr" {
  name = "ecr-access"
  role = aws_iam_role.reservation_processor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
          "ecr:BatchCheckLayerAvailability",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages"
        ]
        Resource = "*"
      }
    ]
  })
}

# IAM policy for EFS (needed for shared storage management)
resource "aws_iam_role_policy" "reservation_processor_efs" {
  name = "efs-access"
  role = aws_iam_role.reservation_processor_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "elasticfilesystem:DescribeFileSystems",
          "elasticfilesystem:CreateFileSystem",
          "elasticfilesystem:CreateMountTarget",
          "elasticfilesystem:DescribeMountTargets",
          "elasticfilesystem:DescribeTags",
          "elasticfilesystem:CreateTags",
          "elasticfilesystem:TagResource"
        ]
        Resource = "*"
      }
    ]
  })
}

# ============================================================================
# Kubernetes Resources
# ============================================================================

# ServiceAccount for reservation processor with IRSA annotation
resource "kubernetes_service_account" "reservation_processor_sa" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "reservation-processor-sa"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.reservation_processor_role.arn
    }
    labels = {
      app = "reservation-processor"
    }
  }
}

# ClusterRole for reservation processor - needs to manage pods, nodes, services across all namespaces
resource "kubernetes_cluster_role" "reservation_processor" {
  metadata {
    name = "reservation-processor-role"
  }

  # Node access - for checking GPU availability and node status
  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list", "watch"]
  }

  # Pod access - for creating, managing, and monitoring reservation pods
  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "pods/status", "pods/exec"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  # Service access - for creating NodePort services for SSH access
  rule {
    api_groups = [""]
    resources  = ["services"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  # PersistentVolumeClaim access - for managing EBS volumes
  rule {
    api_groups = [""]
    resources  = ["persistentvolumeclaims"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }

  # ConfigMap and Secret access - for pod configurations
  rule {
    api_groups = [""]
    resources  = ["configmaps", "secrets"]
    verbs      = ["get", "list", "watch", "create", "update", "patch"]
  }

  # Event access - for monitoring pod events
  rule {
    api_groups = [""]
    resources  = ["events"]
    verbs      = ["get", "list", "watch"]
  }

  # Job access - for creating and monitoring worker jobs
  rule {
    api_groups = ["batch"]
    resources  = ["jobs", "jobs/status"]
    verbs      = ["get", "list", "watch", "create", "update", "patch", "delete"]
  }
}

# ClusterRoleBinding for reservation processor
resource "kubernetes_cluster_role_binding" "reservation_processor" {
  metadata {
    name = "reservation-processor-binding"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.reservation_processor.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.reservation_processor_sa.metadata[0].name
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }
}

# ConfigMap for reservation processor configuration
resource "kubernetes_config_map" "reservation_processor_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "reservation-processor-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "reservation-processor"
    }
  }

  data = {
    # PGMQ Configuration
    QUEUE_NAME                 = "gpu_reservations"
    POLL_INTERVAL_SECONDS      = "5"
    VISIBILITY_TIMEOUT_SECONDS = "900"  # 15 minutes (Lambda-like timeout)
    BATCH_SIZE                 = "1"
    
    # AWS Configuration
    REGION                     = local.current_config.aws_region
    EKS_CLUSTER_NAME           = aws_eks_cluster.gpu_dev_cluster.name
    PRIMARY_AVAILABILITY_ZONE  = aws_subnet.gpu_dev_subnet.availability_zone
    
    # Reservation Configuration
    MAX_RESERVATION_HOURS      = "168"  # 7 days maximum
    DEFAULT_TIMEOUT_HOURS      = "4"    # Default 4 hours
    
    # Container Configuration
    GPU_DEV_CONTAINER_IMAGE    = "pytorch/pytorch:2.8.0-cuda12.9-cudnn9-devel"
    
    # Optional: EFS Configuration (if using persistent disks)
    EFS_SECURITY_GROUP_ID      = aws_security_group.efs_sg.id
    EFS_SUBNET_IDS             = join(",", compact([
      aws_subnet.gpu_dev_subnet.id,
      aws_subnet.gpu_dev_subnet_secondary.id,
      try(aws_subnet.gpu_dev_subnet_tertiary[0].id, "")
    ]))
    CCACHE_SHARED_EFS_ID       = aws_efs_file_system.ccache_shared.id
    
    # Optional: ECR Configuration (if using custom images)
    ECR_REPOSITORY_URL         = aws_ecr_repository.gpu_dev_custom_images.repository_url
    
    # Version Configuration
    PROCESSOR_VERSION          = "0.4.0"
    MIN_CLI_VERSION            = "0.0.1"  # Temporarily lowered to allow current CLI
  }
}

# Deployment for reservation processor (runs continuously, not a CronJob)
resource "kubernetes_deployment" "reservation_processor" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_stateful_set.postgres_primary,
    kubernetes_service.postgres_primary,
    kubernetes_job.database_schema_migration,  # Wait for schema (includes PGMQ queues)
    kubernetes_deployment.api_service,         # Wait for API service to be ready
    null_resource.reservation_processor_build,
  ]

  # Wait for deployment to be ready before considering it complete
  wait_for_rollout = true
  
  timeouts {
    create = "10m"
    update = "10m"
  }

  metadata {
    name      = "reservation-processor"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "reservation-processor"
    }
  }

  spec {
    replicas = 1  # Single replica for now (can scale later if needed)

    selector {
      match_labels = {
        app = "reservation-processor"
      }
    }

    template {
      metadata {
        labels = {
          app = "reservation-processor"
        }
        annotations = {
          # Force pod replacement when code changes
          "reservation-processor/content-hash" = local.reservation_processor_hash
        }
      }

      spec {
        service_account_name = kubernetes_service_account.reservation_processor_sa.metadata[0].name

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
          name              = "reservation-processor"
          image             = local.reservation_processor_runtime_latest_uri
          image_pull_policy = "Always"

          # Environment variables from ConfigMap
          env_from {
            config_map_ref {
              name = kubernetes_config_map.reservation_processor_config.metadata[0].name
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

          # Job orchestration configuration
          env {
            name  = "WORKER_IMAGE"
            value = local.reservation_processor_latest_uri
          }

          env {
            name  = "KUBE_NAMESPACE"
            value = kubernetes_namespace.controlplane.metadata[0].name
          }

          env {
            name  = "SERVICE_ACCOUNT"
            value = kubernetes_service_account.reservation_processor_sa.metadata[0].name
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

          # Liveness probe - restart if processor hangs
          liveness_probe {
            exec {
              command = ["pgrep", "-f", "python"]
            }
            initial_delay_seconds = 30
            period_seconds        = 60
            timeout_seconds       = 5
            failure_threshold     = 3
          }
        }
      }
    }
  }
}

# ============================================================================
# Outputs
# ============================================================================

output "reservation_processor_status" {
  description = "Reservation processor deployment status"
  value = {
    image      = local.reservation_processor_runtime_latest_uri
    namespace  = kubernetes_namespace.controlplane.metadata[0].name
    deployment = "reservation-processor"
  }
}

