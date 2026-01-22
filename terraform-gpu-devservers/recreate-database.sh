#!/bin/bash
# Recreate PostgreSQL database with fresh schema
# This will delete existing data and create a clean database with all columns

set -e

NAMESPACE="gpu-controlplane"
BACKUP_DIR="./database-backups/$(date +%Y%m%d-%H%M%S)"

echo "========================================="
echo "PostgreSQL Database Recreation"
echo "========================================="
echo ""
echo "⚠️  WARNING: This will DELETE all existing database data!"
echo "⚠️  A backup will be created, but this is a destructive operation."
echo ""
read -p "Are you sure you want to continue? (type 'yes' to proceed): " CONFIRM

if [ "$CONFIRM" != "yes" ]; then
    echo "❌ Aborted."
    exit 1
fi

echo ""
echo "📋 Step 1: Creating backup directory..."
mkdir -p "$BACKUP_DIR"
echo "✅ Backup directory: $BACKUP_DIR"

echo ""
echo "📊 Step 2: Checking current PostgreSQL status..."
kubectl get statefulset -n $NAMESPACE | grep postgres || echo "No postgres statefulsets found"
kubectl get pvc -n $NAMESPACE | grep postgres || echo "No postgres PVCs found"
kubectl get pod -n $NAMESPACE | grep postgres || echo "No postgres pods found"

echo ""
echo "💾 Step 3: Attempting to backup existing data..."
POSTGRES_POD=$(kubectl get pods -n $NAMESPACE -l app=postgres,role=primary -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "")

if [ -n "$POSTGRES_POD" ]; then
    echo "Found PostgreSQL pod: $POSTGRES_POD"
    echo "Exporting data..."
    
    # Export all databases
    kubectl exec -n $NAMESPACE "$POSTGRES_POD" -- bash -c "
        pg_dumpall -U gpudev > /tmp/backup.sql 2>&1
    " || echo "⚠️  Warning: Database export failed (database may be empty or unreachable)"
    
    # Copy backup to local
    kubectl cp -n $NAMESPACE "$POSTGRES_POD:/tmp/backup.sql" "$BACKUP_DIR/full_backup.sql" 2>/dev/null || echo "⚠️  Could not copy backup"
    
    # Export individual tables
    for table in reservations disks users ssh_public_keys gpu_types ssh_domain_mappings alb_target_groups; do
        echo "  → Backing up table: $table"
        kubectl exec -n $NAMESPACE "$POSTGRES_POD" -- bash -c "
            psql -U gpudev -d gpudev -c \"\\copy $table TO '/tmp/${table}.csv' WITH CSV HEADER\" 2>&1 || true
        " && kubectl cp -n $NAMESPACE "$POSTGRES_POD:/tmp/${table}.csv" "$BACKUP_DIR/${table}.csv" 2>/dev/null || true
    done
    
    echo "✅ Backup completed (check $BACKUP_DIR for files)"
else
    echo "⚠️  No PostgreSQL pod found - skipping backup"
fi

echo ""
echo "🗑️  Step 4: Deleting PostgreSQL resources..."

# Delete the schema migration job first (if it exists)
echo "  → Deleting schema migration job..."
kubectl delete job database-schema-migration -n $NAMESPACE --ignore-not-found=true

# Delete PostgreSQL StatefulSets
echo "  → Deleting StatefulSets..."
kubectl delete statefulset postgres-primary -n $NAMESPACE --ignore-not-found=true
kubectl delete statefulset postgres-replica -n $NAMESPACE --ignore-not-found=true

# Wait for pods to terminate
echo "  → Waiting for pods to terminate..."
kubectl wait --for=delete pod -l app=postgres -n $NAMESPACE --timeout=120s 2>/dev/null || echo "    (pods already gone)"

# Delete Services
echo "  → Deleting Services..."
kubectl delete service postgres-primary -n $NAMESPACE --ignore-not-found=true
kubectl delete service postgres-replica -n $NAMESPACE --ignore-not-found=true

# Delete PVCs (this will delete the data!)
echo "  → Deleting PersistentVolumeClaims..."
kubectl delete pvc postgres-primary-data -n $NAMESPACE --ignore-not-found=true
kubectl delete pvc postgres-replica-data -n $NAMESPACE --ignore-not-found=true

# Wait for PVCs to be deleted
echo "  → Waiting for PVCs to be fully deleted..."
sleep 10
kubectl get pvc -n $NAMESPACE | grep postgres && echo "    (still deleting...)" && sleep 10 || true

