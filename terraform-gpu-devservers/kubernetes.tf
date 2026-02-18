# Kubernetes resources for GPU development pods

# =============================================================================
# Local Variables
# =============================================================================

# Internal registry DNS names (Route53 private hosted zone)
locals {
  registry_ghcr_dns       = "registry-ghcr.internal.${var.prefix}.local:5000"
  registry_dockerhub_dns  = "registry-dockerhub.internal.${var.prefix}.local:5000"
  registry_native_dns     = "registry.internal.${var.prefix}.local:5000"
}

# GPU types that should have one node labeled for Nsight profiling (no DCGM)
locals {
  profiling_gpu_types = {
    default = ["t4"]           # Test: one T4 node for profiling
    prod    = ["h200", "b200"] # Prod: one H200 and one B200 node for profiling
  }
}

# =============================================================================
# Namespaces
# =============================================================================

# Namespace for GPU development pods
resource "kubernetes_namespace" "gpu_dev" {
  depends_on = [aws_eks_cluster.gpu_dev_cluster]

  metadata {
    name = "gpu-dev"
    labels = {
      name    = "gpu-dev"
      purpose = "gpu-development"
    }
  }
}

# Namespace for control plane infrastructure (PostgreSQL, reservation controller, etc.)
resource "kubernetes_namespace" "controlplane" {
  depends_on = [aws_eks_cluster.gpu_dev_cluster]

  metadata {
    name = "gpu-controlplane"
    labels = {
      name    = "gpu-controlplane"
      purpose = "control-plane-infrastructure"
    }
  }
}

# =============================================================================
# AWS Auth ConfigMap
# =============================================================================

# AWS Auth ConfigMap to allow Lambda roles to access EKS
# Use the kubernetes_config_map resource to manage the full ConfigMap
resource "kubernetes_config_map" "aws_auth" {
  depends_on = [
    aws_eks_cluster.gpu_dev_cluster
  ]

  metadata {
    name      = "aws-auth"
    namespace = "kube-system"
  }

  data = {
    mapRoles = yamlencode([
      # EKS Node Group role (required for nodes to join cluster)
      {
        rolearn  = aws_iam_role.eks_node_role.arn
        username = "system:node:{{EC2PrivateDNSName}}"
        groups = [
          "system:bootstrappers",
          "system:nodes"
        ]
      },
      # SSO role for GPU reservation users - maps to gpu-dev-users K8s group
      # Allows kubectl exec / gpu-dev connect access to pods in gpu-dev namespace
      {
        rolearn  = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:role/SSOCloudDevGpuReservation"
        username = "{{SessionName}}"
        groups   = ["gpu-dev-users"]
      },
    ])
  }

  # Ensure this is created after the cluster but before nodes try to join
}

# =============================================================================
# Shared Secrets (used by Helm chart and other resources)
# =============================================================================

# Generate a random password for PostgreSQL
resource "random_password" "postgres_password" {
  length  = 32
  special = false  # Avoid special chars that might cause escaping issues
}

# =============================================================================
# Registry Pull-Through Cache for ghcr.io
# =============================================================================
# Caches images from ghcr.io to avoid authentication issues and improve pull times
# Usage: Instead of ghcr.io/org/image:tag, use:
#        registry-ghcr.internal.pytorch-gpu-dev.local:5000/org/image:tag
# The DNS name is resolved via Route53 private hosted zone → internal NLB → registry pod

# Secret for ghcr.io credentials (GitHub PAT with read:packages scope)
# To create the PAT: GitHub → Settings → Developer settings → Personal access tokens
# Create token with ONLY "read:packages" scope
resource "kubernetes_secret" "registry_ghcr_credentials" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-ghcr-credentials"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  data = {
    # GitHub username (can be any valid GitHub username with the PAT)
    GHCR_USERNAME = var.ghcr_username
    # GitHub PAT with read:packages scope
    GHCR_TOKEN    = var.ghcr_token
  }

  type = "Opaque"
}

