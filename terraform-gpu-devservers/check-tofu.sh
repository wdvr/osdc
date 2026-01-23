#!/bin/bash
# Safety check script - Verifies OpenTofu is available and terraform is not being used
# Run this before any infrastructure operations

set -e

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  OpenTofu Safety Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check 1: OpenTofu installed
echo "Check 1: OpenTofu Installation"
echo "────────────────────────────────"
if command -v tofu &> /dev/null; then
    TOFU_VERSION=$(tofu version | head -n1)
    echo "✅ OpenTofu is installed: $TOFU_VERSION"
    echo "   Location: $(which tofu)"
else
    echo "❌ CRITICAL ERROR: OpenTofu is NOT installed"
    echo ""
    echo "This infrastructure requires OpenTofu. Install it now:"
    echo ""
    echo "  macOS:"
    echo "    brew install opentofu"
    echo ""
    echo "  Linux:"
    echo "    # See https://opentofu.org/docs/intro/install/"
    echo ""
    echo "❌ Cannot proceed safely without OpenTofu"
    exit 1
fi
echo ""

# Check 2: Terraform should NOT be used
echo "Check 2: Terraform Detection"
echo "────────────────────────────────"
if command -v terraform &> /dev/null; then
    TERRAFORM_PATH=$(which terraform)
    TERRAFORM_VERSION=$(terraform version 2>/dev/null | head -n1 || echo "unknown")
    echo "⚠️  WARNING: Terraform is installed on this system"
    echo "   Location: $TERRAFORM_PATH"
    echo "   Version: $TERRAFORM_VERSION"
    echo ""
    echo "   🚨 DO NOT USE TERRAFORM ON THIS PROJECT 🚨"
    echo ""
    echo "   Using terraform will:"
    echo "   - Corrupt the OpenTofu state file"
    echo "   - Cause resource duplication"
    echo "   - Lead to data loss"
    echo "   - Require complete infrastructure rebuild"
    echo ""
    echo "   ALWAYS use 'tofu' instead of 'terraform'"
else
    echo "✅ Terraform not found (good - prevents accidental usage)"
fi
echo ""

# Check 3: State file format
echo "Check 3: State File Check"
echo "────────────────────────────────"
if [ -f ".terraform/terraform.tfstate" ] || [ -f "terraform.tfstate" ]; then
    echo "⚠️  WARNING: Found terraform.tfstate file"
    echo "   This may indicate previous terraform usage"
    echo "   Proceed with caution"
elif [ -f ".terraform.lock.hcl" ]; then
    # Check if state backend is configured
    if grep -q "backend" *.tf 2>/dev/null; then
        echo "✅ Using remote state backend (good)"
    else
        echo "ℹ️  Local state backend in use"
    fi
    echo "✅ Lock file exists - dependency tracking active"
else
    echo "ℹ️  No state files found (project not initialized yet)"
    echo "   Run 'tofu init' to initialize"
fi
echo ""

# Check 4: Git status
echo "Check 4: Git Repository Status"
echo "────────────────────────────────"
if git rev-parse --git-dir > /dev/null 2>&1; then
    BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
    UNCOMMITTED=$(git status --porcelain | wc -l | tr -d ' ')
    
    echo "✅ Git repository detected"
    echo "   Branch: $BRANCH"
    echo "   Uncommitted changes: $UNCOMMITTED files"
    
    if [ "$UNCOMMITTED" -gt 0 ]; then
        echo ""
        echo "   💡 TIP: Commit your changes before applying infrastructure updates"
    fi
else
    echo "ℹ️  Not a git repository"
fi
echo ""

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Safety Check Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✅ OpenTofu: READY"
if command -v terraform &> /dev/null; then
    echo "⚠️  Terraform: DETECTED (do not use)"
else
    echo "✅ Terraform: Not installed (good)"
fi
echo ""
echo "You can now proceed with OpenTofu commands:"
echo "  tofu init"
echo "  tofu plan"
echo "  tofu apply"
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""


