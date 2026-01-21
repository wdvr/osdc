# Database Migrations

This directory contains database migration scripts for the GPU Dev platform.

## GPU Types Migration

### Overview

The `populate_gpu_types.py` script populates the `gpu_types` table with GPU configuration data. This table stores:
- GPU type identifiers (t4, h100, h200, etc.)
- Instance type mappings (g4dn.12xlarge, p5.48xlarge, etc.)
- Resource specifications (CPUs, memory, max GPUs per node)
- Cluster capacity information (total GPUs available)

### Usage

#### 1. Port-forward to Postgres (from your local machine)

```bash
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432
```

#### 2. Get the Postgres password

```bash
export POSTGRES_PASSWORD=$(kubectl get secret -n gpu-controlplane \
  postgres-credentials -o jsonpath='{.data.POSTGRES_PASSWORD}' | base64 -d)
```

#### 3. Run the migration

```bash
# Dry run (see what would be changed without making changes)
cd terraform-gpu-devservers/migrations
python populate_gpu_types.py --dry-run

# Apply the migration
python populate_gpu_types.py

# Verify the migration
python populate_gpu_types.py --verify
```

### What it does

The script will:
1. Connect to the Postgres database
2. Check for existing GPU types
3. Insert new GPU types or update existing ones
4. Display a summary of changes

### Example Output

```
Connecting to database...

Found 0 existing GPU types in database

Inserting: t4
  Instance: g4dn.12xlarge
  Max GPUs per node: 4
  Total cluster GPUs: 8
  CPUs: 48, Memory: 192GB
  Description: NVIDIA T4 - Entry-level GPU for inference and light training

Inserting: h100
  Instance: p5.48xlarge
  Max GPUs per node: 8
  Total cluster GPUs: 16
  CPUs: 192, Memory: 2048GB
  Description: NVIDIA H100 - Top-tier GPU for AI training and HPC

...

============================================================
MIGRATION SUMMARY:
  Inserted: 10
  Updated:  0
  Total:    10
============================================================

Final GPU Types Configuration:
  ✓ a100         → p4d.24xlarge         (16 GPUs, 8 per node)
  ✓ a10g         → g5.12xlarge          ( 4 GPUs, 4 per node)
  ✓ b200         → p6-b200.48xlarge     (16 GPUs, 8 per node)
  ✓ cpu-arm      → c7g.8xlarge          ( 0 GPUs, 0 per node)
  ✓ cpu-x86      → c7i.8xlarge          ( 0 GPUs, 0 per node)
  ✓ h100         → p5.48xlarge          (16 GPUs, 8 per node)
  ✓ h200         → p5e.48xlarge         (16 GPUs, 8 per node)
  ✓ l4           → g6.12xlarge          ( 4 GPUs, 4 per node)
  ✓ t4           → g4dn.12xlarge        ( 8 GPUs, 4 per node)
  ✓ t4-small     → g4dn.2xlarge         ( 1 GPUs, 1 per node)
```

### Customizing GPU Configuration

To add or modify GPU types:

1. Edit the `GPU_TYPES_CONFIG` dictionary in `populate_gpu_types.py`
2. Run the migration script to update the database
3. The API service will automatically use the updated configuration

### Database Schema

The `gpu_types` table is automatically created by the API service on startup:

```sql
CREATE TABLE gpu_types (
    gpu_type VARCHAR(50) PRIMARY KEY,
    instance_type VARCHAR(100) NOT NULL,
    max_gpus INTEGER NOT NULL,
    cpus INTEGER NOT NULL,
    memory_gb INTEGER NOT NULL,
    total_cluster_gpus INTEGER DEFAULT 0,
    max_per_node INTEGER,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    description TEXT
);
```

### Impact

After running this migration:
- ✅ API `/v1/gpu/availability` endpoint reads from database
- ✅ API `/v1/cluster/status` endpoint reads from database
- ✅ CLI `gpu-dev avail` command shows correct availability
- ✅ No more hardcoded GPU configs in multiple places
- ✅ Easy to add/modify GPU types without code changes

### Troubleshooting

**Error: gpu_types table does not exist**
- Make sure the API service has been deployed and started at least once
- The table is created automatically on API service startup

**Connection refused**
- Ensure kubectl port-forward is running
- Check that you're using the correct port (5432)

**Authentication failed**
- Verify POSTGRES_PASSWORD is set correctly
- Try getting the password again from Kubernetes

**Need to run from inside the cluster?**
```bash
# Get database connection info from the API pod
kubectl exec -n gpu-controlplane deployment/api-service -- env | grep POSTGRES

# Set environment variables and run
export POSTGRES_HOST=postgres-primary.gpu-controlplane.svc.cluster.local
export POSTGRES_PORT=5432
export POSTGRES_USER=gpudev
export POSTGRES_DB=gpudev
export POSTGRES_PASSWORD=<from secret>

python populate_gpu_types.py
```

