#!/bin/bash
#
# Clean up CTF game from Kubernetes
#

set -e

echo "Cleaning up CTF Game..."
echo "======================="

read -p "Are you sure you want to delete the entire CTF game? [y/N] " -n 1 -r
echo

if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

# Delete all resources in namespace
echo ""
echo "Deleting all resources in ctf-game namespace..."
kubectl delete all --all -n ctf-game 2>/dev/null || true

# Delete network policies
echo ""
echo "Deleting network policies..."
kubectl delete networkpolicy --all -n ctf-game 2>/dev/null || true

# Delete namespace
echo ""
echo "Deleting namespace..."
kubectl delete namespace ctf-game 2>/dev/null || true

echo ""
echo "======================="
echo "Cleanup complete!"
echo ""
echo "To also remove Docker images:"
echo "  docker rmi \$(docker images 'ctf-game/*' -q)"
