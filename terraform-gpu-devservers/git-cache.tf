# Git Cache Service - In-cluster object cache for fast git clone operations
# Maintains bare copies of pytorch/pytorch AND all its submodules, refreshed every 15 min.
# User pods get a transparent git wrapper that clones from cache,
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
        storage = "50Gi"
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

        # Init: populate cache with pytorch + all submodules
        init_container {
          name              = "seed-cache"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[CACHE] Seeding git object cache..."

            # Mirror main repo
            REPO_DIR="/git-cache/pytorch.git"
            if [ -d "$REPO_DIR" ]; then
              echo "[CACHE] pytorch cache exists, refreshing..."
              cd "$REPO_DIR" && git remote update --prune || true
            else
              echo "[CACHE] Cold start - mirroring pytorch/pytorch..."
              git clone --mirror https://github.com/pytorch/pytorch.git "$REPO_DIR"
            fi

            # Mirror all submodules (parse .gitmodules from the cached repo)
            echo "[CACHE] Mirroring submodules..."
            cd "$REPO_DIR"
            git show HEAD:.gitmodules 2>/dev/null | grep 'url = ' | awk '{print $3}' | while read url; do
              # Derive cache dir name from URL: https://github.com/org/repo.git -> org_repo.git
              name=$(echo "$url" | sed 's|https://github.com/||;s|/|_|g;s|\.git$||').git
              sub_dir="/git-cache/$name"
              if [ -d "$sub_dir" ]; then
                echo "[CACHE]   Refreshing $name..."
                cd "$sub_dir" && git remote update --prune 2>/dev/null || true
              else
                echo "[CACHE]   Mirroring $name..."
                git clone --mirror "$url" "$sub_dir" 2>/dev/null || echo "[CACHE]   WARNING: Failed to mirror $url"
              fi
            done

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

        # Sidecar: refreshes all cached repos every 15 minutes
        container {
          name              = "cache-updater"
          image             = "alpine/git:latest"
          image_pull_policy = "IfNotPresent"
          command           = ["/bin/sh", "-c"]
          args = [<<-EOT
            echo "[CACHE] Starting cache refresh loop..."
            while true; do
              for repo in /git-cache/*.git; do
                if [ -d "$repo" ]; then
                  name=$(basename "$repo")
                  echo "[CACHE] Refreshing $name..."
                  cd "$repo"
                  git remote update --prune 2>&1 || echo "[CACHE] WARNING: Failed to refresh $name"
                fi
              done
              # Also pick up any new submodules
              REPO_DIR="/git-cache/pytorch.git"
              if [ -d "$REPO_DIR" ]; then
                cd "$REPO_DIR"
                git show HEAD:.gitmodules 2>/dev/null | grep 'url = ' | awk '{print $3}' | while read url; do
                  name=$(echo "$url" | sed 's|https://github.com/||;s|/|_|g;s|\.git$||').git
                  sub_dir="/git-cache/$name"
                  if [ ! -d "$sub_dir" ]; then
                    echo "[CACHE] New submodule detected, mirroring $name..."
                    git clone --mirror "$url" "$sub_dir" 2>/dev/null || true
                  fi
                done
              fi
              echo "[CACHE] Refresh complete at $(date). Next in 900s..."
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
