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
  depends_on = [
    kubernetes_deployment.registry_native,
    kubernetes_service.registry_native,
  ]

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
kubectl port-forward --address 0.0.0.0 -n gpu-controlplane svc/registry-native $REGISTRY_PORT:5000 > /tmp/reservation-processor-port-forward.log 2>&1 &
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

      # Build and push (using host.docker.internal for Docker Desktop compatibility)
      echo ""
      echo "Building Docker image..."
      cd ${path.module}
      docker build --platform=$PLATFORM \
        -f reservation-processor-service/Dockerfile \
        -t host.docker.internal:$REGISTRY_PORT/reservation-processor:${local.reservation_processor_image_tag} \
        .
      docker tag host.docker.internal:$REGISTRY_PORT/reservation-processor:${local.reservation_processor_image_tag} host.docker.internal:$REGISTRY_PORT/reservation-processor:latest

      echo "Pushing to registry..."
      docker push host.docker.internal:$REGISTRY_PORT/reservation-processor:${local.reservation_processor_image_tag}
      docker push host.docker.internal:$REGISTRY_PORT/reservation-processor:latest

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

# IAM policy for S3 (needed for disk content backups)
resource "aws_iam_role_policy" "reservation_processor_s3" {
  name = "s3-access"
  role = aws_iam_role.reservation_processor_role.id

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
# Outputs
# ============================================================================

output "reservation_processor_status" {
  description = "Reservation processor deployment status"
  value = {
    image      = local.reservation_processor_runtime_latest_uri
    namespace  = "gpu-controlplane"
    deployment = "reservation-processor"
  }
}

