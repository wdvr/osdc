# Database Schema Management

This directory contains the database schema and fixture files for the GPU Dev platform. The schema is managed declaratively using SQL files and applied via OpenTofu/Kubernetes during infrastructure deployment.

## Directory Structure

```
database/
├── README.md           # This file
├── schema/             # Database schema DDL files
│   ├── 001_users_and_keys.sql
│   ├── 002_reservations.sql
│   ├── 003_disks.sql
│   ├── 004_gpu_types.sql
│   ├── 005_domain_mappings.sql
│   ├── 006_alb_target_groups.sql
│   ├── 007_pgmq_queues.sql
│   ├── 008_add_expiry_tracking.sql
│   └── 009_add_availability_to_gpu_types.sql
└── fixtures/           # Initial data/seed files
    └── 001_initial_gpu_types.sql
```

## Table Schemas

### `api_users`

User accounts for API authentication.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `user_id` | SERIAL | PRIMARY KEY | Auto-incrementing user ID |
| `username` | VARCHAR(255) | UNIQUE, NOT NULL | GitHub username |
| `email` | VARCHAR(255) | | User email (optional) |
| `created_at` | TIMESTAMP | DEFAULT NOW() | Account creation time |
| `is_active` | BOOLEAN | DEFAULT true | Account status |

### `api_keys`

API keys for authenticated requests.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `key_id` | SERIAL | PRIMARY KEY | Auto-incrementing key ID |
| `user_id` | INTEGER | FK→api_users, CASCADE | Owner user ID |
| `key_hash` | VARCHAR(128) | UNIQUE, NOT NULL | SHA-256 hash of API key |
| `key_prefix` | VARCHAR(16) | NOT NULL | First chars for identification |
| `created_at` | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Key creation time |
| `expires_at` | TIMESTAMP WITH TIME ZONE | | Key expiration (default: 2 hours) |
| `last_used_at` | TIMESTAMP WITH TIME ZONE | | Last API call with this key |
| `is_active` | BOOLEAN | DEFAULT true | Key status |
| `description` | TEXT | | Optional key description |

### `reservations`

GPU reservation/job tracking.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `reservation_id` | VARCHAR(255) | PRIMARY KEY | Unique reservation ID |
| `user_id` | VARCHAR(255) | NOT NULL | Owner's username |
| `status` | VARCHAR(50) | NOT NULL | Status (queued, preparing, running, etc.) |
| `gpu_type` | VARCHAR(50) | | GPU type (h100, a100, t4, etc.) |
| `gpu_count` | INTEGER | | Number of GPUs requested |
| `instance_type` | VARCHAR(100) | | AWS instance type |
| `duration_hours` | FLOAT | NOT NULL | Requested duration |
| `created_at` | TIMESTAMP WITH TIME ZONE | NOT NULL | Request creation time |
| `launched_at` | TIMESTAMP WITH TIME ZONE | | Pod launch time |
| `expires_at` | TIMESTAMP WITH TIME ZONE | | Expiration time |
| `updated_at` | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Last update (auto-triggered) |
| `name` | VARCHAR(255) | | User-friendly reservation name |
| `github_user` | VARCHAR(255) | | GitHub username for SSH keys |
| `pod_name` | VARCHAR(255) | | Kubernetes pod name |
| `namespace` | VARCHAR(100) | DEFAULT 'default' | Kubernetes namespace |
| `node_ip` | VARCHAR(50) | | Node public IP for SSH |
| `node_port` | INTEGER | | NodePort for SSH access |
| `ssh_command` | TEXT | | Ready-to-use SSH command |
| `jupyter_enabled` | BOOLEAN | DEFAULT FALSE | Jupyter notebook enabled |
| `jupyter_url` | TEXT | | Jupyter access URL |
| `jupyter_port` | INTEGER | | Jupyter port |
| `jupyter_token` | VARCHAR(255) | | Jupyter authentication token |
| `jupyter_error` | TEXT | | Jupyter startup error |
| `ebs_volume_id` | VARCHAR(255) | | Attached EBS volume ID |
| `disk_name` | VARCHAR(255) | | Persistent disk name |
| `failure_reason` | TEXT | | Error message if failed |
| `current_detailed_status` | TEXT | | Detailed status message |
| `status_history` | JSONB | DEFAULT '[]' | Status change history |
| `pod_logs` | TEXT | | Recent pod logs |
| `warning` | TEXT | | Active warning message |
| `secondary_users` | JSONB | DEFAULT '[]' | Additional users with access |
| `is_multinode` | BOOLEAN | DEFAULT FALSE | Multi-node reservation |
| `master_reservation_id` | VARCHAR(255) | | Master reservation for workers |
| `node_index` | INTEGER | | Node index in multi-node |
| `total_nodes` | INTEGER | | Total nodes in multi-node |
| `cli_version` | VARCHAR(50) | | CLI version used |
| `ebs_availability_zone` | VARCHAR(50) | | EBS volume AZ |
| `domain_name` | VARCHAR(255) | | Subdomain name |
| `fqdn` | VARCHAR(512) | | Full qualified domain name |
| `alb_config` | JSONB | | ALB/NLB configuration |
| `preserve_entrypoint` | BOOLEAN | DEFAULT false | Keep Docker ENTRYPOINT |
| `node_private_ip` | VARCHAR(50) | | Node private IP |

