#!/bin/bash

set -e

if [ $# -ne 1 ]; then
    echo "Usage: $0 <prod|test|local>"
    echo ""
    echo "Switches between prod, test, and local environments by:"
    echo "  - Updating kubeconfig for the correct cluster"
    echo "  - Switching kubens to gpu-dev namespace"
    echo "  - Selecting the correct Terraform workspace"
    echo "  - Setting AWS region via aws-cli config"
    exit 1
fi

ENVIRONMENT=$1

case $ENVIRONMENT in
    "local")
        echo "Switching to local k3d environment..."
        if command -v gpu-dev >/dev/null 2>&1; then
            gpu-dev config environment local
            gpu-dev config set api_url http://localhost:8000
        fi
        kubectl config use-context k3d-gpu-dev-local
        if command -v kubens >/dev/null 2>&1; then
            kubens gpu-dev
        else
            kubectl config set-context --current --namespace=gpu-dev
        fi
        echo ""
        echo "Switched to local. API: http://localhost:8000"
        exit 0
        ;;
    "prod")
        REGION="us-east-2"
        WORKSPACE="prod"
        API_URL="https://api.devservers.io"
        ;;
    "test")
        REGION="us-west-1"
        WORKSPACE="default"
        API_URL="https://api.test.devservers.io"
        ;;
    *)
        echo "Error: Environment must be 'prod', 'test', or 'local'"
        exit 1
        ;;
esac

echo "🔄 Switching to $ENVIRONMENT environment..."
echo ""

# Set AWS region via gpu-dev config
echo "📍 Setting AWS region to $REGION..."
if command -v gpu-dev >/dev/null 2>&1; then
    gpu-dev config environment $ENVIRONMENT
else
    echo "⚠️  gpu-dev command not found, setting AWS_DEFAULT_REGION manually"
    export AWS_DEFAULT_REGION=$REGION
    echo "   Set AWS_DEFAULT_REGION=$REGION (session only)"
fi

# Update kubeconfig for EKS cluster
echo "☸️  Updating kubeconfig for EKS cluster in $REGION..."
aws eks update-kubeconfig --region $REGION --name pytorch-gpu-dev-cluster

# Switch to gpu-dev namespace
echo "📦 Switching to gpu-dev namespace..."
if command -v kubens >/dev/null 2>&1; then
    kubens gpu-dev
else
    echo "⚠️  kubens not found, using kubectl"
    kubectl config set-context --current --namespace=gpu-dev
fi

# Select Terraform workspace
echo "🏗️  Selecting Terraform workspace: $WORKSPACE..."
tofu workspace select $WORKSPACE

# Set API URL
echo "🌐 Setting API URL to $API_URL..."
if command -v gpu-dev >/dev/null 2>&1; then
    gpu-dev config set api_url "$API_URL"
fi

echo ""
echo "✅ Successfully switched to $ENVIRONMENT environment!"
echo "   Region: $REGION"
echo "   Workspace: $WORKSPACE"
echo "   Namespace: gpu-dev"
echo "   API URL: $API_URL"