# ConfigMap for ghcr.io registry cache configuration (template - credentials injected at runtime)
resource "kubernetes_config_map" "registry_ghcr_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-ghcr-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  data = {
    # Template config - init container will substitute GHCR_USERNAME and GHCR_TOKEN
    "config.yml.tmpl" = <<-EOT
      version: 0.1
      log:
        level: info
        fields:
          service: registry
      storage:
        filesystem:
          rootdirectory: /var/lib/registry
        cache:
          blobdescriptor: inmemory
        delete:
          enabled: true
      http:
        addr: :5000
        headers:
          X-Content-Type-Options: [nosniff]
      proxy:
        remoteurl: https://ghcr.io
        username: GHCR_USERNAME_PLACEHOLDER
        password: GHCR_TOKEN_PLACEHOLDER
    EOT
  }
}

# PersistentVolumeClaim for registry cache storage
resource "kubernetes_persistent_volume_claim" "registry_ghcr_pvc" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_storage_class.gp3,
  ]

  wait_until_bound = false

  metadata {
    name      = "registry-ghcr-data"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.gp3.metadata[0].name

    resources {
      requests = {
        storage = "50Gi"
      }
    }
  }
}

# Deployment for ghcr.io pull-through cache
resource "kubernetes_deployment" "registry_ghcr" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_config_map.registry_ghcr_config,
    kubernetes_secret.registry_ghcr_credentials,
    kubernetes_persistent_volume_claim.registry_ghcr_pvc,
  ]

  metadata {
    name      = "registry-ghcr"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app      = "registry-cache"
        upstream = "ghcr"
      }
    }

    strategy {
      type = "Recreate"  # Required for RWO PVC
    }

    template {
      metadata {
        labels = {
          app      = "registry-cache"
          upstream = "ghcr"
        }
      }

      spec {
        # Set fsGroup so mounted volume is writable by registry container
        security_context {
          fs_group               = 1000
          fs_group_change_policy = "OnRootMismatch"
        }

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

        # Init container to inject credentials into config
        init_container {
          name  = "inject-credentials"
          image = "busybox:1.36"  # Must use direct pull for registry bootstrap

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            # Read credentials from environment and substitute into config template
            sed -e "s/GHCR_USERNAME_PLACEHOLDER/$GHCR_USERNAME/" \
                -e "s/GHCR_TOKEN_PLACEHOLDER/$GHCR_TOKEN/" \
                /config-template/config.yml.tmpl > /etc/docker/registry/config.yml
            echo "Registry config generated with credentials"
          EOT
          ]

          env {
            name = "GHCR_USERNAME"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.registry_ghcr_credentials.metadata[0].name
                key  = "GHCR_USERNAME"
              }
            }
          }

          env {
            name = "GHCR_TOKEN"
            value_from {
              secret_key_ref {
                name = kubernetes_secret.registry_ghcr_credentials.metadata[0].name
                key  = "GHCR_TOKEN"
              }
            }
          }

          volume_mount {
            name       = "config-template"
            mount_path = "/config-template"
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/docker/registry"
          }
        }

        container {
          name  = "registry"
          image = "registry:2"

          port {
            container_port = 5000
            name           = "registry"
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/docker/registry"
          }

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/registry"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }

          liveness_probe {
            http_get {
              path = "/"
              port = 5000
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/"
              port = 5000
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
        }

        volume {
          name = "config-template"
          config_map {
            name = kubernetes_config_map.registry_ghcr_config.metadata[0].name
          }
        }

        volume {
          name = "config"
          empty_dir {}
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.registry_ghcr_pvc.metadata[0].name
          }
        }
      }
    }
  }
}

# Service for ghcr.io pull-through cache
# Uses internal Network Load Balancer so nodes can reach it via VPC DNS
resource "kubernetes_service" "registry_ghcr" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-ghcr"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
    annotations = {
      # Use internal NLB (not internet-facing)
      "service.beta.kubernetes.io/aws-load-balancer-internal" = "true"
      "service.beta.kubernetes.io/aws-load-balancer-type"     = "nlb"
      # Cross-zone load balancing for reliability
      "service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled" = "true"
    }
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app      = "registry-cache"
      upstream = "ghcr"
    }

    port {
      name        = "registry"
      port        = 5000
      target_port = 5000
    }
  }
}

