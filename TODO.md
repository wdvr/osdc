# TODO

## Blocking Issues

### 1. SSH from devvm doesn't work
- **Symptom**: `ssh gpu-dev-xxx` → "Too many authentication failures" or key rejected
- **Root cause 1**: Meta's `/etc/ssh/ssh_config` injects 7 extra IdentityFile entries (including `/var/facebook/credentials/...`), overriding our `IdentitiesOnly yes`
- **Fix committed**: Added `IdentityAgent none` + `PreferredAuthentications publickey` to SSH config generation — needs push + reinstall
- **Root cause 2**: The pubkey might not be installed correctly on the pod. The init container creates the key at `/home/$DEV_USER/.ssh/authorized_keys` but the main container starts sshd as root with `AuthorizedKeysFile .ssh/authorized_keys` — this path is relative to the login user's home. Need to verify the volume mount at `/home/$DEV_USER` persists between init and main containers.
- **Root cause 3**: NodePort may not be routable from devvm (Connection refused). `gpu-dev connect` (kubectl exec) works as fallback.

### 2. MSL runtime image tag doesn't exist
- **Symptom**: `Dockerfile.msl_runtime` does `FROM 588845.../msl_infra/buildkit-cache:runtime-amd64` but that tag doesn't exist. Build succeeds because BuildKit falls back to the base pytorch image somehow.
- **Result**: `osdc-msl_runtime` image is just vanilla pytorch re-tagged — no conda, no entrypoint-user.sh, no Meta tools
- **Fix**: Find the correct tag for the MSL runtime image. Check with MSL infra team, or run `pkg-builder` to see what tags are pushed. The tag might be `dev-amd64`, a git hash, or something else.
- **Workaround**: Use `Dockerfile.msl_base_new` which extends `base-new-amd64` (this tag DOES exist) and adds openssh on top.

### 3. Conda not available
- **Depends on**: Fix #2 (correct MSL runtime image)
- The MSL `base-new` and `runtime` images should have conda at `/opt/conda` or `/usr/bin/conda`
- The vanilla pytorch image uses pip only
- If we can't get the MSL runtime image, add conda installation to `Dockerfile.msl_base_new`

## Next Priorities

### 4. hostPath volumes for Meta certs
- MKS nodes are Meta machines — `/var/facebook/rootcanal` and `/var/facebook/x509_identities` likely exist on them
- Add optional hostPath volumes to pod spec for these paths
- This would unblock: manifold access, buck remote execution, internal pip repos
- **Risk**: Requires privileged pod access or relaxed PSP/PSA policies
- **Check**: Exec into a pod and test if hostPath works: mount `/var/facebook` and verify contents

### 5. fbsource / Eden access
- Eden requires FUSE on the host + Eden daemon running — NOT available on K8s worker nodes
- Options:
  - **sshfs mount** back to devvm (requires SSH key + network access)
  - **git clone** (slow, OSDC prod does this pattern with a git-cache service)
  - **hostPath** if `/data/users/` exists on MKS nodes (unlikely — Eden is per-user)
- Most practical: git clone for now, sshfs for interactive use

### 6. Main script "fast path" skips user env setup
- When the image has sshd + user already, the startup script skips PATH setup, oh-my-zsh plugins, conda init etc.
- The user gets a bare shell with no conda/cuda in PATH
- Fix: always run the PATH setup regardless of fast path

## Ideas from seemethere/devservers

### CRDs instead of ConfigMaps (medium-term)
`seemethere/devservers` uses proper K8s CRDs:
- `DevServerFlavor` — t-shirt sized resource configs (like our gpu-types ConfigMap but K8s-native)
- `DevServerUser` — user access management with SSH keys
- `DevServer` — the actual dev server resource

### Kopf Operator
Replace CLI-driven pod creation with a Kopf operator for lifecycle management.

### Docker-style Volume Mounts
`devctl create --name mydev --flavor gpu-small -v mydev-home:/home/dev`

### What We Do Better
- Image building in-cluster (BuildKit)
- Image picker from ConfigMap
- SSH config auto-generation for VS Code Remote
- Real user identity passthrough (DEV_USER/DEV_UID/DEV_GID)
- Zero-config mode detection (k8s-direct default)
- Cluster-level config via ConfigMap (no operator needed)
