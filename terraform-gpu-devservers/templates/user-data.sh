#!/bin/bash

# User data script for EKS GPU nodes
# Supports both testing (g4dn) and production (p5) instances

set -o xtrace

# =============================================================================
# Configure container runtimes to trust internal HTTP registries
# This must be done BEFORE bootstrap.sh starts containerd/docker
# =============================================================================

# Configure containerd (certs.d method for containerd 1.5+)
# Using Route53 private hosted zone DNS names (resolved via VPC DNS)

# Native registry (for service images)
REGISTRY_NATIVE_DNS="registry.internal.pytorch-gpu-dev.local:5000"
mkdir -p /etc/containerd/certs.d/$REGISTRY_NATIVE_DNS
cat > /etc/containerd/certs.d/$REGISTRY_NATIVE_DNS/hosts.toml <<NATIVE_EOF
server = "http://$REGISTRY_NATIVE_DNS"

[host."http://$REGISTRY_NATIVE_DNS"]
  capabilities = ["pull", "resolve", "push"]
  skip_verify = true
NATIVE_EOF

# GHCR pull-through cache
REGISTRY_GHCR_DNS="registry-ghcr.internal.pytorch-gpu-dev.local:5000"
mkdir -p /etc/containerd/certs.d/$REGISTRY_GHCR_DNS
cat > /etc/containerd/certs.d/$REGISTRY_GHCR_DNS/hosts.toml <<GHCR_EOF
server = "http://$REGISTRY_GHCR_DNS"

[host."http://$REGISTRY_GHCR_DNS"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
GHCR_EOF

# Docker Hub pull-through cache
REGISTRY_DOCKERHUB_DNS="registry-dockerhub.internal.pytorch-gpu-dev.local:5000"
mkdir -p /etc/containerd/certs.d/$REGISTRY_DOCKERHUB_DNS
cat > /etc/containerd/certs.d/$REGISTRY_DOCKERHUB_DNS/hosts.toml <<DOCKERHUB_EOF
server = "http://$REGISTRY_DOCKERHUB_DNS"

[host."http://$REGISTRY_DOCKERHUB_DNS"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
DOCKERHUB_EOF

# Configure Docker daemon (if Docker is present/used)
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<DOCKER_EOF
{
  "insecure-registries": [
    "$REGISTRY_NATIVE_DNS",
    "$REGISTRY_GHCR_DNS",
    "$REGISTRY_DOCKERHUB_DNS"
  ],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
DOCKER_EOF

echo "Configured containerd and Docker to trust internal registries (native, GHCR, Docker Hub)"

# Join the EKS cluster using the standard bootstrap script with GPU type label
/etc/eks/bootstrap.sh ${cluster_name} --kubelet-extra-args '--node-labels=GpuType=${gpu_type}'

# Install additional GPU monitoring tools
yum update -y
yum install -y htop

# Try to install nvtop (may not be available on all AMIs)
yum install -y nvtop || echo "nvtop not available"

# Configure EFA settings only for supported instances
# This will be harmless on instances that don't support EFA
echo 'FI_PROVIDER=efa' >> /etc/environment
echo 'NCCL_PROTO=simple' >> /etc/environment

# Basic network tuning (safe for all instances)
echo 'net.core.rmem_default = 262144000' >> /etc/sysctl.conf
echo 'net.core.rmem_max = 262144000' >> /etc/sysctl.conf
echo 'net.core.wmem_default = 262144000' >> /etc/sysctl.conf
echo 'net.core.wmem_max = 262144000' >> /etc/sysctl.conf
sysctl -p

echo "EKS node bootstrap completed successfully"