# =============================================================================
# Registry Pull-Through Cache for Docker Hub
# =============================================================================
# Caches images from docker.io to improve pull times and avoid rate limits
# Usage: Instead of busybox:1.36, use:
#        registry-dockerhub.internal.pytorch-gpu-dev.local:5000/library/busybox:1.36
# The DNS name is resolved via Route53 private hosted zone → internal NLB → registry pod

# ConfigMap for Docker Hub registry cache configuration
# Note: Docker Hub pull-through cache doesn't require authentication for public images
resource "kubernetes_config_map" "registry_dockerhub_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-dockerhub-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  data = {
    "config.yml" = <<-EOT
      version: 0.1
      log:
        level: info
        fields:
          service: registry
      storage:
        filesystem:
          rootdirectory: /var/lib/registry
        cache:
          blobdescriptor: inmemory
        delete:
          enabled: true
      http:
        addr: :5000
        headers:
          X-Content-Type-Options: [nosniff]
      proxy:
        remoteurl: https://registry-1.docker.io
    EOT
  }
}

# PersistentVolumeClaim for Docker Hub registry cache storage
resource "kubernetes_persistent_volume_claim" "registry_dockerhub_pvc" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_storage_class.gp3,
  ]

  wait_until_bound = false

  metadata {
    name      = "registry-dockerhub-data"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.gp3.metadata[0].name

    resources {
      requests = {
        storage = "50Gi"
      }
    }
  }
}

# Deployment for Docker Hub pull-through cache
resource "kubernetes_deployment" "registry_dockerhub" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_config_map.registry_dockerhub_config,
    kubernetes_persistent_volume_claim.registry_dockerhub_pvc,
  ]

  metadata {
    name      = "registry-dockerhub"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app      = "registry-cache"
        upstream = "dockerhub"
      }
    }

    strategy {
      type = "Recreate"  # Required for RWO PVC
    }

    template {
      metadata {
        labels = {
          app      = "registry-cache"
          upstream = "dockerhub"
        }
      }

      spec {
        # Set fsGroup so mounted volume is writable by registry container
        security_context {
          fs_group               = 1000
          fs_group_change_policy = "OnRootMismatch"
        }

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
          name  = "registry"
          image = "registry:2"

          port {
            container_port = 5000
            name           = "registry"
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/docker/registry"
          }

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/registry"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "128Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "512Mi"
            }
          }

          liveness_probe {
            http_get {
              path = "/"
              port = 5000
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path = "/"
              port = 5000
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.registry_dockerhub_config.metadata[0].name
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.registry_dockerhub_pvc.metadata[0].name
          }
        }
      }
    }
  }
}

# Service for Docker Hub pull-through cache
# Uses internal Network Load Balancer so nodes can reach it via VPC DNS
resource "kubernetes_service" "registry_dockerhub" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-dockerhub"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-cache"
    }
    annotations = {
      # Use internal NLB (not internet-facing)
      "service.beta.kubernetes.io/aws-load-balancer-internal" = "true"
      "service.beta.kubernetes.io/aws-load-balancer-type"     = "nlb"
      # Cross-zone load balancing for reliability
      "service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled" = "true"
    }
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app      = "registry-cache"
      upstream = "dockerhub"
    }

    port {
      name        = "registry"
      port        = 5000
      target_port = 5000
    }
  }
}

# =============================================================================
# Native In-Cluster Registry (for internal images)
# =============================================================================
# This registry hosts all internal service images (built by Terraform)
# Unlike pull-through caches, this is a true registry that stores images
# Used for: api-service, reservation-processor, ssh-proxy, etc.

# TLS secret for registry-native (self-signed certificate)
resource "kubernetes_secret" "registry_native_tls" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-native-tls"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-native"
    }
  }

  type = "kubernetes.io/tls"

  data = {
    "tls.crt" = file("${path.module}/.certs/registry.crt")
    "tls.key" = file("${path.module}/.certs/registry.key")
  }
}

