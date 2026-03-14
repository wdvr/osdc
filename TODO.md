# TODO — Ideas from seemethere/devservers

## Architecture Ideas

### CRDs instead of ConfigMaps (medium-term)
`seemethere/devservers` uses proper K8s CRDs:
- `DevServerFlavor` — t-shirt sized resource configs (like our gpu-types ConfigMap but K8s-native)
- `DevServerUser` — user access management with SSH keys
- `DevServer` — the actual dev server resource

We currently use ConfigMaps for GPU types + CLI config. CRDs would give us:
- kubectl integration (`kubectl get devservers`, `kubectl describe devserverflavor h100`)
- Schema validation (OpenAPI v3)
- Watch/reconcile via an operator (Kopf or similar)
- Proper RBAC per-resource

### Kopf Operator
They use [Kopf](https://kopf.readthedocs.io/) (Python K8s operator framework).
We could replace our CLI-driven pod creation with an operator that:
- Watches `DevServer` CRs
- Creates pods + services
- Handles lifecycle (expiry, shutdown)
- Manages user SSH key rotation

Currently our CLI does all this directly — works but doesn't scale to multi-user.

### Docker-style Volume Mounts
`devctl create --name mydev --flavor gpu-small -v mydev-home:/home/dev -v datasets:/data:ro`

Familiar syntax for users. We could adopt this for persistent disk mounting.

### Flavor YAML files
Their flavors are clean:
```yaml
apiVersion: devserver.io/v1
kind: DevServerFlavor
metadata:
  name: gpu-small
spec:
  resources:
    requests:
      cpu: "4"
      memory: "16Gi"
      nvidia.com/gpu: "1"
    limits:
      cpu: "8"
      memory: "32Gi"
      nvidia.com/gpu: "1"
  nodeSelector:
    kubernetes.io/arch: amd64
  tolerations:
    - key: nvidia.com/gpu
      operator: Exists
      effect: NoSchedule
```

Key differences from our approach:
- Separates requests vs limits (we combine them)
- Supports nodeSelector and tolerations per flavor
- Standard K8s resource spec format

### What We Do Better
- Image building in-cluster (BuildKit jobs)
- Image picker from ConfigMap
- SSH config auto-generation for VS Code Remote
- Real user identity passthrough (DEV_USER/DEV_UID/DEV_GID)
- entrypoint-user.sh fast path for pre-built images
- Zero-config mode detection (k8s-direct default)
- Cluster-level config via ConfigMap (no operator needed to get started)

## Actionable Next Steps

1. **Add nodeSelector/tolerations to GPU config** — our ConfigMap types don't support this yet
2. **Consider CRD migration** — would unlock kubectl integration and operator-based lifecycle
3. **Volume mount syntax** — `-v name:/path` is more intuitive than our current approach
4. **User CRD** — manage SSH keys at cluster level instead of per-pod env vars
