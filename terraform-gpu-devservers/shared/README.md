# Shared Utilities

Shared Python utilities used across multiple services in the GPU dev infrastructure.

**✅ Migrated to PostgreSQL** - All DynamoDB dependencies have been replaced with PostgreSQL queries.

## Modules

### db_pool.py
**PostgreSQL connection pooling with automatic transaction management.**

This module provides a thread-safe connection pool for PostgreSQL with:
- Connection pooling (1-20 connections by default)
- Automatic transaction management (commit/rollback)
- Safe connection cleanup (no leaks)
- Context managers for clean code

**Key Functions:**
- `get_db_cursor()` - **RECOMMENDED** - Context manager that provides a cursor with automatic transaction handling
- `get_db_transaction()` - Context manager for manual transaction control
- `get_db_connection()` - Context manager for direct connection access
- `init_connection_pool()` - Initialize pool with custom settings (optional)
- `close_connection_pool()` - Shutdown pool (for application cleanup)
- `get_pool_stats()` - Get pool statistics for monitoring

**Quick Example:**
```python
from shared.db_pool import get_db_cursor

# Simple write
with get_db_cursor() as cur:
    cur.execute("INSERT INTO users (id, name) VALUES (%s, %s)", (1, "Alice"))
    # Auto-commits on success, auto-rollback on exception

# Simple read
with get_db_cursor(readonly=True) as cur:
    cur.execute("SELECT * FROM users WHERE id = %s", (1,))
    user = cur.fetchone()
```

**📖 See [DB_USAGE.md](./DB_USAGE.md) for complete documentation and examples.**

**⚠️ Important**: Each call to `get_db_cursor()` gets a **different connection** with a **separate transaction**. Nested context managers do NOT share the same transaction. See [NESTED_CONTEXT_MANAGERS.md](./NESTED_CONTEXT_MANAGERS.md) for details.

### k8s_client.py
Kubernetes client setup with EKS authentication using IRSA (IAM Roles for Service Accounts).

**Key Functions:**
- `setup_kubernetes_client()` - Creates authenticated K8s API client
- `get_bearer_token()` - Generates EKS bearer token for authentication

### k8s_resource_tracker.py
Real-time GPU resource tracking via Kubernetes API.

**Key Class:**
- `K8sGPUTracker` - Tracks GPU capacity, usage, and availability across cluster nodes

### disk_reconciler.py
**Disk state reconciliation between AWS EBS and PostgreSQL database.**

Ensures database accurately reflects AWS EBS volume state by:
- Syncing volume metadata (size, attachment status, snapshot counts)
- Detecting and importing orphaned AWS volumes
- Handling volume deletions and temporary detachments
- Preserving audit trails (reservation associations)
- Using atomic transactions and exponential backoff for reliability

**Key Functions:**
- `reconcile_all_disks(ec2_client)` - Main reconciliation loop (called by availability-updater service)
- `get_all_gpudev_volumes(ec2_client)` - Fetches all EBS volumes with gpu-dev tags from AWS
- `sync_volume_to_db(aws_vol, db_disk, ec2_client)` - Syncs AWS volume state to DB record
- `import_volume_to_db(aws_vol, ec2_client)` - Creates DB record for orphaned AWS volume
- `get_snapshot_info(ec2_client, volume_id, user_id)` - Retrieves snapshot metadata from AWS
- `ensure_utc(dt)` - Normalizes datetimes to timezone-aware UTC (per project standards)

**Features:**
- Handles AWS API rate limiting with exponential backoff + jitter
- Uses atomic database transactions for each volume (prevents race conditions)
- Distinguishes volume replacement from conflicts
- Detects duplicate database records
- Timezone-aware timestamp comparisons
- Supports EBS Multi-Attach volumes

**Used By:** `availability-updater-service` (runs every 5 minutes)

### snapshot_utils.py
EBS snapshot management utilities for persistent disk backups.

**Key Functions:**
- `safe_create_snapshot()` - Creates snapshots with duplicate detection
- `get_latest_snapshot()` - Retrieves most recent snapshot for a user
- `cleanup_old_snapshots()` - Removes old snapshots based on retention policy
- `capture_disk_contents()` - Captures disk file listing to S3
- `update_disk_snapshot_completed()` - Updates PostgreSQL when snapshot completes

### dns_utils.py
Route53 DNS record management for reservation subdomains.

**Key Functions:**
- `generate_unique_name()` - Generates unique subdomain names (e.g., "grumpy_bear")
- `create_dns_record()` - Creates DNS CNAME records
- `delete_dns_record()` - Removes DNS records
- `store_domain_mapping()` - Stores domain mappings in PostgreSQL
- `delete_domain_mapping()` - Removes domain mappings from PostgreSQL

### alb_utils.py
ALB/NLB target group and listener rule management.

**Key Functions:**
- `create_jupyter_target_group()` - Creates ALB target group for Jupyter access
- `create_alb_listener_rule()` - Creates hostname-based routing rules
- `store_alb_mapping()` - Stores ALB mappings in PostgreSQL
- `delete_alb_mapping()` - Cleans up ALB resources
- `get_instance_id_from_pod()` - Retrieves EC2 instance ID from K8s pod

## Usage

These utilities are imported by:
- **Reservation Processor Service** - Main reservation processing logic
- **Lambda Functions** (legacy) - Expiry handler, availability updater
- **API Service** (future) - May use some utilities for direct operations

## Dependencies

Common dependencies across modules:
- `boto3` - AWS SDK for EC2, ELBv2, Route53, S3
- `kubernetes==28.1.0` - Kubernetes Python client
- `psycopg2-binary>=2.9.9` - PostgreSQL client (connection pooling)
- `urllib3<2.0` - HTTP client (K8s dependency)

## Migration Notes

These utilities were originally in `lambda/shared/` and are now shared across:
1. Kubernetes-based services (reservation processor)
2. Remaining Lambda functions (until fully migrated)

When all services are migrated to Kubernetes, Lambda-specific code can be removed.

---

## 📚 Documentation Index

### Core Documentation
- **[README.md](./README.md)** - This file, overview of shared utilities
- **[DB_USAGE.md](./DB_USAGE.md)** - Complete guide to using the database connection pool

### Security Best Practices
- **[SQL_SECURITY_PATTERNS.md](../SQL_SECURITY_PATTERNS.md)** - SQL query construction patterns and injection prevention

### Important Concepts
- **[NESTED_CONTEXT_MANAGERS.md](./NESTED_CONTEXT_MANAGERS.md)** - ⚠️ **Must Read**: How nested `get_db_cursor()` calls behave

