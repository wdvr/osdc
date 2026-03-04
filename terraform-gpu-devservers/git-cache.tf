# Git Cache Service - In-cluster object cache for fast git clone operations
# Maintains a bare copy of pytorch/pytorch, refreshed every 15 minutes.
# User pods get a `git-clone-fast` helper that clones from the cache,
# then sets origin to GitHub — all subsequent git ops go to GitHub directly.

resource "kubernetes_persistent_volume_claim" "git_cache" {
  metadata {
    name      = "git-cache"
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

resource "kubernetes_deployment" "git_cache" {
  metadata {
    name      = "git-cache"
    namespace = "gpu-controlplane"
    labels = {
      app = "git-cache"
    }
  }

  spec {
    replicas = 1

    selector {
      match_labels = {
        app = "git-cache"
      }
    }

    template {
      metadata {
        labels = {
          app = "git-cache"
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

        # Init: populate cache if empty
        init_container {
          name              = "seed-cache"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[CACHE] Seeding git object cache..."
            REPO_DIR="/git-cache/pytorch.git"
            if [ -d "$REPO_DIR" ]; then
              echo "[CACHE] Cache exists, refreshing..."
              cd "$REPO_DIR" && git remote update --prune || true
            else
              echo "[CACHE] Cold start - fetching pytorch/pytorch objects..."
              git clone --mirror https://github.com/pytorch/pytorch.git "$REPO_DIR"
            fi
            echo "[CACHE] Seed complete"
          EOT
          ]

          volume_mount {
            name       = "git-cache"
            mount_path = "/git-cache"
          }
        }

        # Git daemon: serves cached objects read-only over git:// protocol
        container {
          name              = "git-daemon"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[GIT-CACHE] Starting git daemon (read-only object server)..."
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

        # Sidecar: refreshes cache every 15 minutes
        container {
          name              = "cache-updater"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[CACHE] Starting cache refresh loop..."
            while true; do
              REPO_DIR="/git-cache/pytorch.git"
              if [ -d "$REPO_DIR" ]; then
                echo "[CACHE] Refreshing pytorch cache..."
                cd "$REPO_DIR"
                git remote update --prune 2>&1 || echo "[CACHE] WARNING: Refresh failed"
                echo "[CACHE] Refreshed at $(date)"
              fi
              echo "[CACHE] Next refresh in 900s..."
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
            claim_name = kubernetes_persistent_volume_claim.git_cache.metadata[0].name
          }
        }
      }
    }
  }
}

resource "kubernetes_service" "git_cache" {
  metadata {
    name      = "git-cache"
    namespace = "gpu-controlplane"
    labels = {
      app = "git-cache"
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
      app = "git-cache"
    }
  }
}
