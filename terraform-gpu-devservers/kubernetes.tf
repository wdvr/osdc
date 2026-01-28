# Kubernetes resources for GPU development pods

# Local variables for internal registry DNS names (Route53 private hosted zone)
locals {
  registry_ghcr_dns       = "registry-ghcr.internal.${var.prefix}.local:5000"
  registry_dockerhub_dns  = "registry-dockerhub.internal.${var.prefix}.local:5000"
  registry_native_dns     = "registry.internal.${var.prefix}.local:5000"
}

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
      }
    ])
  }

  # Ensure this is created after the cluster but before nodes try to join
}

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

# Service account for PostgreSQL database
resource "kubernetes_service_account" "postgres_sa" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-service-account"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "postgres"
    }
  }
}

# Role for PostgreSQL - access to secrets, configmaps, and persistent volume claims
resource "kubernetes_role" "postgres_role" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-role"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }

  # Access to secrets (for database credentials)
  rule {
    api_groups = [""]
    resources  = ["secrets"]
    verbs      = ["get", "list", "watch"]
  }

  # Access to configmaps (for PostgreSQL configuration)
  rule {
    api_groups = [""]
    resources  = ["configmaps"]
    verbs      = ["get", "list", "watch"]
  }

  # Access to persistent volume claims (for data storage)
  rule {
    api_groups = [""]
    resources  = ["persistentvolumeclaims"]
    verbs      = ["get", "list", "watch"]
  }
}

# Role binding for PostgreSQL service account
resource "kubernetes_role_binding" "postgres_role_binding" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-role-binding"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.postgres_role.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.postgres_sa.metadata[0].name
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }
}

# Secret for PostgreSQL credentials
resource "kubernetes_secret" "postgres_credentials" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-credentials"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "postgres"
    }
  }

  data = {
    POSTGRES_USER     = "gpudev"
    POSTGRES_PASSWORD = random_password.postgres_password.result
    POSTGRES_DB       = "gpudev"
  }

  type = "Opaque"
}

# Generate a random password for PostgreSQL
resource "random_password" "postgres_password" {
  length  = 32
  special = false  # Avoid special chars that might cause escaping issues
}

# Generate a password for PostgreSQL replication user
resource "random_password" "postgres_replication_password" {
  length  = 32
  special = false
}

# Secret for PostgreSQL replication credentials
resource "kubernetes_secret" "postgres_replication_credentials" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-replication-credentials"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "postgres"
    }
  }

  data = {
    REPLICATION_USER     = "replicator"
    REPLICATION_PASSWORD = random_password.postgres_replication_password.result
  }

  type = "Opaque"
}

# ConfigMap for PostgreSQL primary configuration
resource "kubernetes_config_map" "postgres_primary_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-primary-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "primary"
    }
  }

  data = {
    "postgresql.conf" = <<-EOT
      # Connection settings
      listen_addresses = '*'
      port = 5432
      max_connections = 200
      
      # Memory settings
      shared_buffers = 256MB
      effective_cache_size = 768MB
      work_mem = 16MB
      maintenance_work_mem = 128MB
      
      # WAL settings for replication
      wal_level = replica
      max_wal_senders = 10
      max_replication_slots = 10
      wal_keep_size = 1GB
      hot_standby = on
      
      # Checkpoints
      checkpoint_completion_target = 0.9
      
      # Logging
      log_destination = 'stderr'
      logging_collector = off
      log_statement = 'ddl'
      log_min_duration_statement = 1000
      
      # PGMQ optimization
      shared_preload_libraries = 'pg_partman_bgw'
    EOT

    "pg_hba.conf" = <<-EOT
      # TYPE  DATABASE        USER            ADDRESS                 METHOD
      local   all             all                                     trust
      host    all             all             127.0.0.1/32            scram-sha-256
      host    all             all             ::1/128                 scram-sha-256
      host    all             all             0.0.0.0/0               scram-sha-256
      host    replication     replicator      0.0.0.0/0               scram-sha-256
    EOT
  }
}

