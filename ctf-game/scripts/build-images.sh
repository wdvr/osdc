#!/bin/bash
#
# Build all CTF game Docker images
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Building CTF Game Images..."
echo "==========================="

cd "$PROJECT_DIR"

# Build each layer
echo ""
echo "Building Layer 1 - Web Service..."
docker build -t ctf-game/layer1-web:latest layers/layer1-web/

echo ""
echo "Building Layer 2 - Restricted Shell..."
docker build -t ctf-game/layer2-shell:latest layers/layer2-shell/

echo ""
echo "Building Layer 3 - Privilege Escalation..."
docker build -t ctf-game/layer3-priv:latest layers/layer3-privesc/

echo ""
echo "Building Layer 4 - Pivot Server..."
docker build -t ctf-game/layer4-pivot:latest layers/layer4-pivot/

echo ""
echo "Building Layer 5 - AI Agent..."
docker build -t ctf-game/layer5-agent:latest layers/layer5-agent/

echo ""
echo "Building Player Workbench..."
docker build -t ctf-game/workbench:latest workbench/

echo ""
echo "Building Scoreboard..."
docker build -t ctf-game/scoreboard:latest scoreboard/

echo ""
echo "==========================="
echo "All images built successfully!"
echo ""
echo "Images created:"
docker images | grep ctf-game