### `disks`

Persistent disk management.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `disk_id` | UUID | PRIMARY KEY | Auto-generated disk ID |
| `disk_name` | TEXT | NOT NULL, UNIQUE(user_id, disk_name) | Disk name |
| `user_id` | TEXT | NOT NULL | Owner's username |
| `size_gb` | INTEGER | | Disk size in GB |
| `disk_size` | TEXT | | Human-readable usage (e.g., "1.2G") |
| `created_at` | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Creation time |
| `last_used` | TIMESTAMP WITH TIME ZONE | | Last usage time |
| `in_use` | BOOLEAN | DEFAULT FALSE | Currently attached |
| `reservation_id` | VARCHAR(255) | FK→reservations, SET NULL | Current reservation |
| `is_backing_up` | BOOLEAN | DEFAULT FALSE | Backup in progress |
| `is_deleted` | BOOLEAN | DEFAULT FALSE | Soft deleted |
| `delete_date` | DATE | | Scheduled deletion date |
| `snapshot_count` | INTEGER | DEFAULT 0 | Number of snapshots |
| `pending_snapshot_count` | INTEGER | DEFAULT 0 | Pending snapshots |
| `ebs_volume_id` | TEXT | | AWS EBS volume ID |
| `last_snapshot_at` | TIMESTAMP WITH TIME ZONE | | Last snapshot time |
| `operation_id` | UUID | | Current async operation |
| `operation_status` | TEXT | | Operation status |
| `operation_error` | TEXT | | Operation error message |
| `latest_snapshot_content_s3` | TEXT | | S3 path to snapshot |
| `last_updated` | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Auto-updated timestamp |

### `gpu_types`

GPU configuration and availability.

| Column | Type | Constraints | Description |
|--------|------|-------------|-------------|
| `gpu_type` | VARCHAR(50) | PRIMARY KEY | GPU type identifier |
| `instance_type` | VARCHAR(100) | NOT NULL | AWS instance type |
| `max_gpus` | INTEGER | NOT NULL | GPUs per instance |
| `cpus` | INTEGER | NOT NULL | vCPUs per instance |
| `memory_gb` | INTEGER | NOT NULL | RAM in GB |
| `total_cluster_gpus` | INTEGER | DEFAULT 0 | Total GPUs in cluster |
| `max_per_node` | INTEGER | | Max GPUs per node |
| `is_active` | BOOLEAN | DEFAULT true | Type enabled |
| `created_at` | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Creation time |
| `updated_at` | TIMESTAMP WITH TIME ZONE | DEFAULT NOW() | Last update |
| `description` | TEXT | | Human-readable description |

## How It Works

### 1. Schema Files (`schema/`)

SQL files that define the database structure:
- Tables
- Indexes
- Triggers
- Functions

