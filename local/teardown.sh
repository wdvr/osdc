#!/bin/bash
set -e

echo "Deleting k3d cluster gpu-dev-local..."
k3d cluster delete gpu-dev-local
echo "Done."
