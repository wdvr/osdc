#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
TF_DIR="$ROOT_DIR/terraform-gpu-devservers"

echo "=== Building local dev images ==="

# API service (build context is api-service/)
echo ""
echo "Building gpu-dev-api:local..."
docker build -t gpu-dev-api:local -f "$TF_DIR/api-service/Dockerfile" "$TF_DIR/api-service/"

# Reservation processor (build context is terraform-gpu-devservers/ for shared/)
echo ""
echo "Building gpu-dev-processor:local..."
docker build -t gpu-dev-processor:local -f "$TF_DIR/reservation-processor-service/Dockerfile" "$TF_DIR/"

# CPU-only dev pod (uses terraform-gpu-devservers/docker/ as build context
# to reuse the same shell configs, scripts, and ssh_config as production)
echo ""
echo "Building gpu-dev-pod:local..."
docker build -t gpu-dev-pod:local -f "$SCRIPT_DIR/dev-pod-image/Dockerfile" "$TF_DIR/docker/"

# Load into k3d
echo ""
echo "Loading images into k3d cluster..."
k3d image import gpu-dev-api:local gpu-dev-processor:local gpu-dev-pod:local -c gpu-dev-local

echo ""
echo "=== All images built and loaded ==="