# ConfigMap for PostgreSQL replica configuration
resource "kubernetes_config_map" "postgres_replica_config" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-replica-config"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "replica"
    }
  }

  data = {
    "postgresql.conf" = <<-EOT
      # Connection settings
      listen_addresses = '*'
      port = 5432
      max_connections = 200
      
      # Memory settings
      shared_buffers = 256MB
      effective_cache_size = 768MB
      work_mem = 16MB
      maintenance_work_mem = 128MB
      
      # Replica settings
      hot_standby = on
      hot_standby_feedback = on
      
      # Logging
      log_destination = 'stderr'
      logging_collector = off
    EOT

    "pg_hba.conf" = <<-EOT
      # TYPE  DATABASE        USER            ADDRESS                 METHOD
      local   all             all                                     trust
      host    all             all             127.0.0.1/32            scram-sha-256
      host    all             all             ::1/128                 scram-sha-256
      host    all             all             0.0.0.0/0               scram-sha-256
    EOT
  }
}

# ConfigMap for PostgreSQL initialization script (creates PGMQ extension and replication user)
resource "kubernetes_config_map" "postgres_init_script" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-init-script"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "postgres"
    }
  }

  data = {
    "init-pgmq.sh" = <<-EOT
      #!/bin/bash
      set -e
      
      echo "Creating replication user..."
      psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
        -- Create replication user if not exists
        DO \$\$
        BEGIN
          IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = '$${REPLICATION_USER}') THEN
            CREATE ROLE $${REPLICATION_USER} WITH REPLICATION LOGIN PASSWORD '$${REPLICATION_PASSWORD}';
          END IF;
        END
        \$\$;
        
        -- Create PGMQ extension
        CREATE EXTENSION IF NOT EXISTS pgmq;
        
        -- Create pg_partman extension (used by PGMQ for partition management)
        CREATE EXTENSION IF NOT EXISTS pg_partman;
        
        -- Grant permissions
        GRANT ALL ON SCHEMA pgmq TO $${POSTGRES_USER};
        GRANT ALL ON ALL TABLES IN SCHEMA pgmq TO $${POSTGRES_USER};
        GRANT ALL ON ALL SEQUENCES IN SCHEMA pgmq TO $${POSTGRES_USER};
      EOSQL
      
      echo "PGMQ extension enabled and replication user created."
    EOT
  }
}

# ConfigMap for database schema files
resource "kubernetes_config_map" "database_schema" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "database-schema"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "postgres"
    }
  }

  # Load all SQL files from database/schema directory
  data = {
    for file in fileset("${path.module}/database/schema", "*.sql") :
    file => file("${path.module}/database/schema/${file}")
  }
}

# ConfigMap for database fixture files
resource "kubernetes_config_map" "database_fixtures" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "database-fixtures"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "postgres"
    }
  }

  # Load all SQL files from database/fixtures directory
  data = {
    for file in fileset("${path.module}/database/fixtures", "*.sql") :
    file => file("${path.module}/database/fixtures/${file}")
  }
}