# ConfigMap for native registry configuration
resource "kubernetes_config_map" "registry_native_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-native-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-native"
    }
  }

  data = {
    "config.yml" = <<-EOT
      version: 0.1
      log:
        level: info
        fields:
          service: registry
      storage:
        filesystem:
          rootdirectory: /var/lib/registry
        cache:
          blobdescriptor: inmemory
        delete:
          enabled: true
      http:
        addr: :5000
        headers:
          X-Content-Type-Options: [nosniff]
      # No proxy configuration - this is a native registry for storing images
    EOT
  }
}

# PersistentVolumeClaim for native registry storage
resource "kubernetes_persistent_volume_claim" "registry_native_pvc" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_storage_class.gp3,
  ]

  wait_until_bound = false

  metadata {
    name      = "registry-native-data"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-native"
    }
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.gp3.metadata[0].name

    resources {
      requests = {
        storage = "100Gi"  # Larger for storing all service images
      }
    }
  }
}

# Deployment for native registry
resource "kubernetes_deployment" "registry_native" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_config_map.registry_native_config,
    kubernetes_persistent_volume_claim.registry_native_pvc,
  ]

  metadata {
    name      = "registry-native"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-native"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "registry-native"
      }
    }

    strategy {
      type = "Recreate"  # Required for RWO PVC
    }

    template {
      metadata {
        labels = {
          app = "registry-native"
        }
      }

      spec {
        # Set fsGroup so mounted volume is writable by registry container
        security_context {
          fs_group               = 1000
          fs_group_change_policy = "OnRootMismatch"
        }

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
          name  = "registry"
          image = "registry:2"

          port {
            container_port = 5000
            name           = "registry"
          }

          volume_mount {
            name       = "config"
            mount_path = "/etc/docker/registry"
            read_only  = true
          }

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/registry"
          }

          resources {
            requests = {
              cpu    = "200m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "1000m"
              memory = "1Gi"
            }
          }

          liveness_probe {
            http_get {
              path   = "/"
              port   = 5000
              scheme = "HTTP"
            }
            initial_delay_seconds = 10
            period_seconds        = 10
          }

          readiness_probe {
            http_get {
              path   = "/"
              port   = 5000
              scheme = "HTTP"
            }
            initial_delay_seconds = 5
            period_seconds        = 5
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.registry_native_config.metadata[0].name
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.registry_native_pvc.metadata[0].name
          }
        }
      }
    }
  }
}

# Service for native registry
# Uses internal Network Load Balancer so nodes can reach it via VPC DNS
resource "kubernetes_service" "registry_native" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "registry-native"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "registry-native"
    }
    annotations = {
      # Use internal NLB (not internet-facing)
      "service.beta.kubernetes.io/aws-load-balancer-internal" = "true"
      "service.beta.kubernetes.io/aws-load-balancer-type"     = "nlb"
      # Cross-zone load balancing for reliability
      "service.beta.kubernetes.io/aws-load-balancer-cross-zone-load-balancing-enabled" = "true"
    }
  }

  spec {
    type = "LoadBalancer"

    selector = {
      app = "registry-native"
    }

    port {
      name        = "registry"
      port        = 5000
      target_port = 5000
    }
  }
}

# =============================================================================
# EFA Device Plugin
# =============================================================================

# NVIDIA Device Plugin is now managed by gpu-operator (see helm_release.nvidia_gpu_operator)
# Removed the manual kubernetes_daemonset to avoid conflicts

# AWS EFA Device Plugin to expose EFA resources to Kubernetes
resource "kubernetes_service_account" "efa_device_plugin_sa" {
  depends_on = [aws_eks_cluster.gpu_dev_cluster]

  metadata {
    name      = "aws-efa-k8s-device-plugin"
    namespace = "kube-system"
  }
}