echo "✅ PostgreSQL resources deleted"

echo ""
echo "🔄 Step 5: Recreating PostgreSQL with fresh schema..."
echo ""
echo "Running tofu apply to recreate resources..."

# Apply tofu to recreate the PostgreSQL resources
tofu apply -auto-approve \
    -target=kubernetes_persistent_volume_claim.postgres_primary_pvc \
    -target=kubernetes_persistent_volume_claim.postgres_replica_pvc \
    -target=kubernetes_stateful_set.postgres_primary \
    -target=kubernetes_stateful_set.postgres_replica \
    -target=kubernetes_service.postgres_primary \
    -target=kubernetes_service.postgres_replica \
    -target=kubernetes_job.database_schema_migration

echo ""
echo "⏳ Step 6: Waiting for PostgreSQL to be ready..."

# Wait for primary to be ready
echo "  → Waiting for postgres-primary StatefulSet..."
kubectl rollout status statefulset/postgres-primary -n $NAMESPACE --timeout=300s

# Wait for pod to be running
echo "  → Waiting for postgres-primary pod..."
kubectl wait --for=condition=ready pod -l app=postgres,role=primary -n $NAMESPACE --timeout=300s

# Wait a bit for PostgreSQL to fully initialize
echo "  → Waiting for PostgreSQL service to initialize..."
sleep 10

echo "✅ PostgreSQL is running"

echo ""
echo "⏳ Step 7: Waiting for schema migration job to complete..."

# Wait for the migration job to complete
kubectl wait --for=condition=complete job/database-schema-migration -n $NAMESPACE --timeout=300s || {
    echo "❌ Schema migration job failed or timed out"
    echo ""
    echo "Job status:"
    kubectl get job database-schema-migration -n $NAMESPACE
    echo ""
    echo "Job logs:"
    kubectl logs -n $NAMESPACE job/database-schema-migration --tail=100
    exit 1
}

echo "✅ Schema migration completed successfully"

echo ""
echo "📊 Step 8: Verifying new database..."

POSTGRES_POD=$(kubectl get pods -n $NAMESPACE -l app=postgres,role=primary -o jsonpath='{.items[0].metadata.name}')
echo "PostgreSQL pod: $POSTGRES_POD"

echo ""
echo "Checking tables..."
kubectl exec -n $NAMESPACE "$POSTGRES_POD" -- psql -U gpudev -d gpudev -c "\dt" || {
    echo "❌ Could not list tables"
    exit 1
}

echo ""
echo "Checking disk_size column in disks table..."
kubectl exec -n $NAMESPACE "$POSTGRES_POD" -- psql -U gpudev -d gpudev -c "\d disks" | grep disk_size && {
    echo "✅ disk_size column exists!"
} || {
    echo "❌ disk_size column NOT found!"
    exit 1
}

echo ""
echo "Checking PGMQ extension..."
kubectl exec -n $NAMESPACE "$POSTGRES_POD" -- psql -U gpudev -d gpudev -c "SELECT extname, extversion FROM pg_extension WHERE extname = 'pgmq';" || {
    echo "⚠️  PGMQ extension check failed"
}

echo ""
echo "========================================="
echo "✅ Database Recreation Complete!"
echo "========================================="
echo ""
echo "📁 Backup Location: $BACKUP_DIR"
echo ""
echo "📊 Database Status:"
kubectl get statefulset,pvc,pod,svc -n $NAMESPACE | grep postgres
echo ""
echo "🔧 Next Steps:"
echo ""
echo "Option 1: Manual restart (quick):"
echo "  kubectl rollout restart deployment/api-service -n gpu-controlplane"
echo "  kubectl rollout restart deployment/reservation-processor -n gpu-controlplane"
echo ""
echo "Option 2: Re-run tofu (recommended, ensures proper dependencies):"
echo "  tofu apply -target=kubernetes_deployment.api_service \\"
echo "            -target=kubernetes_deployment.reservation_processor"
echo ""
echo "Then test:"
echo "  gpu-dev reserve --gpu-type t4 --gpu-count 1"
echo ""
echo "📝 Note:"
echo "  - All existing reservations, disks, and users have been deleted"
echo "  - Database now has complete schema with all columns"
echo "  - PGMQ queues created by schema (007_pgmq_queues.sql)"
echo ""
echo "See SCHEMA_IMPROVEMENTS.md for details on the new schema-first approach."