# Job for database schema migration
# Name includes hash of schema files to trigger re-migration on changes
resource "kubernetes_job" "database_schema_migration" {
  depends_on = [
    kubernetes_stateful_set.postgres_primary,
    kubernetes_config_map.database_schema,
    kubernetes_config_map.database_fixtures,
  ]

  # Wait for job to complete before continuing
  wait_for_completion = true
  
  # Set timeouts for job completion
  timeouts {
    create = "10m"
    update = "10m"
  }

  metadata {
    # Include hash of all schema files in name to trigger re-run on changes
    name = "db-migration-${substr(md5(join("", [
      for f in sort(fileset("${path.module}/database/schema", "*.sql")) :
      filemd5("${path.module}/database/schema/${f}")
    ])), 0, 8)}"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app = "database-migration"
    }
  }

  spec {
    # Retry failed migrations (up to 3 retries = 4 total attempts)
    backoff_limit = 3
    
    # Clean up completed/failed jobs after 1 hour
    ttl_seconds_after_finished = 3600
    
    template {
      metadata {
        labels = {
          app = "database-migration"
        }
      }

      spec {
        restart_policy = "OnFailure"

        # Wait for postgres to be ready
        init_container {
          name  = "wait-for-postgres"
          image = "${local.registry_ghcr_dns}/pgmq/pg18-pgmq:v1.8.1"

          command = ["/bin/bash", "-c"]
          args = [<<-EOT
            echo "Waiting for PostgreSQL to be ready..."
            until pg_isready -h postgres-primary -U gpudev -d gpudev; do
              echo "PostgreSQL is unavailable - sleeping"
              sleep 2
            done
            echo "PostgreSQL is ready!"
          EOT
          ]

          env_from {
            secret_ref {
              name = kubernetes_secret.postgres_credentials.metadata[0].name
            }
          }
        }

        container {
          name  = "migrate"
          image = "${local.registry_ghcr_dns}/pgmq/pg18-pgmq:v1.8.1"

          command = ["/bin/bash", "-c"]
          args = [<<-EOT
            set -e
            
            echo "=========================================="
            echo "Database Schema Migration"
            echo "=========================================="
            echo ""
            
            # Run schema files in order
            echo "Applying schema files..."
            for file in $(ls /schema/*.sql | sort); do
              if [ -f "$file" ]; then
                echo "  → $(basename $file)"
                PGPASSWORD="$POSTGRES_PASSWORD" psql \
                  -h postgres-primary \
                  -U "$POSTGRES_USER" \
                  -d "$POSTGRES_DB" \
                  -v ON_ERROR_STOP=1 \
                  -f "$file" || {
                    echo "ERROR: Failed to apply $(basename $file)"
                    exit 1
                  }
              fi
            done
            
            echo ""
            echo "Applying fixture data..."
            
            # Run fixtures in order
            for file in $(ls /fixtures/*.sql | sort); do
              if [ -f "$file" ]; then
                echo "  → $(basename $file)"
                PGPASSWORD="$POSTGRES_PASSWORD" psql \
                  -h postgres-primary \
                  -U "$POSTGRES_USER" \
                  -d "$POSTGRES_DB" \
                  -v ON_ERROR_STOP=1 \
                  -f "$file" || {
                    echo "ERROR: Failed to apply $(basename $file)"
                    exit 1
                  }
              fi
            done
            
            echo ""
            echo "=========================================="
            echo "Migration completed successfully!"
            echo "=========================================="
          EOT
          ]

          env_from {
            secret_ref {
              name = kubernetes_secret.postgres_credentials.metadata[0].name
            }
          }

          volume_mount {
            name       = "schema"
            mount_path = "/schema"
          }

          volume_mount {
            name       = "fixtures"
            mount_path = "/fixtures"
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
        }

        volume {
          name = "schema"
          config_map {
            name = kubernetes_config_map.database_schema.metadata[0].name
          }
        }

        volume {
          name = "fixtures"
          config_map {
            name = kubernetes_config_map.database_fixtures.metadata[0].name
          }
        }
      }
    }
  }
}

# PersistentVolumeClaim for PostgreSQL primary
resource "kubernetes_persistent_volume_claim" "postgres_primary_pvc" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_storage_class.gp3,  # Storage class defined in monitoring.tf
  ]

  # Don't wait for PVC to bind - gp3 uses WaitForFirstConsumer mode
  # PVC will bind when the StatefulSet pod starts
  wait_until_bound = false

  metadata {
    name      = "postgres-primary-data"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "primary"
    }
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.gp3.metadata[0].name

    resources {
      requests = {
        storage = "100Gi"
      }
    }
  }
}

# PersistentVolumeClaim for PostgreSQL replica
resource "kubernetes_persistent_volume_claim" "postgres_replica_pvc" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_storage_class.gp3,  # Storage class defined in monitoring.tf
  ]

  wait_until_bound = false  # PVC uses WaitForFirstConsumer - will bind when pod is created

  metadata {
    name      = "postgres-replica-data"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "replica"
    }
  }

  spec {
    access_modes       = ["ReadWriteOnce"]
    storage_class_name = kubernetes_storage_class.gp3.metadata[0].name

    resources {
      requests = {
        storage = "100Gi"
      }
    }
  }
}

# StatefulSet for PostgreSQL Primary with PGMQ
resource "kubernetes_stateful_set" "postgres_primary" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_config_map.postgres_primary_config,
    kubernetes_config_map.postgres_init_script,
    kubernetes_secret.postgres_credentials,
    kubernetes_secret.postgres_replication_credentials,
    kubernetes_deployment.registry_ghcr,  # Wait for registry cache to be deployed
    kubernetes_service.registry_ghcr,
  ]

  metadata {
    name      = "postgres-primary"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "primary"
    }
  }

  spec {
    service_name = "postgres-primary-headless"
    replicas     = 1

    selector {
      match_labels = {
        app  = "postgres"
        role = "primary"
      }
    }

    template {
      metadata {
        labels = {
          app  = "postgres"
          role = "primary"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.postgres_sa.metadata[0].name

        # Set fsGroup to postgres UID so volumes are writable
        security_context {
          fs_group                = 999
          fs_group_change_policy  = "OnRootMismatch"
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

        init_container {
          name  = "init-config"
          image = "busybox:1.36"  # Direct pull - can migrate to cache after registry-dockerhub is stable

          security_context {
            run_as_user = 999
          }

          command = ["/bin/sh", "-c"]
          args = [<<-EOT
            cp /config/postgresql.conf /var/lib/postgresql/data-config/postgresql.conf
            cp /config/pg_hba.conf /var/lib/postgresql/data-config/pg_hba.conf
            chmod 600 /var/lib/postgresql/data-config/*.conf
          EOT
          ]

          volume_mount {
            name       = "config"
            mount_path = "/config"
          }

          volume_mount {
            name       = "config-writable"
            mount_path = "/var/lib/postgresql/data-config"
          }
        }

        container {
          name  = "postgres"
          image = "${local.registry_ghcr_dns}/pgmq/pg18-pgmq:v1.8.1"

          port {
            container_port = 5432
            name           = "postgres"
          }

          env_from {
            secret_ref {
              name = kubernetes_secret.postgres_credentials.metadata[0].name
            }
          }

          env_from {
            secret_ref {
              name = kubernetes_secret.postgres_replication_credentials.metadata[0].name
            }
          }

          env {
            name  = "PGDATA"
            value = "/var/lib/postgresql/data/pgdata"
          }

          # PostgreSQL startup args to use our config
          args = [
            "-c", "config_file=/var/lib/postgresql/data-config/postgresql.conf",
            "-c", "hba_file=/var/lib/postgresql/data-config/pg_hba.conf"
          ]

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/postgresql/data"
          }

          volume_mount {
            name       = "config-writable"
            mount_path = "/var/lib/postgresql/data-config"
          }

          volume_mount {
            name       = "init-scripts"
            mount_path = "/docker-entrypoint-initdb.d"
          }

          resources {
            requests = {
              cpu    = "500m"
              memory = "1Gi"
            }
            limits = {
              cpu    = "2"
              memory = "4Gi"
            }
          }

          liveness_probe {
            exec {
              command = ["pg_isready", "-U", "gpudev", "-d", "gpudev"]
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 6
          }

          readiness_probe {
            exec {
              command = ["pg_isready", "-U", "gpudev", "-d", "gpudev"]
            }
            initial_delay_seconds = 5
            period_seconds        = 5
            timeout_seconds       = 3
            failure_threshold     = 3
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.postgres_primary_pvc.metadata[0].name
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.postgres_primary_config.metadata[0].name
          }
        }

        volume {
          name = "config-writable"
          empty_dir {}
        }

        volume {
          name = "init-scripts"
          config_map {
            name         = kubernetes_config_map.postgres_init_script.metadata[0].name
            default_mode = "0755"
          }
        }
      }
    }
  }
}

# Headless Service for PostgreSQL Primary (for StatefulSet DNS)
resource "kubernetes_service" "postgres_primary_headless" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-primary-headless"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "primary"
    }
  }

  spec {
    type       = "ClusterIP"
    cluster_ip = "None"

    selector = {
      app  = "postgres"
      role = "primary"
    }

    port {
      name        = "postgres"
      port        = 5432
      target_port = 5432
    }
  }
}

# ClusterIP Service for PostgreSQL Primary (read-write endpoint)
resource "kubernetes_service" "postgres_primary" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-primary"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "primary"
    }
  }

  spec {
    type = "ClusterIP"

    selector = {
      app  = "postgres"
      role = "primary"
    }

    port {
      name        = "postgres"
      port        = 5432
      target_port = 5432
    }
  }
}

# StatefulSet for PostgreSQL Replica (streaming replication from primary)
resource "kubernetes_stateful_set" "postgres_replica" {
  depends_on = [
    kubernetes_namespace.controlplane,
    kubernetes_config_map.postgres_replica_config,
    kubernetes_secret.postgres_credentials,
    kubernetes_secret.postgres_replication_credentials,
    kubernetes_stateful_set.postgres_primary,
    kubernetes_deployment.registry_ghcr,  # Wait for registry cache to be deployed
    kubernetes_service.registry_ghcr,
  ]

  metadata {
    name      = "postgres-replica"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "replica"
    }
  }

  spec {
    service_name = "postgres-replica-headless"
    replicas     = 1

    selector {
      match_labels = {
        app  = "postgres"
        role = "replica"
      }
    }

    template {
      metadata {
        labels = {
          app  = "postgres"
          role = "replica"
        }
      }

      spec {
        service_account_name = kubernetes_service_account.postgres_sa.metadata[0].name

        # Set fsGroup to postgres UID so volumes are writable
        security_context {
          fs_group                = 999
          fs_group_change_policy  = "OnRootMismatch"
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

        # Init container to set up streaming replication
        init_container {
          name  = "init-replica"
          image = "${local.registry_ghcr_dns}/pgmq/pg18-pgmq:v1.8.1"

          security_context {
            run_as_user = 999
          }

          command = ["/bin/bash", "-c"]
          args = [<<-EOT
            set -e
            
            # Check if data directory is empty (fresh replica)
            if [ -z "$(ls -A /var/lib/postgresql/data/pgdata 2>/dev/null)" ]; then
              echo "Initializing replica from primary..."
              
              # Wait for primary to be ready
              until pg_isready -h postgres-primary -p 5432 -U gpudev; do
                echo "Waiting for primary to be ready..."
                sleep 2
              done
              
              # Create base backup from primary
              PGPASSWORD=$REPLICATION_PASSWORD pg_basebackup \
                -h postgres-primary \
                -p 5432 \
                -U replicator \
                -D /var/lib/postgresql/data/pgdata \
                -Fp -Xs -P -R
              
              echo "Base backup complete. Replica initialized."
            else
              echo "Data directory exists. Skipping initialization."
            fi
            
            # Copy config files
            cp /config/postgresql.conf /var/lib/postgresql/data-config/postgresql.conf
            cp /config/pg_hba.conf /var/lib/postgresql/data-config/pg_hba.conf
            chmod 600 /var/lib/postgresql/data-config/*.conf
          EOT
          ]

          env_from {
            secret_ref {
              name = kubernetes_secret.postgres_replication_credentials.metadata[0].name
            }
          }

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/postgresql/data"
          }

          volume_mount {
            name       = "config"
            mount_path = "/config"
          }

          volume_mount {
            name       = "config-writable"
            mount_path = "/var/lib/postgresql/data-config"
          }
        }

        container {
          name  = "postgres"
          image = "${local.registry_ghcr_dns}/pgmq/pg18-pgmq:v1.8.1"

          port {
            container_port = 5432
            name           = "postgres"
          }

          env {
            name  = "PGDATA"
            value = "/var/lib/postgresql/data/pgdata"
          }

          # PostgreSQL startup args to use our config
          args = [
            "-c", "config_file=/var/lib/postgresql/data-config/postgresql.conf",
            "-c", "hba_file=/var/lib/postgresql/data-config/pg_hba.conf"
          ]

          volume_mount {
            name       = "data"
            mount_path = "/var/lib/postgresql/data"
          }

          volume_mount {
            name       = "config-writable"
            mount_path = "/var/lib/postgresql/data-config"
          }

          resources {
            requests = {
              cpu    = "500m"
              memory = "1Gi"
            }
            limits = {
              cpu    = "2"
              memory = "4Gi"
            }
          }

          liveness_probe {
            exec {
              command = ["pg_isready", "-U", "gpudev", "-d", "gpudev"]
            }
            initial_delay_seconds = 30
            period_seconds        = 10
            timeout_seconds       = 5
            failure_threshold     = 6
          }

          readiness_probe {
            exec {
              command = ["pg_isready", "-U", "gpudev", "-d", "gpudev"]
            }
            initial_delay_seconds = 5
            period_seconds        = 5
            timeout_seconds       = 3
            failure_threshold     = 3
          }
        }

        volume {
          name = "data"
          persistent_volume_claim {
            claim_name = kubernetes_persistent_volume_claim.postgres_replica_pvc.metadata[0].name
          }
        }

        volume {
          name = "config"
          config_map {
            name = kubernetes_config_map.postgres_replica_config.metadata[0].name
          }
        }

        volume {
          name = "config-writable"
          empty_dir {}
        }
      }
    }
  }
}

# Headless Service for PostgreSQL Replica (for StatefulSet DNS)
resource "kubernetes_service" "postgres_replica_headless" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-replica-headless"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "replica"
    }
  }

  spec {
    type       = "ClusterIP"
    cluster_ip = "None"

    selector = {
      app  = "postgres"
      role = "replica"
    }

    port {
      name        = "postgres"
      port        = 5432
      target_port = 5432
    }
  }
}

# ClusterIP Service for PostgreSQL Replica (read-only endpoint)
resource "kubernetes_service" "postgres_replica" {
  depends_on = [kubernetes_namespace.controlplane]

  metadata {
    name      = "postgres-replica"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
    labels = {
      app  = "postgres"
      role = "replica"
    }
  }

  spec {
    type = "ClusterIP"

    selector = {
      app  = "postgres"
      role = "replica"
    }

    port {
      name        = "postgres"
      port        = 5432
      target_port = 5432
    }
  }
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

# Service account for GPU development pods
resource "kubernetes_service_account" "gpu_dev_sa" {
  depends_on = [aws_eks_cluster.gpu_dev_cluster]

  metadata {
    name      = "gpu-dev-service-account"
    namespace = kubernetes_namespace.gpu_dev.metadata[0].name
  }
}

# Role for GPU development pods (basic permissions)
resource "kubernetes_role" "gpu_dev_role" {
  depends_on = [aws_eks_cluster.gpu_dev_cluster]

  metadata {
    namespace = kubernetes_namespace.gpu_dev.metadata[0].name
    name      = "gpu-dev-role"
  }

  rule {
    api_groups = [""]
    resources  = ["pods", "pods/log", "pods/exec"]
    verbs      = ["get", "list", "create", "update", "patch", "watch"]
  }
}

# Role binding for GPU development service account
resource "kubernetes_role_binding" "gpu_dev_role_binding" {
  depends_on = [aws_eks_cluster.gpu_dev_cluster]

  metadata {
    name      = "gpu-dev-role-binding"
    namespace = kubernetes_namespace.gpu_dev.metadata[0].name
  }

  role_ref {
    api_group = "rbac.authorization.k8s.io"
    kind      = "Role"
    name      = kubernetes_role.gpu_dev_role.metadata[0].name
  }

  subject {
    kind      = "ServiceAccount"
    name      = kubernetes_service_account.gpu_dev_sa.metadata[0].name
    namespace = kubernetes_namespace.gpu_dev.metadata[0].name
  }
}

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

# DaemonSet to pre-pull GPU dev container image on all GPU nodes
# This ensures first user on new node doesn't wait for slow image pull
# After rebuilding image, trigger re-pull with: kubectl rollout restart daemonset gpu-dev-image-prepuller -n kube-system
resource "kubernetes_manifest" "image_prepuller_daemonset" {
  # force_conflicts needed because ecr.tf runs "kubectl rollout restart" after each image push,
  # which adds an annotation owned by kubectl-rollout that would otherwise conflict with terraform
  field_manager {
    force_conflicts = true
  }

  # Tell provider these fields are server-managed and shouldn't cause drift errors
  computed_fields = [
    "metadata.annotations[\"deprecated.daemonset.template.generation\"]",
    "metadata.annotations[\"kubectl.kubernetes.io/restartedAt\"]",
  ]

  manifest = {
    apiVersion = "apps/v1"
    kind       = "DaemonSet"
    metadata = {
      name      = "gpu-dev-image-prepuller"
      namespace = "kube-system"
      labels = {
        app = "image-prepuller"
      }
    }
    spec = {
      selector = {
        matchLabels = {
          app = "image-prepuller"
        }
      }
      template = {
        metadata = {
          labels = {
            app = "image-prepuller"
          }
        }
        spec = {
          nodeSelector = {
            NodeType                         = "gpu"
            "kubernetes.io/arch"            = "amd64"
          }
          tolerations = [
            {
              key      = "nvidia.com/gpu"
              operator = "Exists"
              effect   = "NoSchedule"
            }
          ]
          initContainers = [
            {
              name            = "pull-gpu-dev-image"
              image           = local.latest_image_uri  # Use stable 'latest' tag
              imagePullPolicy = "Always"
              command         = ["/bin/sh", "-c", "echo 'GPU dev image pulled successfully'"]
            }
          ]
          containers = [
            {
              name  = "pause"
              image = "registry.k8s.io/pause:3.10"
              resources = {
                requests = {
                  cpu    = "10m"
                  memory = "10Mi"
                }
                limits = {
                  cpu    = "10m"
                  memory = "10Mi"
                }
              }
            }
          ]
        }
      }
    }
  }

  depends_on = [
    null_resource.docker_build_and_push
  ]
}

# GPU types that should have one node labeled for Nsight profiling (no DCGM)
locals {
  profiling_gpu_types = {
    default = ["t4"]           # Test: one T4 node for profiling
    prod    = ["h200", "b200"] # Prod: one H200 and one B200 node for profiling
  }
}

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
