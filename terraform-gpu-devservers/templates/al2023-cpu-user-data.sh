#!/bin/bash

# CPU-only Amazon Linux 2023 EKS node setup
# No NVIDIA drivers or GPU configuration

set -o xtrace

# Disable the default nodeadm services that try to parse user-data as config
systemctl disable nodeadm-config.service || true
systemctl disable nodeadm-run.service || true
systemctl stop nodeadm-config.service || true
systemctl stop nodeadm-run.service || true

# Install basic monitoring tools
yum install -y htop wget

# =============================================================================
# Configure container runtimes to trust internal HTTP registries
# This must be done BEFORE nodeadm init starts containerd/docker
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

# Configure and run nodeadm for EKS cluster joining
# Get the base64 certificate data from AWS
CA_DATA=$(aws eks describe-cluster --region ${region} --name ${cluster_name} --query 'cluster.certificateAuthority.data' --output text)

cat > /tmp/nodeadm-config.yaml <<EOF
apiVersion: node.eks.aws/v1alpha1
kind: NodeConfig
spec:
  cluster:
    name: ${cluster_name}
    apiServerEndpoint: ${cluster_endpoint}
    certificateAuthority: $CA_DATA
    cidr: 172.20.0.0/16
  kubelet:
    config:
      clusterDNS:
        - 172.20.0.10
      cpuManagerPolicy: static
      cpuManagerReconcilePeriod: 10s
      systemReserved:
        cpu: "1"
        memory: "2Gi"
      kubeReserved:
        cpu: "1"
        memory: "2Gi"
    flags:
      - --node-labels=NodeType=${gpu_type}
EOF

/usr/bin/nodeadm init --config-source file:///tmp/nodeadm-config.yaml

# Network tuning
cat >/etc/sysctl.d/99-net.conf <<'EOF'
net.core.rmem_default=262144000
net.core.rmem_max=262144000
net.core.wmem_default=262144000
net.core.wmem_max=262144000
EOF
sysctl --system

echo "Amazon Linux 2023 EKS CPU node setup completed"