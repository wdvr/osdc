# High Priority Optimizations - Reservation Speed

## Current Performance (with trace breakdown)
- **Total time:** ~50s for persistent disk reservations
- **Breakdown:**
  - CLI → Lambda: 0.084s
  - Disk restore from snapshot: 6s
  - EBS volume attach + mount: 26s ← **BOTTLENECK #1**
  - Init containers (ssh-setup): 1s
  - Container startup (sudo, SSH, env): 13s ← **BOTTLENECK #2**
  - Total pod ready wait: 40s

## Planned Optimizations (HIGH PRIORITY)

### 1. Skip filesystem check on EBS mount
- **Current:** fsck runs on every 1TB ext4 mount (~8-12s overhead)
- **Fix:** Run `tune2fs -c 0 -i 0` on volume creation to disable periodic checks
- **Expected savings:** 8-12 seconds
- **Implementation:** Add to disk creation in `create_disk_from_snapshot_or_empty()`

### 2. Pre-bake sudo in Docker base image
- **Current:** Every pod startup runs `apt-get install sudo` (~2-3s)
- **Fix:** Add `RUN apt-get update && apt-get install -y sudo` to Dockerfile
- **Expected savings:** 2-3 seconds
- **Implementation:** Update `docker/gpu-dev-image/Dockerfile`

### 3. Parallelize container startup tasks
- **Current:** Sequential sudo install → sudoers setup → SSH startup
- **Fix:** Run sudo config and SSH daemon in parallel
- **Expected savings:** 1-2 seconds
- **Implementation:** Update container startup script in `create_pod()`

## Total Expected Improvement
- **Before:** 50s total
- **After:** 28-35s total (~40% faster)
- **Target:** Sub-30 second reservations with persistent disk

## NOT Implementing (rejected)
- ❌ Reduce disk size to 250GB (user wants to keep 1TB)
- ❌ Pre-attached volumes (too complex, needs node affinity)
- ❌ Systemd in containers (incompatible with Kubernetes, needs privileged mode)

## Status
- ✅ Granular timing trace implemented and deployed
- ⏸️ Optimizations parked - investigating prod issue first
