# Database Recreation Guide

## ⚠️ IMPORTANT: This Project Uses OpenTofu (tofu), NOT Terraform

**All commands in this guide use `tofu`, NEVER `terraform`.**

See the [main README](reservation-processor-service/README.md) for detailed explanation of why this matters.

## Overview

This guide explains how to fully recreate the PostgreSQL database to ensure all columns from the schema files are properly created.

## When to Use This

Use database recreation when:
- ✅ Schema migrations are missing columns (like `disk_size`)
- ✅ You want a clean database with all schema definitions
- ✅ Database structure is inconsistent or corrupted
- ✅ Testing with a fresh state

**⚠️ WARNING:** This is a **destructive operation** that will delete all existing data!

## Three-Step Process

### Step 1: Check Current Status

First, see what you currently have and what will be deleted:

```bash
./check-database-status.sh
```

This shows:
- Current PostgreSQL resources (StatefulSets, PVCs, Pods, Services)
- Table counts (reservations, disks, users, etc.)
- Schema info (checks if disk_size column exists)
- Active reservations
- What will be deleted

### Step 2: Recreate Database

Run the recreation script:

```bash
./recreate-database.sh
```

**What it does:**

1. **Backup Phase** (automatic)
   - Creates backup directory: `./database-backups/YYYYMMDD-HHMMSS/`
   - Exports full database dump: `full_backup.sql`
   - Exports individual table CSVs: `reservations.csv`, `disks.csv`, etc.

2. **Deletion Phase**
   - Deletes schema migration job
   - Deletes PostgreSQL StatefulSets (primary & replica)
   - Deletes PostgreSQL Services
   - **Deletes PVCs** (⚠️ ALL DATA DELETED)

3. **Recreation Phase**
   - Runs `tofu apply` to recreate resources
   - Creates fresh PVCs with new EBS volumes
   - Deploys new PostgreSQL StatefulSets
   - Waits for pods to be ready

4. **Schema Phase**
   - Runs schema migration job
   - Applies all schema files in order:
     - `001_users_and_keys.sql`
     - `002_reservations.sql`
     - `003_disks.sql` (includes `disk_size` column!)
     - `004_gpu_types.sql`
     - `005_domain_mappings.sql`
     - `006_alb_target_groups.sql`
   - Applies fixture data

5. **Verification Phase**
   - Lists all tables
   - Confirms `disk_size` column exists
   - Checks PGMQ extension

**Duration:** ~5-10 minutes

### Step 3: Restore Data (Optional)

If you want to restore from the backup:

```bash
# List available backups
./restore-database-backup.sh

# Restore from specific backup
./restore-database-backup.sh database-backups/20260121-183000/
```

**Note:** Only restore if you need the old data. For a fresh start, skip this step.

## Post-Recreation Steps

After recreation, you need to restart services OR re-run OpenTofu:

### Option 1: Manual Restart (Quick)
```bash
# Restart API service
kubectl rollout restart deployment/api-service -n gpu-controlplane

# Restart reservation processor
kubectl rollout restart deployment/reservation-processor -n gpu-controlplane

# Watch them restart
kubectl get pods -n gpu-controlplane -w
```

### Option 2: Re-run OpenTofu (Recommended)
```bash
# This will automatically:
# 1. Re-run schema migration job (creates PGMQ queues)
# 2. Wait for job to complete
# 3. Restart API service (waits for rollout)
# 4. Restart reservation processor (waits for rollout)
tofu apply -target=kubernetes_job.database_schema_migration \
          -target=kubernetes_deployment.api_service \
          -target=kubernetes_deployment.reservation_processor
```

**Note:** As of the latest changes, PGMQ queues are created by the schema migration (see `database/schema/007_pgmq_queues.sql`), not by the API service at runtime.

## What Changes

### Before Recreation
```sql
-- disks table is MISSING disk_size column
CREATE TABLE disks (
    disk_id UUID PRIMARY KEY,
    disk_name TEXT NOT NULL,
    ...
    -- disk_size column MISSING!
);
```

Result: Errors when trying to update disk_size:
```
ERROR: column "disk_size" of relation "disks" does not exist
```

### After Recreation
```sql
-- disks table now HAS disk_size column
CREATE TABLE disks (
    disk_id UUID PRIMARY KEY,
    disk_name TEXT NOT NULL,
    size_gb INTEGER,
    disk_size TEXT,  -- ✅ NOW PRESENT!
    ...
);
```

Result: No more errors! disk_size updates work correctly.

## Backup Safety

### Automatic Backups
- Created in `./database-backups/<timestamp>/`
- Includes:
  - `full_backup.sql` - Complete database dump
  - `<table>.csv` - Individual table exports

### Manual Backups (optional)
Before running recreation, you can create additional backups:

