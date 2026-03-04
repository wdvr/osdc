# Git Mirror Service - In-cluster cache for fast git clone operations
# Deploys a bare mirror of pytorch/pytorch served via git daemon
# User pods auto-configured with url.insteadOf for transparent use

resource "kubernetes_persistent_volume_claim" "git_mirror_cache" {
  metadata {
    name      = "git-mirror-cache"
    namespace = "gpu-controlplane"
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = "gp3"

    resources {
      requests = {
        storage = "20Gi"
      }
    }
  }
}

resource "kubernetes_deployment" "git_mirror" {
  metadata {
    name      = "git-mirror"
    namespace = "gpu-controlplane"
    labels = {
      app = "git-mirror"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "git-mirror"
      }
    }

    template {
      metadata {
        labels = {
          app = "git-mirror"
        }
      }

      spec {
        node_selector = {
          NodeType = "cpu"
        }

        toleration {
          key      = "node-role"
          operator = "Equal"
          value    = "cpu-only"
          effect   = "NoSchedule"
        }

        # Init: clone mirror if not already present
        init_container {
          name              = "initial-mirror"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[INIT] Setting up git mirror..."
            REPO_DIR="/git-cache/pytorch.git"
            if [ -d "$REPO_DIR" ]; then
              echo "[INIT] Mirror pytorch already exists, updating..."
              cd "$REPO_DIR" && git remote update --prune || true
            else
              echo "[INIT] Creating mirror of pytorch/pytorch..."
              git clone --mirror https://github.com/pytorch/pytorch.git "$REPO_DIR"
            fi
            echo "[INIT] Mirror setup complete"
          EOT
          ]

          volume_mount {
            name       = "git-cache"
            mount_path = "/git-cache"
          }
        }

        # Git daemon: serves repos read-only over git:// protocol
        container {
          name              = "git-daemon"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[GIT-MIRROR] Starting git daemon..."
            for repo in /git-cache/*.git; do
              touch "$repo/git-daemon-export-ok"
            done
            git daemon \
              --verbose \
              --export-all \
              --base-path=/git-cache \
              --reuseaddr \
              --strict-paths \
              /git-cache
          EOT
          ]

          port {
            container_port = 9418
            name           = "git"
          }

          volume_mount {
            name       = "git-cache"
            mount_path = "/git-cache"
            read_only  = true
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "1Gi"
            }
          }

          liveness_probe {
            tcp_socket {
              port = 9418
            }
            initial_delay_seconds = 10
            period_seconds        = 30
          }
        }

        # Sidecar: updates mirror every 15 minutes
        container {
          name              = "mirror-updater"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[UPDATER] Starting mirror update loop..."
            while true; do
              REPO_DIR="/git-cache/pytorch.git"
              if [ -d "$REPO_DIR" ]; then
                echo "[UPDATER] Updating pytorch mirror..."
                cd "$REPO_DIR"
                git remote update --prune 2>&1 || echo "[UPDATER] WARNING: Failed to update"
                echo "[UPDATER] Updated at $(date)"
              fi
              echo "[UPDATER] Next update in 900s..."
              sleep 900
            done
          EOT
          ]

          volume_mount {
            name       = "git-cache"
            mount_path = "/git-cache"
          }

          resources {
            requests = {
              cpu    = "100m"
              memory = "256Mi"
            }
            limits = {
              cpu    = "500m"
              memory = "1Gi"
            }
          }
        }

        volume {
          name = "git-cache"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.git_mirror_cache.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "git_mirror" {
  metadata {
    name      = "git-mirror"
    namespace = "gpu-controlplane"
    labels = {
      app = "git-mirror"
    }
  }

  spec {
    type = "ClusterIP"

    port {
      port        = 9418
      target_port = 9418
      protocol    = "TCP"
      name        = "git"
    }

    selector = {
      app = "git-mirror"
    }
  }
}
