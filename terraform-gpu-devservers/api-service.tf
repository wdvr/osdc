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
  api_service_image_uri  = "${aws_ecr_repository.api_service.repository_url}:${local.api_service_image_tag}"
  api_service_latest_uri = "${aws_ecr_repository.api_service.repository_url}:latest"
}

resource "null_resource" "api_service_build" {
  triggers = {
    api_service_hash = local.api_service_hash
    ecr_repo         = aws_ecr_repository.api_service.repository_url
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e

      echo "Building and pushing API service Docker image..."

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

      # Change to api-service directory
      cd ${path.module}/api-service

      # Login to ECR
      echo "Logging into ECR..."
      aws ecr get-login-password --region ${local.current_config.aws_region} | \
        docker login --username AWS --password-stdin ${aws_ecr_repository.api_service.repository_url}

      # Build image with correct platform
      echo "Building Docker image for platform: $PLATFORM"
      docker build --platform=$PLATFORM -t ${local.api_service_image_uri} .

      # Also tag as latest
      docker tag ${local.api_service_image_uri} ${local.api_service_latest_uri}

      # Push both tags
      echo "Pushing Docker image..."
      docker push ${local.api_service_image_uri}
      docker push ${local.api_service_latest_uri}

      echo "API service image successfully built and pushed!"
      echo "Image URI: ${local.api_service_image_uri}"
    EOF

    working_dir = path.module
  }

  depends_on = [
    aws_ecr_repository.api_service,
    aws_ecr_lifecycle_policy.api_service
  ]
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

# ============================================================================
# Kubernetes Resources
# ============================================================================

# ServiceAccount for API service with IRSA annotation
resource "kubernetes_service_account" "api_service_sa" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "api-service-sa"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    annotations = {
      "eks.amazonaws.com/role-arn" = aws_iam_role.api_service_role.arn
    }
    labels = {
      app = "api-service"
    }
  }
}

# ConfigMap for API service configuration
resource "kubernetes_config_map" "api_service_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "api-service-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "api-service"
    }
  }

  data = {
    QUEUE_NAME           = "gpu_reservations"
    API_KEY_TTL_HOURS    = "2"
    ALLOWED_AWS_ROLE     = "SSOCloudDevGpuReservation"
    AWS_REGION           = local.current_config.aws_region
  }
}

# Deployment for API service
resource "kubernetes_deployment" "api_service" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_stateful_set.postgres_primary,
    kubernetes_service.postgres_primary,
    null_resource.api_service_build,
  ]

  wait_for_rollout = false

  metadata {
    name      = "api-service"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "api-service"
    }
  }

  spec {
    replicas = 2  # At least 2 for high availability

    selector {
      match_labels = {
        app = "api-service"
      }
    }

    template {
      metadata {
        labels = {
          app = "api-service"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.api_service_sa.metadata[0].name

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
          name  = "api-service"
          image = local.api_service_latest_uri
          image_pull_policy = "Always"

          port {
            container_port = 8000
            name           = "http"
          }

          # Environment variables from ConfigMap
          env_from {
            config_map_ref {
              name = kubernetes_config_map.api_service_config.metadata[0].name
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
              cpu    = "250m"
              memory = "512Mi"
            }
            limits = {
              cpu    = "1000m"
              memory = "1Gi"
            }
          }

          liveness_probe {
            http_get {
              path = "/health"
              port = 8000
            }
            initial_delay_seconds = 10
            period_seconds        = 30
            timeout_seconds       = 5
            failure_threshold     = 3
          }

          readiness_probe {
            http_get {
              path = "/health"
              port = 8000
            }
            initial_delay_seconds = 5
            period_seconds        = 10
            timeout_seconds       = 3
            failure_threshold     = 2
          }
        }
      }
    }
  }
}

# ClusterIP Service for API service (internal)
resource "kubernetes_service" "api_service" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "api-service"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "api-service"
    }
  }

  spec {
    type = "ClusterIP"

    selector = {
      app = "api-service"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8000
      protocol    = "TCP"
    }
  }
}

# ============================================================================
# ALB Ingress for Public Access
# ============================================================================

# Public LoadBalancer Service (Classic - Cloud-agnostic)
# Uses standard Kubernetes LoadBalancer (no AWS-specific annotations)
# In EKS, this creates a Classic Load Balancer (CLB) automatically
resource "kubernetes_service" "api_service_public" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_deployment.api_service
  ]

  wait_for_load_balancer = false

  metadata {
    name      = "api-service-public"
    namespace = kubernetes_namespace.controlplane.metadata[0].name

    labels = {
      app = "api-service"
    }
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app = "api-service"
    }

    port {
      name        = "http"
      port        = 80
      target_port = 8000
      protocol    = "TCP"
    }

    # Health checks automatically use the readiness probe
    # defined in the deployment spec
  }
}

# Output the API service URL
output "api_service_url" {
  description = "Public URL for the API service (LoadBalancer DNS)"
  value       = try(
    "http://${kubernetes_service.api_service_public.status[0].load_balancer[0].ingress[0].hostname}",
    "Service not yet provisioned - run 'terraform apply' again or check kubectl get svc -n ${kubernetes_namespace.controlplane.metadata[0].name} api-service-public"
  )
}

output "api_service_https_ready" {
  description = "Whether HTTPS is configured (requires ACM certificate)"
  value       = false  # Set to true after adding SSL certificate annotations
}

