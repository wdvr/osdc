#!/bin/bash
#
# Create a workbench pod for a player
# Usage: ./create-workbench.sh <player-name>
#

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <player-name>"
    echo "Example: $0 alice"
    exit 1
fi

PLAYER_NAME="$1"
PLAYER_NAME_LOWER=$(echo "$PLAYER_NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '-')

echo "Creating workbench for player: $PLAYER_NAME_LOWER"

# Create pod directly
kubectl run "workbench-${PLAYER_NAME_LOWER}" \
    --namespace=ctf-game \
    --image=ctf-game/workbench:latest \
    --restart=Never \
    --stdin --tty \
    --labels="app=workbench,player=${PLAYER_NAME_LOWER}" \
    --requests='memory=256Mi,cpu=100m' \
    --limits='memory=512Mi,cpu=500m'

echo ""
echo "Workbench created for $PLAYER_NAME_LOWER"
echo ""
echo "To connect:"
echo "  kubectl exec -it workbench-${PLAYER_NAME_LOWER} -n ctf-game -- /bin/bash"
echo ""
echo "To delete later:"
echo "  kubectl delete pod workbench-${PLAYER_NAME_LOWER} -n ctf-game"
