#!/bin/bash
# Safety verification script - ensures only OpenTofu is used
# Run this before any infrastructure operations

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo ""
echo "================================================"
echo "  OpenTofu Safety Verification"
echo "================================================"
echo ""

# Check 1: OpenTofu is installed
if ! command -v tofu &> /dev/null; then
    echo -e "${RED}❌ CRITICAL ERROR: OpenTofu is NOT installed${NC}"
    echo ""
    echo "This project requires OpenTofu (not Terraform)."
    echo ""
    echo "Install OpenTofu:"
    echo "  macOS:  brew install opentofu"
    echo "  Linux:  https://opentofu.org/docs/intro/install/"
    echo ""
    echo -e "${RED}⚠️  DO NOT proceed with terraform - it will corrupt state!${NC}"
    echo ""
    exit 1
fi

echo -e "${GREEN}✓ OpenTofu is installed${NC}"
tofu version
echo ""

# Check 2: Warn if terraform is also installed
if command -v terraform &> /dev/null; then
    TERRAFORM_PATH=$(which terraform)
    echo -e "${YELLOW}⚠️  WARNING: terraform is also installed at: $TERRAFORM_PATH${NC}"
    echo ""
    echo "This can lead to accidents. Make sure you:"
    echo "  - Always use 'tofu' commands"
    echo "  - Never use 'terraform' commands"
    echo "  - Consider aliasing terraform to prevent mistakes:"
    echo "    alias terraform='echo \"ERROR: Use tofu instead of terraform!\" && false'"
    echo ""
else
    echo -e "${GREEN}✓ terraform is NOT installed (good!)${NC}"
    echo ""
fi

# Check 3: Verify we're in the right directory
if [ ! -f "main.tf" ] || [ ! -f "api-service.tf" ]; then
    echo -e "${RED}❌ ERROR: Not in terraform-gpu-devservers directory${NC}"
    echo "Run this from: terraform-gpu-devservers/"
    exit 1
fi

echo -e "${GREEN}✓ In correct directory${NC}"
echo ""

# Check 4: Verify state file (if exists)
if [ -f "terraform.tfstate" ]; then
    # Check if it was created by terraform or tofu
    SERIAL=$(cat terraform.tfstate | jq -r '.serial // 0')
    LINEAGE=$(cat terraform.tfstate | jq -r '.lineage // "unknown"')
    
    echo "State file exists:"
    echo "  Serial: $SERIAL"
    echo "  Lineage: $LINEAGE"
    echo ""
    echo -e "${YELLOW}⚠️  IMPORTANT: Only use 'tofu' commands with this state${NC}"
    echo ""
fi

echo "================================================"
echo -e "${GREEN}✅ SAFE TO PROCEED with OpenTofu${NC}"
echo "================================================"
echo ""
echo "You can now run:"
echo "  tofu plan"
echo "  tofu apply"
echo ""
echo -e "${RED}⚠️  Remember: NEVER use 'terraform' commands${NC}"
echo ""

