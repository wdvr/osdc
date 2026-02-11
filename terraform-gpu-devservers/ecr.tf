# ECR Repository for custom GPU dev server image
resource "aws_ecr_repository" "gpu_dev_image" {
  name         = "${var.prefix}-gpu-dev-image"
  force_delete = true

  image_tag_mutability = "MUTABLE"

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name        = "${var.prefix}-gpu-dev-image"
    Environment = local.current_config.environment
  }
}

# ECR Repository Policy to allow EKS nodes to pull
resource "aws_ecr_repository_policy" "gpu_dev_image_policy" {
  repository = aws_ecr_repository.gpu_dev_image.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowEKSNodesPull"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.eks_node_role.arn
        }
        Action = [
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:BatchCheckLayerAvailability"
        ]
      }
    ]
  })
}

# ECR Lifecycle Policy to clean up old images
resource "aws_ecr_lifecycle_policy" "gpu_dev_image_lifecycle" {
  repository = aws_ecr_repository.gpu_dev_image.name

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

# Local to determine if we need to build and push
locals {
  # Get all files in docker directory and create a hash
  docker_files = fileset("${path.module}/docker", "**/*")
  # Create hash from all file contents
  docker_context_hash = md5(join("", [
    for file in local.docker_files : filemd5("${path.module}/docker/${file}")
  ]))

  image_tag          = "latest-${substr(local.docker_context_hash, 0, 8)}"
  # Use localhost:5000 for build (via port-forward), registry-native DNS for runtime
  full_image_uri     = "localhost:5000/gpu-dev-base:${local.image_tag}"
  latest_image_uri   = "localhost:5000/gpu-dev-base:latest"
  # Runtime image URIs for Kubernetes (internal cluster DNS)
  runtime_image_uri        = "${local.registry_native_dns}/gpu-dev-base:${local.image_tag}"
  runtime_latest_image_uri = "${local.registry_native_dns}/gpu-dev-base:latest"
}

# Docker build and push using null_resource with proper architecture handling
resource "null_resource" "docker_build_and_push" {
  depends_on = [
    kubernetes_deployment.registry_native,
    kubernetes_service.registry_native,
  ]

  # Trigger rebuild when Docker context changes
  triggers = {
    docker_context_hash = local.docker_context_hash
    registry           = local.registry_native_dns
  }

  # Local provisioner to build and push Docker image
  provisioner "local-exec" {
    command = <<-EOF
      set -e

      echo "==================================================================="
      echo "Building GPU Dev Base Image"
      echo "==================================================================="

      # Get current architecture
      ARCH=$(uname -m)
      echo "Detected architecture: $ARCH"

      # Set platform for Docker build
      if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
        PLATFORM="linux/amd64"
        echo "Building for linux/amd64 platform (cross-compilation from $ARCH)"
      else
        PLATFORM="linux/amd64" 
        echo "Building for linux/amd64 platform"
      fi

      # Setup port-forward to registry on unique port
      REGISTRY_PORT=5005
      echo ""
      echo "Setting up port-forward to registry on port $REGISTRY_PORT..."
      
      # Kill any existing port-forward on this port
      lsof -ti:$REGISTRY_PORT | xargs kill -9 2>/dev/null || true
      sleep 1
      
# Start kubectl port-forward in background (force IPv4 with 127.0.0.1)
kubectl port-forward --address 0.0.0.0 -n gpu-controlplane svc/registry-native $REGISTRY_PORT:5000 > /tmp/gpu-dev-base-port-forward.log 2>&1 &
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
      cd ${path.module}/docker
      docker build --platform=$PLATFORM -t host.docker.internal:$REGISTRY_PORT/gpu-dev-base:${local.image_tag} .
      docker tag host.docker.internal:$REGISTRY_PORT/gpu-dev-base:${local.image_tag} host.docker.internal:$REGISTRY_PORT/gpu-dev-base:latest

      echo "Pushing to registry..."
      docker push host.docker.internal:$REGISTRY_PORT/gpu-dev-base:${local.image_tag}
      docker push host.docker.internal:$REGISTRY_PORT/gpu-dev-base:latest

      # Cleanup port-forward
      echo ""
      echo "Cleaning up port-forward..."
      kill $PORT_FORWARD_PID 2>/dev/null || true
      
      echo ""
      echo "✓ GPU dev base image successfully built and pushed!"
      echo "  Build port: $REGISTRY_PORT"
      echo "  Runtime URI: ${local.runtime_latest_image_uri}"
      echo "==================================================================="
    EOF

    working_dir = path.module
  }
}

# Trigger DaemonSet rollout to pull new image on all nodes after Docker rebuild
resource "null_resource" "rollout_image_prepuller" {
  # Trigger whenever Docker image is rebuilt
  triggers = {
    docker_build_id = null_resource.docker_build_and_push.id
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e
      echo "Triggering DaemonSet rollout to pull new image on all GPU nodes..."
      kubectl rollout restart daemonset gpu-dev-image-prepuller -n kube-system || echo "DaemonSet rollout failed (might not exist yet)"
    EOF
  }

  depends_on = [
    null_resource.docker_build_and_push
  ]
}

# Output the image URI for use in other resources
output "gpu_dev_image_uri" {
  value       = local.runtime_latest_image_uri
  description = "URI of the custom GPU dev server Docker image (runtime)"
  depends_on  = [null_resource.docker_build_and_push]
}