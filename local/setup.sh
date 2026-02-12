#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform-gpu-devservers"
CHART_DIR="$ROOT_DIR/charts/gpu-dev-server"

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

# 4. Deploy via Helm chart
echo ""
echo "Installing GPU Dev Server chart..."
helm upgrade --install gpu-dev-server "$CHART_DIR" \
    -f "$CHART_DIR/values-local.yaml" \
    -n gpu-controlplane --create-namespace \
    --wait --timeout 300s

# 5. Wait for PostgreSQL to be ready (Helm --wait handles deployments but
#    StatefulSets may still need a moment for pg_isready)
echo ""
echo "Waiting for PostgreSQL to be ready..."
kubectl wait --for=condition=ready pod -l app=postgres,role=primary \
    -n gpu-controlplane --timeout=120s

# 6. Clean up database for local dev (only keep the local CPU type active)
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

# 7. Apply ingress (local-only, not part of chart)
echo ""
echo "Applying ingress..."
kubectl apply -f "$SCRIPT_DIR/manifests/ingress.yaml"

# 8. Wait for API service to be ready
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
