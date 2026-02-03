#!/bin/bash
#
# Deploy CTF game to Kubernetes
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
K8S_DIR="$PROJECT_DIR/k8s"

echo "Deploying CTF Game to Kubernetes..."
echo "===================================="

# Create namespace
echo ""
echo "Creating namespace..."
kubectl apply -f "$K8S_DIR/namespace.yaml"

# Deploy services
echo ""
echo "Deploying services..."
kubectl apply -f "$K8S_DIR/services.yaml"

# Deploy applications
echo ""
echo "Deploying applications..."
kubectl apply -f "$K8S_DIR/deployments.yaml"

# Apply network policies (optional)
echo ""
echo "Applying network policies..."
kubectl apply -f "$K8S_DIR/network-policy.yaml" || echo "Warning: Network policies may not be supported"

echo ""
echo "===================================="
echo "Deployment complete!"
echo ""
echo "Checking status..."
kubectl get pods -n ctf-game
echo ""
echo "Services:"
kubectl get svc -n ctf-game
echo ""
echo "Scoreboard available at: http://<node-ip>:30800"