resource "kubernetes_daemonset" "efa_device_plugin" {
  depends_on = [
    aws_eks_cluster.gpu_dev_cluster,
    aws_autoscaling_group.gpu_dev_nodes
  ]

  metadata {
    name      = "aws-efa-k8s-device-plugin-daemonset"
    namespace = "kube-system"
  }

  spec {
    selector {
      match_labels = {
        name = "aws-efa-k8s-device-plugin"
      }
    }

    template {
      metadata {
        labels = {
          name = "aws-efa-k8s-device-plugin"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.efa_device_plugin_sa.metadata[0].name
        host_network        = true

        toleration {
          key      = "CriticalAddonsOnly"
          operator = "Exists"
        }

        toleration {
          key      = "aws.amazon.com/efa"
          operator = "Exists"
          effect   = "NoSchedule"
        }

        node_selector = {
          "kubernetes.io/arch" = "amd64"
        }

        container {
          image = "602401143452.dkr.ecr.us-west-2.amazonaws.com/eks/aws-efa-k8s-device-plugin:v0.3.3"
          name  = "aws-efa-k8s-device-plugin"
          image_pull_policy = "Always"

          resources {
            requests = {
              cpu    = "10m"
              memory = "10Mi"
            }
            limits = {
              cpu    = "10m"
              memory = "10Mi"
            }
          }

          security_context {
            allow_privilege_escalation = false
            capabilities {
              drop = ["ALL"]
            }
          }

          volume_mount {
            name       = "device-plugin"
            mount_path = "/var/lib/kubelet/device-plugins"
          }

          volume_mount {
            name       = "proc"
            mount_path = "/host/proc"
          }

          volume_mount {
            name       = "sys"
            mount_path = "/host/sys"
          }
        }

        volume {
          name = "device-plugin"
          host_path {
            path = "/var/lib/kubelet/device-plugins"
          }
        }

        volume {
          name = "proc"
          host_path {
            path = "/proc"
          }
        }

        volume {
          name = "sys"
          host_path {
            path = "/sys"
          }
        }
      }
    }
  }
}

# =============================================================================
# NVIDIA GPU Operator
# =============================================================================

# NVIDIA GPU Operator - manages GPU drivers, device plugin, and monitoring
resource "helm_release" "nvidia_gpu_operator" {
  depends_on = [
    aws_eks_cluster.gpu_dev_cluster,
    aws_autoscaling_group.gpu_dev_nodes
  ]

  name       = "gpu-operator"
  repository = "https://helm.ngc.nvidia.com/nvidia"
  chart      = "gpu-operator"
  version    = "v25.3.3"
  namespace  = "gpu-operator"
  create_namespace = true

  # Wait for the operator to be ready
  wait = true
  timeout = 600

  set {
    name  = "operator.defaultRuntime"
    value = "containerd"
  }

  # Disable driver installation - drivers pre-installed on host via user-data
  set {
    name  = "driver.enabled"
    value = "false"
  }

  # Driver installation disabled - using host-installed drivers

  set {
    name  = "toolkit.enabled"
    value = "true"
  }

  set {
    name  = "devicePlugin.enabled"
    value = "true"
  }

  set {
    name  = "dcgmExporter.enabled"
    value = "true"
  }

  # Note: DCGM exclusion from profiling-dedicated nodes is handled via node label:
  # nvidia.com/gpu.deploy.dcgm-exporter=false (set in al2023-user-data.sh for profiling nodes)

  set {
    name  = "gfd.enabled"
    value = "true"
  }

  set {
    name  = "migManager.enabled"
    value = "true"
  }

  set {
    name  = "mig.strategy"
    value = "mixed"
  }

  # Configure MIG to expose full GPUs by default (not partitioned)
  set {
    name  = "migManager.config.default"
    value = "all-disabled"
  }

  set {
    name  = "nodeStatusExporter.enabled"
    value = "true"
  }

  # Tolerations for GPU nodes
  set {
    name  = "operator.tolerations[0].key"
    value = "nvidia.com/gpu"
  }

  set {
    name  = "operator.tolerations[0].operator"
    value = "Exists"
  }

  set {
    name  = "operator.tolerations[0].effect"
    value = "NoSchedule"
  }

  # Tolerations for CPU-only nodes
  set {
    name  = "operator.tolerations[1].key"
    value = "node-role"
  }

  set {
    name  = "operator.tolerations[1].operator"
    value = "Equal"
  }

  set {
    name  = "operator.tolerations[1].value"
    value = "cpu-only"
  }

  set {
    name  = "operator.tolerations[1].effect"
    value = "NoSchedule"
  }

  # Prefer CPU management nodes for GPU operator control plane components
  set {
    name  = "operator.nodeSelector.NodeType"
    value = "cpu"
  }

  # Runtime class configuration - toolkit uses default runtime, others use nvidia
  set {
    name  = "toolkit.runtimeClass"
    value = ""
  }

  # Other components can use nvidia runtime once it's configured by container toolkit
  set {
    name  = "devicePlugin.runtimeClass"
    value = "nvidia"
  }

  set {
    name  = "dcgmExporter.runtimeClass"
    value = "nvidia"
  }

  set {
    name  = "gfd.runtimeClass"
    value = "nvidia"
  }
}

# =============================================================================
# Profiling Node Labeler
# =============================================================================

# ServiceAccount for profiling node labeler
resource "kubernetes_service_account" "profiling_labeler" {
  metadata {
    name      = "profiling-node-labeler"
    namespace = "kube-system"
  }
}

# ClusterRole to allow labeling nodes
resource "kubernetes_cluster_role" "profiling_labeler" {
  metadata {
    name = "profiling-node-labeler"
  }

  rule {
    api_groups = [""]
    resources  = ["nodes"]
    verbs      = ["get", "list", "patch"]
  }
}

# ClusterRoleBinding for profiling labeler
resource "kubernetes_cluster_role_binding" "profiling_labeler" {
  metadata {
    name = "profiling-node-labeler"
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "ClusterRole"
    name      = kubernetes_cluster_role.profiling_labeler.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.profiling_labeler.metadata[0].name
    namespace = "kube-system"
  }
}

# CronJob to ensure one node per GPU type has profiling labels
resource "kubernetes_cron_job_v1" "profiling_node_labeler" {
  metadata {
    name      = "profiling-node-labeler"
    namespace = "kube-system"
  }

  spec {
    schedule                      = "*/5 * * * *" # Every 5 minutes
    successful_jobs_history_limit = 1
    failed_jobs_history_limit     = 1

    job_template {
      metadata {}
      spec {
        template {
          metadata {}
          spec {
            service_account_name = kubernetes_service_account.profiling_labeler.metadata[0].name
            restart_policy       = "OnFailure"

            container {
              name  = "labeler"
              image = "bitnami/kubectl:latest"

              command = ["/bin/bash", "-c"]
              args = [<<-EOT
                set -e
                GPU_TYPES="${join(" ", lookup(local.profiling_gpu_types, terraform.workspace, []))}"

                for GPU_TYPE in $GPU_TYPES; do
                  echo "Checking $GPU_TYPE nodes..."

                  # Check if any node already has the profiling label
                  EXISTING=$(kubectl get nodes -l GpuType=$GPU_TYPE,gpu.monitoring/profiling-dedicated=true -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

                  if [ -n "$EXISTING" ]; then
                    echo "$GPU_TYPE: Node $EXISTING already labeled for profiling"
                    continue
                  fi

                  # Get first available node of this GPU type
                  NODE=$(kubectl get nodes -l GpuType=$GPU_TYPE -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

                  if [ -z "$NODE" ]; then
                    echo "$GPU_TYPE: No nodes found, skipping"
                    continue
                  fi

                  # Label the node for profiling
                  echo "$GPU_TYPE: Labeling $NODE for Nsight profiling..."
                  kubectl label node "$NODE" \
                    gpu.monitoring/profiling-dedicated=true \
                    nvidia.com/gpu.deploy.dcgm-exporter=false \
                    --overwrite

                  echo "$GPU_TYPE: Successfully labeled $NODE"
                done

                echo "Profiling node labeling complete"
              EOT
              ]
            }

            # Run on CPU nodes to avoid using GPU resources
            node_selector = {
              "kubernetes.io/arch" = "amd64"
            }

            toleration {
              operator = "Exists"
            }
          }
        }
      }
    }
  }

  depends_on = [
    kubernetes_cluster_role_binding.profiling_labeler
  ]
}
