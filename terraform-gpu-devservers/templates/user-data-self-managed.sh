#!/bin/bash

# User data script for self-managed EKS GPU nodes (Ubuntu 22.04)
# Uses traditional /etc/eks/bootstrap.sh for cluster registration

set -o xtrace

# Disable IPv6 completely during boot to avoid metadata service issues
# This is the simplest and most reliable approach for p5.48xlarge instances

echo 'net.ipv6.conf.all.disable_ipv6 = 1' >> /etc/sysctl.conf
echo 'net.ipv6.conf.default.disable_ipv6 = 1' >> /etc/sysctl.conf
echo 'net.ipv6.conf.lo.disable_ipv6 = 1' >> /etc/sysctl.conf
sysctl -p

# Force cloud-init to use IPv4 only for metadata service
mkdir -p /etc/cloud/cloud.cfg.d
cat > /etc/cloud/cloud.cfg.d/99-disable-ipv6.cfg <<'EOF'
datasource:
  Ec2:
    metadata_urls: ['http://169.254.169.254']
    max_wait: 120
    timeout: 50
EOF

# Update system and install monitoring tools (Ubuntu uses apt)
apt-get update -y
apt-get install -y htop wget curl nvtop

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

# Join EKS cluster with GPU node labels
/etc/eks/bootstrap.sh ${cluster_name} \
    --apiserver-endpoint ${cluster_endpoint} \
    --b64-cluster-ca ${cluster_ca} \
    --container-runtime containerd \
    --kubelet-extra-args "--node-labels=GpuType=${gpu_type}"

# Configure EFA settings only for instances that actually have EFA hardware
if [[ -d /sys/class/infiniband/efa_0 || -e /dev/infiniband/uverbs0 ]]; then
    echo 'FI_PROVIDER=efa' >> /etc/environment
    echo 'NCCL_PROTO=simple' >> /etc/environment
    echo "EFA hardware detected - configured EFA environment variables"
else
    echo "No EFA hardware detected - skipping EFA configuration"
fi

# Network tuning using drop-in file (cleaner than modifying /etc/sysctl.conf)
cat >/etc/sysctl.d/99-gpu-net.conf <<'EOF'
net.core.rmem_default=262144000
net.core.rmem_max=262144000
net.core.wmem_default=262144000
net.core.wmem_max=262144000
EOF
sysctl --system

echo "Self-managed EKS node bootstrap completed successfully"