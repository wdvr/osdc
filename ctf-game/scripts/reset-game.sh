#!/bin/bash
#
# Reset the CTF game (redeploy all services)
#

set -e

echo "Resetting CTF Game..."
echo "====================="

# Reset scoreboard
echo ""
echo "Resetting scoreboard..."
kubectl exec -n ctf-game deploy/scoreboard -- \
    curl -s -X POST -H "X-Admin-Key: ctf-admin-reset-2024" http://localhost:8000/api/reset \
    2>/dev/null || echo "Warning: Could not reset scoreboard via API"

# Restart all deployments
echo ""
echo "Restarting all layer deployments..."
kubectl rollout restart deployment -n ctf-game -l layer

echo ""
echo "Restarting scoreboard..."
kubectl rollout restart deployment scoreboard -n ctf-game

# Wait for rollout
echo ""
echo "Waiting for rollout to complete..."
kubectl rollout status deployment -n ctf-game --timeout=120s

echo ""
echo "====================="
echo "Game reset complete!"
echo ""
echo "Current status:"
kubectl get pods -n ctf-game