Files are executed in **lexicographic order** (001, 002, 003...), so number them appropriately to respect dependencies.

**Key Features:**
- All DDL uses `CREATE TABLE IF NOT EXISTS` for idempotency
- All indexes use `CREATE INDEX IF NOT EXISTS`
- Triggers are created with `CREATE OR REPLACE FUNCTION` and `DROP TRIGGER IF EXISTS`

### 2. Fixture Files (`fixtures/`)

SQL files that populate initial/seed data:
- GPU type configurations
- Default settings
- Reference data

Files use `INSERT ... ON CONFLICT DO UPDATE` to be idempotent.

### 3. Terraform Integration

The schema is applied via Kubernetes Job during `tofu apply`:

1. **ConfigMaps**: Schema and fixture files are loaded into ConfigMaps
2. **Migration Job**: Runs after PostgreSQL is ready, applies all SQL files in order
3. **API Deployment**: Only starts after migration job completes successfully

The migration job name includes a hash of all schema files, so any changes to the schema will trigger a new migration run.

## Making Schema Changes

### Adding a New Table

1. Create a new file in `schema/` with an appropriate number:
   ```bash
   # Example: 005_new_feature.sql
   cd terraform-gpu-devservers/database/schema
   vim 005_new_feature.sql
   ```

2. Write idempotent DDL:
   ```sql
   -- 005_new_feature.sql
   CREATE TABLE IF NOT EXISTS my_new_table (
       id SERIAL PRIMARY KEY,
       name VARCHAR(255) NOT NULL,
       created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
   );
   
   CREATE INDEX IF NOT EXISTS idx_my_new_table_name
       ON my_new_table(name);
   ```

3. Apply via Terraform:
   ```bash
   cd terraform-gpu-devservers
   tofu plan   # See that migration job will be recreated
   tofu apply  # Apply the changes
   ```

### Modifying Existing Tables

**⚠️ Important:** Schema files should be **append-only** for production safety.

For table modifications:

1. Add **new columns** using `ALTER TABLE IF NOT EXISTS` patterns (if supported), or:
2. Create a new migration file with the changes:
   ```sql
   -- 005_add_column_to_users.sql
   DO $$
   BEGIN
       IF NOT EXISTS (
           SELECT 1 FROM information_schema.columns 
           WHERE table_name = 'api_users' 
           AND column_name = 'last_login'
       ) THEN
           ALTER TABLE api_users ADD COLUMN last_login TIMESTAMP WITH TIME ZONE;
       END IF;
   END $$;
   ```

### Updating Fixture Data

Fixtures use `ON CONFLICT` to update existing data:

```sql
INSERT INTO gpu_types (gpu_type, instance_type, ...)
VALUES ('h100', 'p5.48xlarge', ...)
ON CONFLICT (gpu_type) DO UPDATE SET
    instance_type = EXCLUDED.instance_type,
    updated_at = NOW();
```

Just edit the fixture file and run `tofu apply`.

## Migration Job Details

The Kubernetes Job:
- **Name**: `db-migration-<hash>` (hash of schema files)
- **Namespace**: `gpu-controlplane`
- **Image**: Uses the same PostgreSQL image as the database
- **Init Container**: Waits for PostgreSQL to be ready
- **Main Container**: Applies schema then fixtures in order
- **Backoff**: Up to 4 retries on failure
- **TTL**: Cleaned up 1 hour after completion

### Viewing Migration Logs

```bash
# Find the migration job
kubectl get jobs -n gpu-controlplane | grep db-migration

# View logs
kubectl logs -n gpu-controlplane job/db-migration-<hash>

# Example output:
# ==========================================
# Database Schema Migration
# ==========================================
# 
# Applying schema files...
#   → 001_users_and_keys.sql
#   → 002_reservations.sql
#   → 003_disks.sql
#   → 004_gpu_types.sql
# 
# Applying fixture data...
#   → 001_initial_gpu_types.sql
# 
# ==========================================
# Migration completed successfully!
# ==========================================
```

## Verification

### Check Schema Was Applied

