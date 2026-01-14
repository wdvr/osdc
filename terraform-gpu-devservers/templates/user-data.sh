#!/bin/bash

# User data script for EKS GPU nodes
# Supports both testing (g4dn) and production (p5) instances

set -o xtrace

# =============================================================================
# Configure container runtimes to trust internal HTTP registry (pull-through cache)
# This must be done BEFORE bootstrap.sh starts containerd/docker
# =============================================================================

# Configure containerd (certs.d method for containerd 1.5+)
mkdir -p /etc/containerd/certs.d/registry-ghcr.gpu-controlplane.svc.cluster.local:5000
cat > /etc/containerd/certs.d/registry-ghcr.gpu-controlplane.svc.cluster.local:5000/hosts.toml <<'REGISTRY_EOF'
server = "http://registry-ghcr.gpu-controlplane.svc.cluster.local:5000"

[host."http://registry-ghcr.gpu-controlplane.svc.cluster.local:5000"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
REGISTRY_EOF

# Configure Docker daemon (if Docker is present/used)
mkdir -p /etc/docker
cat > /etc/docker/daemon.json <<'DOCKER_EOF'
{
  "insecure-registries": ["registry-ghcr.gpu-controlplane.svc.cluster.local:5000"],
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "100m",
    "max-file": "3"
  }
}
DOCKER_EOF

echo "Configured containerd and Docker to trust internal registry cache"

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