# Database Schema Migration - Implementation Summary

## What Was Changed

### ✅ Files Created

1. **Schema Files** (4 files)
   - `database/schema/001_users_and_keys.sql` - API users and authentication
   - `database/schema/002_reservations.sql` - GPU reservations/jobs
   - `database/schema/003_disks.sql` - Persistent disk management
   - `database/schema/004_gpu_types.sql` - GPU configuration

2. **Fixture Files** (1 file)
   - `database/fixtures/001_initial_gpu_types.sql` - Default GPU types (h100, a100, t4, etc.)

3. **Documentation** (2 files)
   - `database/README.md` - Complete guide to schema management
   - `database/MIGRATION_SUMMARY.md` - This file

### ✅ Files Modified

1. **terraform-gpu-devservers/kubernetes.tf**
   - Added `kubernetes_config_map.database_schema` - Loads schema SQL files
   - Added `kubernetes_config_map.database_fixtures` - Loads fixture SQL files
   - Added `kubernetes_job.database_schema_migration` - Applies schema during `tofu apply`

2. **terraform-gpu-devservers/api-service.tf**
   - Updated `kubernetes_deployment.api_service` dependencies to wait for migration job

3. **terraform-gpu-devservers/api-service/app/main.py**
   - Replaced schema creation logic with schema verification
   - API now fails fast if schema is missing
   - Removed ~270 lines of DDL from Python code

## Benefits

### Before (Fragile)
❌ Schema embedded in API Python code  
❌ Created on every API startup  
❌ Race conditions with multiple pods  
❌ No version control visibility  
❌ Hard to review/audit changes  
❌ Tightly coupled API and schema  

### After (Maintainable)
✅ Schema in version-controlled SQL files  
✅ Applied once during infrastructure deployment  
✅ No race conditions  
✅ Clear audit trail in Git  
✅ Easy to review in PRs  
✅ Clean separation of concerns  
✅ Automatic re-migration on schema changes  

## How to Use

### First-Time Deployment

When you first run `tofu apply` after these changes:

```bash
cd terraform-gpu-devservers
tofu plan   # Review changes
tofu apply  # Apply infrastructure

# What happens:
# 1. ConfigMaps created with schema/fixture files
# 2. Migration job runs (idempotent - safe if tables exist)
# 3. API deployment updated with new verification logic
# 4. API pods start and verify schema exists
```

### Making Schema Changes

#### Example: Add a new table

1. Create a new schema file:
```bash
# database/schema/005_api_logs.sql
CREATE TABLE IF NOT EXISTS api_logs (
    log_id BIGSERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES api_users(user_id),
    endpoint VARCHAR(255),
    status_code INTEGER,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_logs_user_id
    ON api_logs(user_id);

CREATE INDEX IF NOT EXISTS idx_api_logs_created_at
    ON api_logs(created_at DESC);
```

2. Apply changes:
```bash
tofu apply
```

That's it! The migration job will automatically run and apply the new schema.

#### Example: Update GPU types

1. Edit the fixture file:
```bash
vim database/fixtures/001_initial_gpu_types.sql

# Add or modify entries:
INSERT INTO gpu_types (...)
VALUES ('h200', 'p5e.48xlarge', ...)
ON CONFLICT (gpu_type) DO UPDATE SET ...
```

2. Apply changes:
```bash
tofu apply
```

### Verifying Schema

```bash
# View migration job logs
kubectl logs -n gpu-controlplane -l app=database-migration --tail=100

# Check tables
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432
export PGPASSWORD=$(kubectl get secret -n gpu-controlplane postgres-credentials -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
psql -h localhost -U gpudev -d gpudev -c "\dt"
```

## Rollback Plan

If you need to roll back to the old system:

1. Revert changes to `api-service/app/main.py` (restore schema creation in `lifespan()`)
2. Remove migration job from `kubernetes.tf`
3. Run `tofu apply`

However, the new system is **backward compatible** - the schema files create the exact same tables as the old Python code, so there's no data migration needed.

## Technical Details

### Migration Job Behavior

- **Idempotent**: Uses `CREATE TABLE IF NOT EXISTS`, safe to run multiple times
- **Automatic**: Runs during `tofu apply`, before API starts
- **Hash-based**: Job name includes hash of schema files, so changes trigger re-run
- **Self-cleaning**: Jobs are cleaned up 1 hour after completion

### API Startup Checks

The API now verifies these tables exist on startup:
- `api_users`
- `api_keys`
- `reservations`
- `disks`
- `gpu_types`

If any table is missing, the API fails with a clear error message directing you to check the migration job.

### PGMQ Queues

PGMQ queues are still created by the API (not in schema files) because:
- They're lightweight metadata, not business data
- Safe to create dynamically
- May need per-environment customization

## FAQ

**Q: What happens to existing databases?**  
A: The schema files are idempotent - they use `CREATE TABLE IF NOT EXISTS`. Existing tables are not modified.

**Q: Do I need to manually migrate data?**  
A: No. The new SQL files create the exact same schema as the old Python code.

**Q: Can I still use populate_gpu_types.py?**  
A: It will still work, but it's no longer needed. GPU types are now populated via the fixture file during `tofu apply`.

**Q: What if the migration job fails?**  
A: The API won't start (by design). Check the job logs: `kubectl logs -n gpu-controlplane -l app=database-migration`

**Q: Can I preview what SQL will be applied?**  
A: Yes, just look at the files in `database/schema/` and `database/fixtures/`. They're plain SQL.

**Q: How do I know the migration ran?**  
A: Check for the migration job: `kubectl get jobs -n gpu-controlplane | grep db-migration`

## Next Steps

1. **Review the changes**: Look at the SQL files in `database/`
2. **Test in development**: Run `tofu plan` and `tofu apply`
3. **Verify migration**: Check job logs and table existence
4. **Update documentation**: Add any project-specific notes to `database/README.md`

## Files Reference

- **Schema files**: `terraform-gpu-devservers/database/schema/*.sql`
- **Fixture files**: `terraform-gpu-devservers/database/fixtures/*.sql`
- **Documentation**: `terraform-gpu-devservers/database/README.md`
- **Terraform config**: `terraform-gpu-devservers/kubernetes.tf` (lines 328+)
- **API verification**: `terraform-gpu-devservers/api-service/app/main.py` (lines 155-199)

