# GPU Dev — Quick Start

Reserve dev servers on a shared K8s cluster.

## Setup

```bash
# 1. Get kubeconfig (needs cluster access — ask your team for the cluster name)
# Example: cloud k8s generate-kubeconfig <cluster> > ~/.kube/gpu-dev.yaml

# 2. Install CLI
git clone <repo-url> -b feat/k8s-direct ~/osdc
cd ~/osdc && pip install -e .

# 3. Configure (add to ~/.zshrc)
export KUBECONFIG=~/.kube/gpu-dev.yaml
export GPU_DEV_MODE=k8s-direct
```

## Use

```bash
gpu-dev reserve           # interactive — pick size, duration, image
gpu-dev list              # show your pods
gpu-dev connect <id>      # shell into pod
ssh gpu-dev-<pod-name>    # or SSH directly
gpu-dev cancel <id>       # tear down
gpu-dev avail             # cluster capacity
```

## Sizes

| Name | CPUs | Memory | Use case |
|------|------|--------|----------|
| `cpu-s` | 8 req / 32 limit | 16 / 40 GB | Light dev |
| `cpu-m` | 16 / 64 | 32 / 80 GB | General |
| `cpu-l` | 32 / 128 | 64 / 160 GB | Builds |
| `cpu-xl` | 64 / 252 | 128 / 314 GB | Heavy compute |
| `cpu-xxl` | 200 / 252 | 280 / 314 GB | Full node |

GPU sizes available when NVIDIA operator is installed on the cluster.

## Build custom images (optional)

```bash
export GPU_DEV_REGISTRY_REPO=your-registry.com/your-repo
gpu-dev build-image                 # build all Dockerfiles in docker/ dir
gpu-dev build-image -p foo          # build only Dockerfile.foo
```

After building, `gpu-dev reserve` shows built images in the interactive picker.
