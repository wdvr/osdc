# API Service for GPU Dev - Kubernetes Deployment
# Provides REST API for job submission using PGMQ with AWS IAM auth

# ============================================================================
# ECR Repository for API Service
# ============================================================================

resource "aws_ecr_repository" "api_service" {
  name                 = "${var.prefix}-api-service"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.prefix}-api-service"
    Environment = local.current_config.environment
  }
}

resource "aws_ecr_lifecycle_policy" "api_service" {
  repository = aws_ecr_repository.api_service.name

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
# Build and Push API Service Docker Image
# ============================================================================

locals {
  # Hash API service files to detect changes (matches project pattern)
  api_service_files = fileset("${path.module}/api-service", "**/*.py")
  api_service_hash = md5(join("", concat(
    [for file in local.api_service_files : filemd5("${path.module}/api-service/${file}")],
    [filemd5("${path.module}/api-service/Dockerfile")],
    [filemd5("${path.module}/api-service/requirements.txt")]
  )))

  api_service_image_tag  = "v1-${substr(local.api_service_hash, 0, 8)}"
  # Use localhost:5000 for build (via port-forward), registry-native DNS for runtime
  api_service_image_uri         = "localhost:5000/api-service:${local.api_service_image_tag}"
  api_service_latest_uri        = "localhost:5000/api-service:latest"
  # Runtime image URIs for Kubernetes (internal cluster DNS)
  api_service_runtime_uri        = "${local.registry_native_dns}/api-service:${local.api_service_image_tag}"
  api_service_runtime_latest_uri = "${local.registry_native_dns}/api-service:latest"
}

resource "null_resource" "api_service_build" {
  depends_on = [
    kubernetes_deployment.registry_native,
    kubernetes_service.registry_native,
  ]

  triggers = {
    api_service_hash = local.api_service_hash
    registry         = local.registry_native_dns
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e

      echo "==================================================================="
      echo "Building API Service"
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
      REGISTRY_PORT=5001
      echo ""
      echo "Setting up port-forward to registry on port $REGISTRY_PORT..."
      
      # Kill any existing port-forward on this port
      lsof -ti:$REGISTRY_PORT | xargs kill -9 2>/dev/null || true
      sleep 1
      
      # Start kubectl port-forward in background (force IPv4 with 127.0.0.1)
      kubectl port-forward --address 0.0.0.0 -n gpu-controlplane svc/registry-native $REGISTRY_PORT:5000 > /tmp/api-service-port-forward.log 2>&1 &
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
      cd ${path.module}/api-service
      docker build --platform=$PLATFORM -t host.docker.internal:$REGISTRY_PORT/api-service:${local.api_service_image_tag} .
      docker tag host.docker.internal:$REGISTRY_PORT/api-service:${local.api_service_image_tag} host.docker.internal:$REGISTRY_PORT/api-service:latest

      echo "Pushing to registry..."
      docker push host.docker.internal:$REGISTRY_PORT/api-service:${local.api_service_image_tag}
      docker push host.docker.internal:$REGISTRY_PORT/api-service:latest

      # Cleanup port-forward
      echo ""
      echo "Cleaning up port-forward..."
      kill $PORT_FORWARD_PID 2>/dev/null || true
      
      echo ""
      echo "✓ API service image successfully built and pushed!"
      echo "  Build port: $REGISTRY_PORT"
      echo "  Runtime URI: ${local.api_service_runtime_uri}"
      echo "==================================================================="
    EOF

    working_dir = path.module
  }
}

# ============================================================================
# IAM Role for API Service (IRSA - IAM Roles for Service Accounts)
# ============================================================================

# IAM role for API service to call AWS STS
resource "aws_iam_role" "api_service_role" {
  name = "${var.prefix}-api-service-role"

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
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:${kubernetes_namespace.controlplane.metadata[0].name}:api-service-sa"
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "${var.prefix}-api-service-role"
    Environment = local.current_config.environment
  }
}

# IAM policy to allow STS GetCallerIdentity
resource "aws_iam_role_policy" "api_service_sts" {
  name = "sts-get-caller-identity"
  role = aws_iam_role.api_service_role.id

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

# IAM policy for S3 (needed for reading disk content backups)
resource "aws_iam_role_policy" "api_service_s3" {
  name = "s3-read-access"
  role = aws_iam_role.api_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
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
# API Service public access
# ============================================================================
# The API service's public Service (NodePort or LoadBalancer) is now managed
# by the Helm chart (templates/api-service/service.yaml).
# HTTPS access is provided by the ALB at api.<domain> (see alb.tf).
#
# Migration: tofu state rm kubernetes_service.api_service_public

output "api_service_https_ready" {
  description = "Whether HTTPS is configured via ALB"
  value       = local.effective_domain_name != ""
}