```bash
# Manual backup directory
mkdir -p manual-backup

# Get postgres pod name
POSTGRES_POD=$(kubectl get pods -n gpu-controlplane -l app=postgres,role=primary -o jsonpath='{.items[0].metadata.name}')

# Export full database
kubectl exec -n gpu-controlplane "$POSTGRES_POD" -- \
    pg_dumpall -U gpudev > manual-backup/full_backup.sql

# Export specific table
kubectl exec -n gpu-controlplane "$POSTGRES_POD" -- \
    psql -U gpudev -d gpudev -c "\copy reservations TO STDOUT WITH CSV HEADER" \
    > manual-backup/reservations.csv
```

## Rollback Plan

If something goes wrong:

1. **Check the backup was created:**
   ```bash
   ls -lh database-backups/
   ```

2. **Restore from backup:**
   ```bash
   ./restore-database-backup.sh database-backups/<timestamp>/
   ```

3. **If restore fails, check logs:**
   ```bash
   kubectl logs -n gpu-controlplane job/database-schema-migration
   ```

## Testing After Recreation

1. **Check tables exist:**
   ```bash
   kubectl exec -n gpu-controlplane $(kubectl get pods -n gpu-controlplane -l app=postgres,role=primary -o jsonpath='{.items[0].metadata.name}') -- \
       psql -U gpudev -d gpudev -c "\dt"
   ```

2. **Verify disk_size column:**
   ```bash
   kubectl exec -n gpu-controlplane $(kubectl get pods -n gpu-controlplane -l app=postgres,role=primary -o jsonpath='{.items[0].metadata.name}') -- \
       psql -U gpudev -d gpudev -c "\d disks" | grep disk_size
   ```

3. **Test creating a reservation:**
   ```bash
   gpu-dev reserve --gpu-type t4 --gpu-count 1
   ```

4. **Test canceling a reservation:**
   ```bash
   gpu-dev cancel <reservation-id>
   # Should complete without "disk_size column does not exist" error
   ```

## Troubleshooting

### Schema Migration Job Fails

Check logs:
```bash
kubectl logs -n gpu-controlplane job/database-schema-migration
```

Re-run migration manually:
```bash
kubectl delete job database-schema-migration -n gpu-controlplane
tofu apply -target=kubernetes_job.database_schema_migration
```

### PostgreSQL Pod Won't Start

Check pod status:
```bash
kubectl describe pod -n gpu-controlplane -l app=postgres,role=primary
```

Check logs:
```bash
kubectl logs -n gpu-controlplane -l app=postgres,role=primary
```

### PVC Won't Delete

Force delete:
```bash
kubectl patch pvc postgres-primary-data -n gpu-controlplane -p '{"metadata":{"finalizers":null}}'
kubectl delete pvc postgres-primary-data -n gpu-controlplane --force --grace-period=0
```

## Files Involved

### Schema Files (Applied in Order)
- `database/schema/001_users_and_keys.sql` - Users and SSH keys tables
- `database/schema/002_reservations.sql` - Reservations table
- `database/schema/003_disks.sql` - **Disks table with disk_size column**
- `database/schema/004_gpu_types.sql` - GPU types and availability
- `database/schema/005_domain_mappings.sql` - DNS domain mappings
- `database/schema/006_alb_target_groups.sql` - ALB target group mappings

### OpenTofu Resources
- `kubernetes.tf` - PostgreSQL StatefulSets, Services, PVCs, ConfigMaps
- `kubernetes_job.database_schema_migration` - Applies schema files

### Scripts
- `check-database-status.sh` - Preview what will be deleted
- `recreate-database.sh` - Main recreation script
- `restore-database-backup.sh` - Restore from backup

## FAQ

**Q: Will this affect running reservations?**
A: Yes. All reservation data will be deleted. Active pods will continue running but won't be tracked in the database.

**Q: How long does it take?**
A: ~5-10 minutes total (backup, deletion, recreation, schema application).

**Q: Can I skip the backup?**
A: Not recommended, but you can modify the script to skip backup if you're sure you don't need it.

**Q: What if I want to keep some data?**
A: Use the restore script after recreation to restore specific tables from the CSV backups.

**Q: Will GPU nodes be affected?**
A: No. GPU nodes and running workloads are not affected. Only the control plane database is recreated.

**Q: Do I need to re-run the timeout fixes after this?**
A: No. Code changes are separate from database. Just restart the services after recreation.

## Summary

```bash
# 1. Check what you have
./check-database-status.sh

# 2. Recreate database (creates backup automatically)
./recreate-database.sh

# 3. Restart services
kubectl rollout restart deployment/api-service -n gpu-controlplane
kubectl rollout restart deployment/reservation-processor -n gpu-controlplane

# 4. Test
gpu-dev reserve --gpu-type t4 --gpu-count 1

# (Optional) Restore data if needed
./restore-database-backup.sh database-backups/<timestamp>/
```

✅ Result: Fresh database with all columns, no more schema errors!

