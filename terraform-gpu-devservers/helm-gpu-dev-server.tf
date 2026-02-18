# ============================================================================
# Helm Release for GPU Dev Server Chart
# ============================================================================
# Deploys all application-level K8s resources via the gpu-dev-server Helm chart.
# TF continues to manage AWS resources (VPC, EKS, IAM, ECR, S3, etc.)
# and deploys this chart with cloud-specific values injected from TF state.
#
# After verifying the chart deployment, the individual kubernetes_* resources
# in kubernetes.tf, api-service.tf, reservation-*-service.tf, and
# availability-updater-service.tf should be removed (Phase 5/6 cutover).
# ============================================================================

resource "helm_release" "gpu_dev_server" {
  name             = "gpu-dev-server"
  chart            = "${path.module}/../charts/gpu-dev-server"
  namespace        = "gpu-controlplane"
  create_namespace = false  # Namespace managed by kubernetes_namespace.controlplane in kubernetes.tf
  timeout          = 900  # 15 minutes
  wait             = true
  wait_for_jobs    = true

  values = [
    file("${path.module}/../charts/gpu-dev-server/values.yaml"),
    file("${path.module}/../charts/gpu-dev-server/values-aws.yaml"),
    # Pass values with commas via yamlencode to avoid Helm set key parsing issues
    yamlencode({
      cloudProvider = {
        name   = "aws"
        region = local.current_config.aws_region
        aws = {
          eksClusterName          = aws_eks_cluster.gpu_dev_cluster.name
          primaryAvailabilityZone = aws_subnet.gpu_dev_subnet.availability_zone
          efsSecurityGroupId      = aws_security_group.efs_sg.id
          efsSubnetIds = join(",", compact([
            aws_subnet.gpu_dev_subnet.id,
            aws_subnet.gpu_dev_subnet_secondary.id,
            try(aws_subnet.gpu_dev_subnet_tertiary[0].id, "")
          ]))
          ccacheSharedEfsId  = aws_efs_file_system.ccache_shared.id
          ecrRepositoryUrl   = aws_ecr_repository.gpu_dev_custom_images.repository_url
        }
      }
    }),
  ]

  # Namespaces and StorageClass managed by TF (needed for IAM IRSA references / monitoring)
  set {
    name  = "namespaces.create"
    value = "false"
  }

  set {
    name  = "storage.createClass"
    value = "false"
  }

  # NVIDIA device plugin managed by GPU operator (helm_release.nvidia_gpu_operator)
  set {
    name  = "nvidia.devicePlugin.enabled"
    value = "false"
  }

  # PostgreSQL
  set_sensitive {
    name  = "postgres.auth.password"
    value = random_password.postgres_password.result
  }

  set_sensitive {
    name  = "postgres.auth.replicationPassword"
    value = random_password.postgres_password.result  # Reuse same password for simplicity
  }

  # GHCR Registry
  set {
    name  = "registry.ghcr.auth.username"
    value = var.ghcr_username
  }

  set_sensitive {
    name  = "registry.ghcr.auth.token"
    value = var.ghcr_token
  }

  # API Service
  set {
    name  = "apiService.image.repository"
    value = "${local.registry_native_dns}/api-service"
  }

  set {
    name  = "apiService.image.tag"
    value = "latest"
  }

  set {
    name  = "apiService.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.api_service_role.arn
  }

  set {
    name  = "apiService.config.diskContentsBucket"
    value = aws_s3_bucket.disk_contents.bucket
  }

  # Reservation Processor
  set {
    name  = "reservationProcessor.image.repository"
    value = "${local.registry_native_dns}/reservation-processor"
  }

  set {
    name  = "reservationProcessor.image.tag"
    value = "latest"
  }

  set {
    name  = "reservationProcessor.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.reservation_processor_role.arn
  }

  set {
    name  = "reservationProcessor.config.gpuDevContainerImage"
    value = local.runtime_latest_image_uri
  }

  set {
    name  = "reservationProcessor.config.diskContentsBucket"
    value = aws_s3_bucket.disk_contents.bucket
  }

  # Availability Updater
  set {
    name  = "availabilityUpdater.image.repository"
    value = "${local.registry_native_dns}/availability-updater"
  }

  set {
    name  = "availabilityUpdater.image.tag"
    value = "latest"
  }

  set {
    name  = "availabilityUpdater.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.availability_updater_role.arn
  }

  # Reservation Expiry
  set {
    name  = "reservationExpiry.image.repository"
    value = "${local.registry_native_dns}/reservation-expiry"
  }

  set {
    name  = "reservationExpiry.image.tag"
    value = "latest"
  }

  set {
    name  = "reservationExpiry.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.reservation_expiry_role.arn
  }

  set {
    name  = "reservationExpiry.config.diskContentsBucket"
    value = aws_s3_bucket.disk_contents.bucket
  }

  # Native Registry TLS certs (from .certs/ directory)
  set_sensitive {
    name  = "registry.native.tls.cert"
    value = file("${path.module}/.certs/registry.crt")
  }

  set_sensitive {
    name  = "registry.native.tls.key"
    value = file("${path.module}/.certs/registry.key")
  }

  # API Service type: use NodePort when ALB is available, LoadBalancer otherwise
  set {
    name  = "apiService.service.type"
    value = local.effective_domain_name != "" ? "NodePort" : "LoadBalancer"
  }

  set {
    name  = "apiService.service.nodePort"
    value = "30080"
  }

  # BuildKit service account (IRSA for ECR access)
  set {
    name  = "buildkit.serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.buildkit_job_role.arn
  }

  # Image Pre-puller (pre-pulls GPU base image on all GPU nodes)
  set {
    name  = "imagePrepuller.enabled"
    value = "true"
  }

  set {
    name  = "imagePrepuller.image"
    value = local.runtime_latest_image_uri
  }

  # Registries managed by TF directly (not Helm chart)
  set {
    name  = "registry.ghcr.enabled"
    value = "false"
  }

  set {
    name  = "registry.dockerhub.enabled"
    value = "false"
  }

  set {
    name  = "registry.native.enabled"
    value = "false"
  }

  depends_on = [
    kubernetes_namespace.controlplane,       # Namespace must exist (TF-managed)
    kubernetes_config_map.aws_auth,          # Must exist before any pods can run
    helm_release.nvidia_gpu_operator,        # GPU operator for device plugin
    null_resource.api_service_build,         # Images must be built first
    null_resource.reservation_processor_build,
    null_resource.reservation_expiry_build,
    null_resource.availability_updater_build,
  ]
}