```bash
# Port-forward to PostgreSQL
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432

# Get password
export PGPASSWORD=$(kubectl get secret -n gpu-controlplane \
  postgres-credentials -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)

# Connect and verify
psql -h localhost -U gpudev -d gpudev -c "\dt"

# Should show:
#  Schema |      Name       | Type  | Owner  
# --------+-----------------+-------+--------
#  public | api_keys        | table | gpudev
#  public | api_users       | table | gpudev
#  public | disks           | table | gpudev
#  public | gpu_types       | table | gpudev
#  public | reservations    | table | gpudev
```

### Check Fixtures Were Applied

```bash
psql -h localhost -U gpudev -d gpudev -c "SELECT gpu_type, instance_type FROM gpu_types ORDER BY gpu_type;"

# Should show GPU types like:
#  gpu_type |  instance_type   
# ----------+------------------
#  a100     | p4d.24xlarge
#  a10g     | g5.12xlarge
#  h100     | p5.48xlarge
#  ...
```

## API Service Changes

The API service **no longer creates schema** on startup. Instead, it:

1. **Verifies** all required tables exist
2. **Fails fast** with a clear error if schema is missing
3. Only creates PGMQ queues (lightweight, safe to create dynamically)

This ensures:
- ✅ Schema changes are visible in version control
- ✅ Schema is applied before API starts
- ✅ No race conditions between multiple API pods
- ✅ Database migrations are auditable

## Troubleshooting

### Migration Job Failed

Check logs:
```bash
kubectl logs -n gpu-controlplane job/db-migration-<hash>
```

Common issues:
- **Syntax error in SQL**: Fix the SQL file and re-apply
- **PostgreSQL not ready**: Job should retry automatically
- **Permission denied**: Check postgres credentials secret

### API Won't Start - "Table does not exist"

The migration job may have failed or not run:

```bash
# Check if migration job exists and completed
kubectl get jobs -n gpu-controlplane | grep db-migration

# If not found or failed, check why:
kubectl describe job -n gpu-controlplane db-migration-<hash>

# Force re-run by applying Terraform
cd terraform-gpu-devservers
tofu apply
```

### Need to Manually Run Migrations

In rare cases, you might want to apply schema manually:

```bash
# Port-forward to PostgreSQL
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432

# Get password
export PGPASSWORD=$(kubectl get secret -n gpu-controlplane \
  postgres-credentials -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)

# Apply schema files manually
for file in database/schema/*.sql; do
  echo "Applying: $(basename $file)"
  psql -h localhost -U gpudev -d gpudev -v ON_ERROR_STOP=1 -f "$file"
done

# Apply fixtures
for file in database/fixtures/*.sql; do
  echo "Applying: $(basename $file)"
  psql -h localhost -U gpudev -d gpudev -v ON_ERROR_STOP=1 -f "$file"
done
```

## Best Practices

1. **Always use idempotent SQL**
   - `CREATE TABLE IF NOT EXISTS`
   - `CREATE INDEX IF NOT EXISTS`
   - `INSERT ... ON CONFLICT`

2. **Number files appropriately**
   - Schema files: 001-099
   - Fixtures: 001-099
   - Keep dependencies in order

3. **Test schema changes locally first**
   - Use a local PostgreSQL instance
   - Run SQL files manually to verify syntax

4. **Keep schema append-only in production**
   - Add new files for changes
   - Avoid modifying existing files after they're deployed

5. **Document complex migrations**
   - Add comments to SQL files
   - Update this README for significant changes

## Migration from Old System

The old system had the API service create the schema on startup. This has been fully replaced.

**Old behavior:**
- API creates tables in `lifespan()` function
- Schema embedded in Python code
- No versioning or audit trail
- Race conditions with multiple pods

**New behavior:**
- Terraform manages schema via Kubernetes Job
- Schema in version-controlled SQL files
- Clear audit trail in Git
- API only verifies schema exists

No data migration is needed - the new schema files create the exact same tables. The first `tofu apply` after this change will:
1. Create the ConfigMaps with schema files
2. Run the migration job (which does nothing if tables exist)
3. Update the API deployment to use the new verification logic

