# Local Development Guide

## Prerequisites

- Docker Desktop (running)
- [k3d](https://k3d.io/) (`brew install k3d`)
- [Helm](https://helm.sh/) (`brew install helm`)
- kubectl (`brew install kubectl`)
- Python 3.11+ with `gpu-dev` CLI installed

## Quick Start

```bash
# 1. Set up local k3d cluster + deploy all services
cd local
./setup.sh

# 2. Switch CLI to local environment
cd ../terraform-gpu-devservers
./switch-to.sh local

# 3. Login and test
gpu-dev login
gpu-dev avail
```

## What `setup.sh` Does

1. Creates a k3d cluster (`gpu-dev-local`) with port mappings
2. Builds local Docker images for API service and reservation processor
3. Imports images into k3d
4. Deploys the Helm chart with local overrides: `helm upgrade --install gpu-dev-server ./charts/gpu-dev-server -f values-local.yaml`
5. Waits for PostgreSQL to be ready
6. Port-forwards the API service to `localhost:8000`

## Local Values Overrides

`charts/gpu-dev-server/values-local.yaml` configures:

- `cloudProvider.name: "local"` - disables all AWS-specific logic
- Single PostgreSQL instance (no replica)
- No registry caches (images loaded directly into k3d)
- No image prepuller
- No availability updater or reservation expiry CronJobs
- ClusterIP services (no LoadBalancer)
- Minimal resource requests

## Testing Reservations

```bash
# CPU-only reservation (no GPU required)
gpu-dev reserve --gpu-type cpu-arm --gpus 0 --hours 1 --no-persist

# Connect to pod
gpu-dev connect

# Cancel
gpu-dev cancel -af
```

## Limitations

- **No GPUs** - k3d doesn't support GPU passthrough, only CPU reservations work
- **No persistent disks** - EBS volumes not available, uses EmptyDir
- **No DNS routing** - No Route53, SSH proxy, or domain-based access
- **No image builds** - BuildKit Dockerfile builds require ECR (disabled locally)
- **No EFS** - Shared ccache and personal EFS mounts use EmptyDir fallback
- **Single node** - Multinode reservations won't schedule correctly

## Teardown

```bash
cd local
./teardown.sh
```

## Troubleshooting

```bash
# Check pod status
kubectl get pods -n gpu-controlplane
kubectl get pods -n gpu-dev

# Check API logs
kubectl logs -n gpu-controlplane -l app=api-service

# Check processor logs
kubectl logs -n gpu-controlplane -l app=reservation-processor

# Restart from scratch
cd local && ./teardown.sh && ./setup.sh
```
