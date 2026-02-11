#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform-gpu-devservers"

echo "=== Setting up local k3d development environment ==="

# Detect architecture for correct CPU type label
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    LOCAL_GPU_TYPE="cpu-arm"
else
    LOCAL_GPU_TYPE="cpu-x86"
fi
echo "Detected architecture: $ARCH -> GPU type: $LOCAL_GPU_TYPE"

# 1. Create k3d cluster with port mapping (8000 -> traefik loadbalancer)
if k3d cluster list | grep -q gpu-dev-local; then
    echo "Cluster gpu-dev-local already exists, skipping creation"
else
    echo ""
    echo "Creating k3d cluster..."
    k3d cluster create gpu-dev-local \
        -p "8000:80@loadbalancer" \
        --agents 0
fi

# 2. Label node for CPU pods (use detected architecture)
echo ""
echo "Labeling node for CPU pods ($LOCAL_GPU_TYPE)..."
NODE=$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}')
kubectl label node "$NODE" GpuType="$LOCAL_GPU_TYPE" --overwrite
kubectl label node "$NODE" NodeType=cpu --overwrite

# 3. Build and load images
echo ""
"$SCRIPT_DIR/build-images.sh"

# 4. Apply namespaces
echo ""
echo "Applying namespaces..."
kubectl apply -f "$SCRIPT_DIR/manifests/namespaces.yaml"

# 5. Apply postgres
echo ""
echo "Applying PostgreSQL..."
kubectl apply -f "$SCRIPT_DIR/manifests/postgres.yaml"

# Wait for postgres to be ready
echo "Waiting for PostgreSQL to be ready..."
kubectl wait --for=condition=ready pod -l app=postgres,role=primary \
    -n gpu-controlplane --timeout=120s

# 6. Create ConfigMaps from SQL files for schema migration
echo ""
echo "Creating schema ConfigMaps from SQL files..."
kubectl create configmap database-schema \
    -n gpu-controlplane \
    --from-file="$TF_DIR/database/schema/" \
    --dry-run=client -o yaml | kubectl apply -f -

kubectl create configmap database-fixtures \
    -n gpu-controlplane \
    --from-file="$TF_DIR/database/fixtures/" \
    --dry-run=client -o yaml | kubectl apply -f -

# 7. Run schema migration
echo ""
echo "Running schema migration..."
# Delete previous job if it exists (jobs are immutable)
kubectl delete job schema-migration -n gpu-controlplane --ignore-not-found
kubectl apply -f "$SCRIPT_DIR/manifests/schema-job.yaml"

echo "Waiting for schema migration to complete..."
kubectl wait --for=condition=complete job/schema-migration \
    -n gpu-controlplane --timeout=120s

# 8. Clean up database for local dev (only keep the local CPU type active)
echo ""
echo "Configuring database for local dev (only $LOCAL_GPU_TYPE active)..."
kubectl exec -n gpu-controlplane postgres-primary-0 -c postgres -- \
    psql -U gpudev -d gpudev -c "
        UPDATE gpu_types SET is_active = false WHERE gpu_type != '$LOCAL_GPU_TYPE';
        UPDATE gpu_types SET
            available_gpus = 3,
            total_cluster_gpus = 3,
            full_nodes_available = 1,
            running_instances = 1,
            desired_capacity = 1,
            last_availability_update = NOW(),
            last_availability_updated_by = 'local-setup'
        WHERE gpu_type = '$LOCAL_GPU_TYPE';
    "

# 9. Apply RBAC
echo ""
echo "Applying RBAC..."
kubectl apply -f "$SCRIPT_DIR/manifests/rbac.yaml"

# 10. Deploy API service
echo ""
echo "Deploying API service..."
kubectl apply -f "$SCRIPT_DIR/manifests/api-service.yaml"

# 11. Deploy processor
echo ""
echo "Deploying reservation processor..."
kubectl apply -f "$SCRIPT_DIR/manifests/processor.yaml"

# 12. Apply ingress
echo ""
echo "Applying ingress..."
kubectl apply -f "$SCRIPT_DIR/manifests/ingress.yaml"

# 13. Wait for API service to be ready
echo ""
echo "Waiting for API service to be ready..."
kubectl wait --for=condition=ready pod -l app=api-service \
    -n gpu-controlplane --timeout=120s

echo ""
echo "=== Local environment ready! ==="
echo "API: http://localhost:8000"
echo "GPU type: $LOCAL_GPU_TYPE (arch: $ARCH)"
echo ""
echo "Next steps:"
echo "  ./terraform-gpu-devservers/switch-to.sh local"
echo "  gpu-dev login"
echo "  gpu-dev avail"
echo "  gpu-dev reserve --gpu-type $LOCAL_GPU_TYPE --gpus 0 --hours 1 --no-persist"
