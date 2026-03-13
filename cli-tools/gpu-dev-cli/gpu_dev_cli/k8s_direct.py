"""K8s-Direct mode: manage GPU dev pods directly without an API service.

The CLI creates the dev Pod + NodePort Service directly via the K8s API.
No Jobs, no provisioner SA, no pip installs at runtime.

Required RBAC: create/delete Pods + Services in gpu-dev namespace,
get/list Nodes (cluster-scoped).
"""

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Suppress noisy kubernetes client logging — we handle errors via exceptions.
logging.getLogger("kubernetes").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

from kubernetes import client as k8s_client

from .config import Config
from .kubeconfig import get_k8s_api_client

# ---------------------------------------------------------------------------
# GPU / instance configuration
# ---------------------------------------------------------------------------

# Default dev pod image. Override with GPU_DEV_IMAGE env var.
# Use the custom OSDC image when available (has zsh, oh-my-zsh, sudo, ssh baked in).
# Falls back to vanilla pytorch if custom image not built yet.
import os as _os
_DEFAULT_IMAGE = _os.getenv(
    "GPU_DEV_IMAGE",
    "pytorch/pytorch:2.10.0-cuda13.0-cudnn9-devel"
)

_MID_GPU = {
    "max_gpus": 4, "cpus": 48, "memory_gb": 192,
    "cpu_request": "8", "mem_request_gb": 32,
    "default_image": _DEFAULT_IMAGE,
}
_HIGH_GPU = {
    "max_gpus": 8, "cpus": 192, "memory_gb": 2048,
    "cpu_request": "32", "mem_request_gb": 128,
    "default_image": _DEFAULT_IMAGE,
}

GPU_CONFIG: Dict[str, Dict[str, Any]] = {
    # CPU-only sizes (S/M/L/XL fractions of a worker node)
    "cpu-s": {
        "max_gpus": 0, "cpus": 32, "memory_gb": 40,
        "cpu_request": "8", "mem_request_gb": 16,
        "default_image": _DEFAULT_IMAGE,
    },
    "cpu-m": {
        "max_gpus": 0, "cpus": 64, "memory_gb": 80,
        "cpu_request": "16", "mem_request_gb": 32,
        "default_image": _DEFAULT_IMAGE,
    },
    "cpu-l": {
        "max_gpus": 0, "cpus": 128, "memory_gb": 160,
        "cpu_request": "32", "mem_request_gb": 64,
        "default_image": _DEFAULT_IMAGE,
    },
    "cpu-xl": {
        "max_gpus": 0, "cpus": 252, "memory_gb": 314,
        "cpu_request": "64", "mem_request_gb": 128,
        "default_image": _DEFAULT_IMAGE,
    },
    "cpu-xxl": {
        "max_gpus": 0, "cpus": 252, "memory_gb": 314,
        "cpu_request": "200", "mem_request_gb": 280,
        "default_image": _DEFAULT_IMAGE,
    },
    # GPU types (need NVIDIA operator)
    "t4":   _MID_GPU,
    "a10g": _MID_GPU,
    "l4":   _MID_GPU,
    "a100": {**_HIGH_GPU, "cpus": 96, "memory_gb": 1152, "cpu_request": "16", "mem_request_gb": 64},
    "h100": _HIGH_GPU,
    "h200": _HIGH_GPU,
    "b200": _HIGH_GPU,
}

# Explicit mapping from NVIDIA node labels to our GPU type names.
# The key is the lowercase value from the "nvidia.com/gpu.product" node label.
NODE_GPU_LABEL_MAP: Dict[str, str] = {
    "tesla-t4": "t4",
    "nvidia-tesla-t4": "t4",
    "nvidia-a10g": "a10g",
    "nvidia-l4": "l4",
    "nvidia-a100-sxm4-40gb": "a100",
    "nvidia-a100-sxm4-80gb": "a100",
    "nvidia-h100-80gb-hbm3": "h100",
    "nvidia-h200": "h200",
    "nvidia-b200": "b200",
}

# Labels applied to all resources created by k8s-direct mode
MANAGED_BY_LABEL = "gpu-dev/managed-by"
MANAGED_BY_VALUE = "gpu-dev-cli"
LABEL_USER = "gpu-dev/user"
LABEL_USERNAME = "gpu-dev/username"
LABEL_GPU_TYPE = "gpu-dev/gpu-type"
LABEL_RESERVATION_ID = "gpu-dev/reservation-id"

# Annotations
ANN_CREATED_AT = "gpu-dev/created-at"
ANN_EXPIRES_AT = "gpu-dev/expires-at"
ANN_HOURS = "gpu-dev/hours"
ANN_NODE_IP = "gpu-dev/node-ip"
ANN_SSH_PORT = "gpu-dev/ssh-port"


def _parse_k8s_memory(mem_str: str) -> int:
    """Parse a K8s memory string (e.g. '328908728Ki', '64Gi', '1024Mi') to bytes."""
    if not mem_str or mem_str == "0":
        return 0
    suffixes = {
        "Ki": 1024,
        "Mi": 1024 ** 2,
        "Gi": 1024 ** 3,
        "Ti": 1024 ** 4,
        "K": 1000,
        "M": 1000 ** 2,
        "G": 1000 ** 3,
        "T": 1000 ** 4,
    }
    for suffix, multiplier in suffixes.items():
        if mem_str.endswith(suffix):
            return int(mem_str[: -len(suffix)]) * multiplier
    try:
        return int(mem_str)
    except ValueError:
        return 0


def _sanitize_label_value(value: str) -> str:
    """Sanitize a string for use as a K8s label value.

    Label values must be <= 63 chars and match [a-zA-Z0-9._-].
    Common issue: email-style user IDs contain '@'.
    """
    import re
    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "-", value)
    sanitized = sanitized.strip("-._")
    return sanitized[:63]


def _ensure_ssh_include() -> None:
    """Ensure ~/.ssh/config includes gpu-dev SSH configs.

    Adds `Include ~/.gpu-dev/*-sshconfig` to ~/.ssh/config (and
    ~/.cursor/ssh_config if it exists) so `ssh gpu-dev-xxx` works.
    """
    from pathlib import Path

    include_line = "Include ~/.gpu-dev/*-sshconfig"

    # Check permission file
    permission_file = Path.home() / ".gpu-dev" / ".ssh-config-permission"
    if permission_file.exists():
        try:
            if permission_file.read_text().strip() != "yes":
                return
        except Exception:
            return
    else:
        # First time — auto-approve (non-destructive, just adds an Include)
        try:
            permission_file.parent.mkdir(mode=0o700, exist_ok=True)
            permission_file.write_text("yes\n")
        except Exception:
            pass

    for config_path in [
        Path.home() / ".ssh" / "config",
        Path.home() / ".cursor" / "ssh_config",
    ]:
        try:
            if config_path.exists():
                content = config_path.read_text()
                if include_line in content:
                    continue
            else:
                config_path.parent.mkdir(mode=0o700, exist_ok=True)
                content = ""

            # Add Include at the top (must be before any Host blocks)
            new_content = include_line + "\n\n" + content
            config_path.write_text(new_content)
            config_path.chmod(0o600)
        except Exception:
            pass


def _raise_friendly(api_exc, action: str):
    """Turn a K8s ApiException into a readable RuntimeError."""
    try:
        body = json.loads(api_exc.body)
        msg = body.get("message", str(api_exc))
    except Exception:
        msg = str(api_exc)

    if api_exc.status == 403:
        raise RuntimeError(
            f"{msg}\n\n"
            "RBAC not configured for k8s-direct mode. Deploy the RBAC resources:\n"
            "  helm upgrade gpu-dev charts/gpu-dev-server --reuse-values "
            "--set gpuDev.k8sDirect.enabled=true"
        ) from None
    elif api_exc.status == 404:
        raise RuntimeError(f"Not found: {msg}") from None
    else:
        raise RuntimeError(f"K8s API error ({api_exc.status}): {msg}") from None


class K8sDirectManager:
    """Manages GPU dev pods directly via the K8s API — no API service needed."""

    def __init__(self, config: Config):
        self.config = config
        self.namespace = config.namespace
        self.api_client = get_k8s_api_client(config)
        self.v1 = k8s_client.CoreV1Api(self.api_client)

    # ------------------------------------------------------------------
    # reserve
    # ------------------------------------------------------------------

    def reserve(
        self,
        user_id: str,
        gpu_type: str,
        gpu_count: int,
        hours: float,
        ssh_pubkey: str = "",
        image: Optional[str] = None,
        username: Optional[str] = None,
        uid: Optional[int] = None,
        gid: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create a dev Pod + NodePort Service directly.

        Returns dict with reservation_id and pod_name.
        """
        gpu_type = gpu_type.lower()
        if gpu_type not in GPU_CONFIG:
            raise ValueError(
                f"Unknown gpu_type '{gpu_type}'. "
                f"Valid: {', '.join(sorted(GPU_CONFIG))}"
            )
        cfg = GPU_CONFIG[gpu_type]

        # Validate GPU count
        if cfg["max_gpus"] == 0 and gpu_count != 0:
            raise ValueError(f"CPU type '{gpu_type}' does not support GPUs")
        if gpu_count > cfg["max_gpus"] and cfg["max_gpus"] > 0:
            raise ValueError(
                f"Max {cfg['max_gpus']} GPUs for {gpu_type}, requested {gpu_count}"
            )

        reservation_id = secrets.token_hex(8)  # 16 hex chars
        if not image:
            image = cfg["default_image"]

        pod_name = f"gpu-dev-{reservation_id}"
        svc_name = f"gpu-dev-svc-{reservation_id}"

        # Resolve user identity defaults
        if username is None:
            username = _os.getenv("USER", "dev")
        if uid is None:
            try:
                uid = _os.getuid()
            except AttributeError:
                uid = 1081  # fallback on non-POSIX
        if gid is None:
            try:
                gid = _os.getgid()
            except AttributeError:
                gid = uid

        labels = {
            "app": "gpu-dev-pod",
            MANAGED_BY_LABEL: MANAGED_BY_VALUE,
            LABEL_USER: _sanitize_label_value(user_id),
            LABEL_USERNAME: _sanitize_label_value(username),
            LABEL_GPU_TYPE: gpu_type,
            LABEL_RESERVATION_ID: reservation_id,
        }

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        exp_ts = time.time() + hours * 3600
        exp_iso = datetime.fromtimestamp(exp_ts, tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        annotations = {
            ANN_CREATED_AT: now_iso,
            ANN_EXPIRES_AT: exp_iso,
            ANN_HOURS: str(hours),
        }

        # --- build pod spec ---
        pod = self._build_pod_spec(
            pod_name=pod_name,
            labels=labels,
            annotations=annotations,
            image=image,
            ssh_pubkey=ssh_pubkey,
            gpu_count=gpu_count,
            hours=hours,
            cfg=cfg,
            username=username,
            uid=uid,
            gid=gid,
        )

        # --- create pod ---
        try:
            self.v1.create_namespaced_pod(namespace=self.namespace, body=pod)
        except k8s_client.exceptions.ApiException as e:
            _raise_friendly(e, "create pod")

        # --- create service (with pod as owner for GC) ---
        try:
            created_pod = self.v1.read_namespaced_pod(pod_name, self.namespace)
            svc = self._build_service_spec(
                svc_name=svc_name,
                labels=labels,
                reservation_id=reservation_id,
                owner_pod=created_pod,
            )
            self.v1.create_namespaced_service(namespace=self.namespace, body=svc)
        except k8s_client.exceptions.ApiException as e:
            # Clean up the pod if service creation fails
            try:
                self.v1.delete_namespaced_pod(pod_name, self.namespace)
            except Exception:
                pass
            _raise_friendly(e, "create service")

        return {
            "reservation_id": reservation_id,
            "pod_name": pod_name,
            "gpu_type": gpu_type,
            "gpu_count": gpu_count,
            "hours": hours,
            "image": image,
            "user_id": user_id,
            "username": username,
        }

    # ------------------------------------------------------------------
    # wait_for_ready
    # ------------------------------------------------------------------

    def wait_for_ready(
        self,
        reservation_id: str,
        timeout_seconds: int = 300,
        console=None,
    ) -> Optional[Dict[str, Any]]:
        """Poll until the dev pod is Running.

        Once running, reads the node IP + service NodePort and annotates
        the pod with connection info. Prints progress updates.

        Returns connection info dict or None on timeout/failure.
        """
        from rich.console import Console as RichConsole
        con = console or RichConsole()

        from rich.live import Live
        from rich.text import Text

        pod_name = f"gpu-dev-{reservation_id}"
        svc_name = f"gpu-dev-svc-{reservation_id}"
        deadline = time.time() + timeout_seconds
        start_time = time.time()

        with Live("", console=con, refresh_per_second=1) as live:
            while time.time() < deadline:
                elapsed = int(time.time() - start_time)

                try:
                    pod = self.v1.read_namespaced_pod(pod_name, self.namespace)
                except k8s_client.exceptions.ApiException as e:
                    if e.status == 404:
                        live.update(Text(f"   [{elapsed}s] Scheduling pod...", style="dim"))
                        time.sleep(3)
                        continue
                    raise

                phase = pod.status.phase
                status_detail = self._get_pod_status_detail(pod)
                live.update(Text(f"   [{elapsed}s] {status_detail}", style="dim"))

                if phase == "Running":
                    container_statuses = pod.status.container_statuses or []
                    main_ready = any(
                        cs.name == "dev" and cs.ready
                        for cs in container_statuses
                    )
                    if main_ready:
                        live.update(Text(f"   [{elapsed}s] Pod ready", style="dim"))
                        ann = pod.metadata.annotations or {}
                        if not ann.get(ANN_NODE_IP):
                            self._annotate_connection_info(pod, svc_name)
                            pod = self.v1.read_namespaced_pod(pod_name, self.namespace)
                        return self._pod_to_info(pod)
                elif phase == "Failed":
                    live.stop()
                    for cs in (pod.status.container_statuses or []):
                        if cs.state and cs.state.terminated and cs.state.terminated.exit_code != 0:
                            con.print(
                                f"[red]   Container '{cs.name}' failed "
                                f"(exit code {cs.state.terminated.exit_code})[/red]"
                            )
                    return None
                elif phase == "Succeeded":
                    return None

                time.sleep(3)

        con.print(f"[yellow]   Timed out after {timeout_seconds}s[/yellow]")
        return None

    def _get_pod_status_detail(self, pod) -> str:
        """Extract a human-readable status from pod conditions and container statuses."""
        # Check init containers first
        for cs in (pod.status.init_container_statuses or []):
            if cs.state:
                if cs.state.running:
                    return f"Init container '{cs.name}': running (SSH setup)"
                if cs.state.waiting:
                    reason = cs.state.waiting.reason or "waiting"
                    if reason == "PodInitializing":
                        # Init done, main container starting — check if pulling
                        return self._check_image_pull_status(pod)
                    elif reason == "ContainerCreating":
                        return "Creating init container..."
                    elif "Pull" in reason:
                        return "Pulling init image (alpine:3.19)..."
                    return f"Init '{cs.name}': {reason}"

        # Check main containers
        for cs in (pod.status.container_statuses or []):
            if cs.state:
                if cs.state.waiting:
                    reason = cs.state.waiting.reason or "waiting"
                    if reason == "ContainerCreating":
                        return self._check_image_pull_status(pod)
                    elif reason == "PodInitializing":
                        return self._check_image_pull_status(pod)
                    elif "Pull" in reason:
                        return "Pulling dev image..."
                    elif reason == "CrashLoopBackOff":
                        return "Container crashed — check logs"
                    return f"Container '{cs.name}': {reason}"
                if cs.state.running and not cs.ready:
                    return "Container starting (sshd initializing)..."

        phase = pod.status.phase or "Unknown"
        return f"Phase: {phase}"

    def _check_image_pull_status(self, pod) -> str:
        """Check K8s events for image pull progress."""
        try:
            events = self.v1.list_namespaced_event(
                namespace=self.namespace,
                field_selector=f"involvedObject.name={pod.metadata.name}",
            )
            # Get most recent Pulling/Pulled event
            pull_events = [
                e for e in events.items
                if e.reason in ("Pulling", "Pulled")
            ]
            if pull_events:
                latest = sorted(pull_events, key=lambda e: e.last_timestamp or e.event_time or "", reverse=True)[0]
                if latest.reason == "Pulling":
                    img = latest.message.replace("Pulling image ", "").strip('"')
                    return f"Pulling image: {img}"
                elif latest.reason == "Pulled":
                    msg = latest.message
                    # Extract pull duration if available
                    if " in " in msg:
                        duration = msg.split(" in ")[1].split(" (")[0]
                        return f"Image pulled ({duration}), starting container..."
                    return "Image pulled, starting container..."
        except Exception:
            pass
        return "Pulling dev image (first pull may take 1-3 min for large images)..."

    # ------------------------------------------------------------------
    # list_reservations
    # ------------------------------------------------------------------

    def list_reservations(
        self,
        user_filter: Optional[str] = None,
        statuses_to_include: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """List dev pods managed by gpu-dev-cli, optionally filtered."""
        label_selector = f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
        if user_filter:
            label_selector += f",{LABEL_USER}={user_filter}"

        try:
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace, label_selector=label_selector
            )
        except k8s_client.exceptions.ApiException as e:
            _raise_friendly(e, "list pods")

        results = []
        for pod in pods.items:
            info = self._pod_to_info(pod)
            if statuses_to_include:
                if info.get("status", "unknown") not in statuses_to_include:
                    continue
            results.append(info)

        return results

    # ------------------------------------------------------------------
    # cancel
    # ------------------------------------------------------------------

    def cancel(self, reservation_id: str, user_id: str) -> bool:
        """Delete the dev Pod + Service for a reservation."""
        info = self._find_pod_by_id(reservation_id)
        if not info:
            return False

        # Verify ownership
        owner = info.get("user_id", "")
        if owner and owner != user_id:
            raise RuntimeError(f"Reservation owned by '{owner}', not '{user_id}'")

        pod_name = info["pod_name"]
        full_res_id = info["reservation_id"]
        svc_name = f"gpu-dev-svc-{full_res_id}"

        deleted = False

        # Delete pod (service is garbage-collected via ownerReference)
        try:
            self.v1.delete_namespaced_pod(pod_name, self.namespace)
            deleted = True
        except k8s_client.exceptions.ApiException as e:
            if e.status != 404:
                _raise_friendly(e, "delete pod")

        # Also delete service explicitly (in case ownerRef wasn't set)
        try:
            self.v1.delete_namespaced_service(svc_name, self.namespace)
        except k8s_client.exceptions.ApiException:
            pass  # 404 or GC'd — fine

        return deleted

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------

    def show(self, reservation_id: str) -> Optional[Dict[str, Any]]:
        """Get detailed info for a single reservation.

        Supports both full reservation IDs and short prefixes (8+ chars).
        """
        return self._find_pod_by_id(reservation_id)

    # ------------------------------------------------------------------
    # avail
    # ------------------------------------------------------------------

    def avail(self) -> Dict[str, Dict[str, Any]]:
        """Compute available resources per GPU type from cluster nodes.

        For GPU types: counts nvidia.com/gpu resources from node labels.
        For CPU types: computes how many pods of each size fit based on
        actual allocatable CPU/memory minus what's already in use.
        """
        try:
            nodes = self.v1.list_node()
        except k8s_client.exceptions.ApiException as e:
            _raise_friendly(e, "list nodes")

        # Sum allocatable resources across schedulable worker nodes
        total_gpus_by_type: Dict[str, int] = {}
        total_cpu_millicores = 0
        total_memory_bytes = 0

        for node in nodes.items:
            if node.spec.unschedulable:
                continue
            taints = node.spec.taints or []
            if any(t.effect == "NoSchedule" for t in taints):
                continue

            allocatable = node.status.allocatable or {}

            # CPU: parse "252" (cores) or "252000m" (millicores)
            cpu_str = allocatable.get("cpu", "0")
            if cpu_str.endswith("m"):
                total_cpu_millicores += int(cpu_str[:-1])
            else:
                total_cpu_millicores += int(cpu_str) * 1000

            # Memory: parse "328908728Ki" etc.
            total_memory_bytes += _parse_k8s_memory(allocatable.get("memory", "0"))

            # GPUs
            gpu_alloc = int(allocatable.get("nvidia.com/gpu", "0"))
            if gpu_alloc > 0:
                node_labels = node.metadata.labels or {}
                product = (
                    node_labels.get("nvidia.com/gpu.product", "")
                    .lower().replace(" ", "-")
                )
                mapped = NODE_GPU_LABEL_MAP.get(product)
                if mapped:
                    total_gpus_by_type[mapped] = (
                        total_gpus_by_type.get(mapped, 0) + gpu_alloc
                    )

        # Count in-use resources from running gpu-dev pods
        in_use_by_type: Dict[str, int] = {}
        used_cpu_millicores = 0
        used_memory_bytes = 0
        in_use_cpu_by_type: Dict[str, int] = {}
        try:
            label_selector = f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace, label_selector=label_selector
            )
            for pod in pods.items:
                if pod.status.phase not in ("Running", "Pending"):
                    continue
                pod_gpu_type = (pod.metadata.labels or {}).get(LABEL_GPU_TYPE, "")
                for container in (pod.spec.containers or []):
                    reqs = (container.resources.requests or {}) if container.resources else {}
                    # Track GPU usage
                    gpu_req = int(reqs.get("nvidia.com/gpu", "0"))
                    if gpu_req > 0:
                        in_use_by_type[pod_gpu_type] = (
                            in_use_by_type.get(pod_gpu_type, 0) + gpu_req
                        )
                    # Track CPU/mem usage for capacity calculation
                    cpu_req = reqs.get("cpu", "0")
                    if cpu_req.endswith("m"):
                        used_cpu_millicores += int(cpu_req[:-1])
                    else:
                        used_cpu_millicores += int(cpu_req) * 1000
                    used_memory_bytes += _parse_k8s_memory(reqs.get("memory", "0"))
                    if pod_gpu_type.startswith("cpu-"):
                        in_use_cpu_by_type[pod_gpu_type] = (
                            in_use_cpu_by_type.get(pod_gpu_type, 0) + 1
                        )
        except k8s_client.exceptions.ApiException:
            pass

        # Available resources
        free_cpu_millicores = max(0, total_cpu_millicores - used_cpu_millicores)
        free_memory_bytes = max(0, total_memory_bytes - used_memory_bytes)

        # Build report
        availability: Dict[str, Dict[str, Any]] = {}
        for gpu_type, cfg in GPU_CONFIG.items():
            if cfg["max_gpus"] == 0:
                # CPU types: how many pods fit in remaining resources?
                # Use requests (not limits) since K8s schedules on requests
                req_cpu_mc = int(cfg["cpu_request"]) * 1000
                req_mem_bytes = cfg["mem_request_gb"] * 1024 * 1024 * 1024
                fits_by_cpu = free_cpu_millicores // req_cpu_mc if req_cpu_mc > 0 else 0
                fits_by_mem = free_memory_bytes // req_mem_bytes if req_mem_bytes > 0 else 0
                can_fit = min(fits_by_cpu, fits_by_mem)

                # Total capacity (if nothing else was running)
                total_by_cpu = total_cpu_millicores // req_cpu_mc if req_cpu_mc > 0 else 0
                total_by_mem = total_memory_bytes // req_mem_bytes if req_mem_bytes > 0 else 0
                total_capacity = min(total_by_cpu, total_by_mem)

                in_use = in_use_cpu_by_type.get(gpu_type, 0)

                availability[gpu_type] = {
                    "gpu_type": gpu_type,
                    "total": total_capacity,
                    "available": can_fit,
                    "in_use": in_use,
                    "max_per_node": 0,
                    "is_cpu": True,
                }
            else:
                total = total_gpus_by_type.get(gpu_type, 0)
                in_use = in_use_by_type.get(gpu_type, 0)
                availability[gpu_type] = {
                    "gpu_type": gpu_type,
                    "total": total,
                    "available": max(0, total - in_use),
                    "in_use": in_use,
                    "max_per_node": cfg["max_gpus"],
                    "is_cpu": False,
                }

        return availability

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self) -> Dict[str, Any]:
        """Aggregate cluster-wide resource stats."""
        availability = self.avail()

        total_gpus = sum(v["total"] for v in availability.values() if not v.get("is_cpu"))
        in_use_gpus = sum(v["in_use"] for v in availability.values() if not v.get("is_cpu"))
        available_gpus = sum(v["available"] for v in availability.values() if not v.get("is_cpu"))

        active = 0
        pending = 0
        try:
            label_selector = f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace, label_selector=label_selector
            )
            active = sum(1 for p in pods.items if p.status.phase == "Running")
            pending = sum(1 for p in pods.items if p.status.phase == "Pending")
        except k8s_client.exceptions.ApiException:
            pass

        return {
            "total_gpus": total_gpus,
            "available_gpus": available_gpus,
            "reserved_gpus": in_use_gpus,
            "active_reservations": active,
            "queue_length": pending,
        }

    # ------------------------------------------------------------------
    # build_image
    # ------------------------------------------------------------------

    def build_image(
        self,
        build_context_b64: str,
        registry_repo: str,
        tag: str = "v1",
    ) -> str:
        """Create a BuildKit Job that builds a Dockerfile and pushes to a registry.

        Registry auth: expects a K8s Secret named 'registry-push-secret'
        (type kubernetes.io/dockerconfigjson) in the gpu-dev namespace.
        If not found, checks 'ecr-pull-secret' in gpu-dev and gpu-controlplane.

        Create the secret however fits your environment:
          # AWS ECR:
          kubectl create secret docker-registry registry-push-secret -n gpu-dev \\
            --docker-server=YOUR_REGISTRY --docker-username=AWS \\
            --docker-password="$(aws ecr get-login-password --region us-east-2)"

          # Docker Hub / generic:
          kubectl create secret docker-registry registry-push-secret -n gpu-dev \\
            --docker-server=https://index.docker.io/v1/ \\
            --docker-username=USER --docker-password=TOKEN

        Returns the job name.
        """
        import hashlib

        context_hash = hashlib.sha256(build_context_b64.encode()).hexdigest()[:12]
        job_name = f"buildkit-{tag}-{context_hash}"
        full_image = f"{registry_repo}:{tag}"

        batch_v1 = k8s_client.BatchV1Api(self.api_client)

        # Check if job already exists
        try:
            existing = batch_v1.read_namespaced_job(job_name, self.namespace)
            if existing.status.succeeded:
                return job_name
            if existing.status.active:
                return job_name
            batch_v1.delete_namespaced_job(
                job_name, self.namespace, propagation_policy="Background"
            )
            time.sleep(3)
        except k8s_client.exceptions.ApiException as e:
            if e.status != 404:
                _raise_friendly(e, "check buildkit job")

        # Find registry auth secret
        secret_name = self._find_registry_secret()
        if not secret_name:
            raise RuntimeError(
                "No registry auth secret found.\n\n"
                "Create one with:\n"
                "  kubectl create secret docker-registry registry-push-secret \\\n"
                "    -n gpu-dev --docker-server=YOUR_REGISTRY \\\n"
                "    --docker-username=USER --docker-password=TOKEN\n\n"
                "Or for AWS ECR:\n"
                "  kubectl create secret docker-registry registry-push-secret \\\n"
                "    -n gpu-dev --docker-server=ACCOUNT.dkr.ecr.REGION.amazonaws.com \\\n"
                '    --docker-username=AWS --docker-password="$(aws ecr get-login-password)"'
            )

        build_script = f"""
set -ex
echo "[BUILDKIT] Starting build for {full_image}"

echo "[BUILDKIT] Extracting build context..."
echo "{build_context_b64}" | base64 -d > /tmp/ctx.tar.gz
mkdir -p /tmp/work && cd /tmp/work
tar -xzf /tmp/ctx.tar.gz
echo "[BUILDKIT] Build context:"
ls -la

echo "[BUILDKIT] Building image..."
buildctl-daemonless.sh build \
  --frontend dockerfile.v0 \
  --local context=/tmp/work \
  --local dockerfile=/tmp/work \
  --output type=image,name={full_image},push=true

echo "[BUILDKIT] Done: {full_image}"
"""

        container = k8s_client.V1Container(
            name="buildkit",
            image="moby/buildkit:v0.21.1",
            command=["/bin/sh", "-c", build_script],
            volume_mounts=[
                k8s_client.V1VolumeMount(
                    name="docker-config",
                    mount_path="/root/.docker",
                    read_only=True,
                ),
            ],
            security_context=k8s_client.V1SecurityContext(privileged=True),
            resources=k8s_client.V1ResourceRequirements(
                requests={"cpu": "4", "memory": "8Gi"},
                limits={"cpu": "16", "memory": "32Gi"},
            ),
        )

        job = k8s_client.V1Job(
            api_version="batch/v1",
            kind="Job",
            metadata=k8s_client.V1ObjectMeta(
                name=job_name,
                namespace=self.namespace,
                labels={"app": "buildkit", "build-hash": context_hash},
            ),
            spec=k8s_client.V1JobSpec(
                template=k8s_client.V1PodTemplateSpec(
                    metadata=k8s_client.V1ObjectMeta(
                        labels={"app": "buildkit", "build-hash": context_hash},
                    ),
                    spec=k8s_client.V1PodSpec(
                        containers=[container],
                        volumes=[
                            k8s_client.V1Volume(
                                name="docker-config",
                                secret=k8s_client.V1SecretVolumeSource(
                                    secret_name=secret_name,
                                    items=[
                                        k8s_client.V1KeyToPath(
                                            key=".dockerconfigjson",
                                            path="config.json",
                                        ),
                                    ],
                                ),
                            ),
                        ],
                        restart_policy="Never",
                        service_account_name="buildkit-service-account",
                    ),
                ),
                backoff_limit=1,
                ttl_seconds_after_finished=3600,
            ),
        )

        try:
            batch_v1.create_namespaced_job(namespace=self.namespace, body=job)
        except k8s_client.exceptions.ApiException as e:
            _raise_friendly(e, "create buildkit job")

        return job_name

    def wait_for_build(
        self,
        job_name: str,
        timeout_seconds: int = 1800,
        progress_callback=None,
    ) -> Dict[str, Any]:
        """Wait for a BuildKit job to complete, streaming progress."""
        import re

        batch_v1 = k8s_client.BatchV1Api(self.api_client)
        deadline = time.time() + timeout_seconds
        last_line_count = 0
        shown_lines = set()

        while time.time() < deadline:
            try:
                job = batch_v1.read_namespaced_job(job_name, self.namespace)
            except k8s_client.exceptions.ApiException:
                time.sleep(5)
                continue

            if job.status.succeeded:
                return {"success": True, "message": "Image built and pushed"}

            if job.status.failed:
                logs = self._get_job_logs(job_name, tail=100)
                return {"success": False, "message": "Build failed", "logs": logs}

            # Stream progress from logs
            if progress_callback:
                logs = self._get_job_logs(job_name, tail=80)
                if logs:
                    lines = logs.strip().split("\n")
                    new_lines = lines[last_line_count:] if last_line_count < len(lines) else lines[-5:]
                    last_line_count = len(lines)

                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue

                        # Parse BuildKit progress into readable messages
                        msg = self._parse_buildkit_line(line)
                        if msg and msg not in shown_lines:
                            shown_lines.add(msg)
                            progress_callback(msg)

            time.sleep(8)

        return {"success": False, "message": f"Build timed out after {timeout_seconds}s"}

    @staticmethod
    def _parse_buildkit_line(line: str) -> Optional[str]:
        """Parse a BuildKit log line into a human-readable progress message."""
        import re

        # Our own echo markers
        if "[BUILDKIT]" in line:
            return line[line.index("[BUILDKIT]"):]

        # BuildKit step progress: "#7 [ 3/11] RUN apt-get update"
        step_match = re.search(r'#\d+\s+\[\s*(\d+)/(\d+)\]\s+(.+)', line)
        if step_match:
            cur, total, cmd = step_match.groups()
            cmd = cmd.strip()
            return f"Step {cur}/{total}: {cmd}"

        # Layer download: "sha256:abc... 245.2MB / 1.2GB"
        dl_match = re.search(r'([\d.]+[KMGT]?B)\s*/\s*([\d.]+[KMGT]?B)', line)
        if dl_match and ("sha256:" in line or "downloading" in line.lower()):
            cur, total = dl_match.groups()
            return f"Downloading: {cur} / {total}"

        # Extracting layers
        if "extracting sha256:" in line.lower():
            if "done" in line.lower():
                return "Extracting layers... done"
            return "Extracting layers..."

        # Exporting/pushing
        if "exporting to image" in line.lower():
            return "Exporting image..."
        if "pushing" in line.lower() and ("layer" in line.lower() or "manifest" in line.lower()):
            return "Pushing to registry..."
        if "exporting cache" in line.lower():
            return "Exporting build cache..."

        # CACHED steps
        if "CACHED" in line:
            step_match = re.search(r'#\d+\s+CACHED\s+\[\s*(\d+)/(\d+)\]\s+(.+)', line)
            if step_match:
                cur, total, cmd = step_match.groups()
                cmd = cmd.strip()
                return f"Step {cur}/{total}: {cmd} (cached)"

        # FROM / base image
        if "resolve image config" in line.lower() or "load metadata" in line.lower():
            return "Resolving base image..."

        # Generic DONE markers
        if line.startswith("#") and "DONE" in line:
            return None  # skip noise

        return None

    def _get_job_logs(self, job_name: str, tail: int = 50) -> str:
        """Get logs from the first pod of a Job."""
        try:
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace,
                label_selector=f"job-name={job_name}",
            )
            if pods.items:
                return self.v1.read_namespaced_pod_log(
                    pods.items[0].metadata.name,
                    self.namespace,
                    tail_lines=tail,
                )
        except Exception:
            pass
        return ""

    # ------------------------------------------------------------------
    # get_ssh_pubkey
    # ------------------------------------------------------------------

    @staticmethod
    def get_ssh_pubkey() -> str:
        """Read the user's default SSH public key."""
        from pathlib import Path

        for key_name in ("id_ed25519.pub", "id_rsa.pub", "id_ecdsa.pub"):
            key_path = Path.home() / ".ssh" / key_name
            if key_path.exists():
                return key_path.read_text().strip()
        return ""

    @staticmethod
    def create_ssh_config(
        reservation_id: str,
        node_ip: str,
        ssh_port: str,
        pod_name: str,
        username: Optional[str] = None,
    ) -> Optional[str]:
        """Create SSH config file in ~/.gpu-dev/ for direct SSH access.

        Creates a config so `ssh gpu-dev-<id>` works without remembering IP:port.
        Also adds Include directive to ~/.ssh/config if user approves.

        Returns the config file path, or None on error.
        """
        from pathlib import Path

        if username is None:
            username = _os.getenv("USER", "dev")

        gpu_dev_dir = Path.home() / ".gpu-dev"
        gpu_dev_dir.mkdir(mode=0o700, exist_ok=True)

        short_id = reservation_id[:8]
        filename = f"{short_id}-sshconfig"
        config_file = gpu_dev_dir / filename

        # Direct SSH via NodePort — works from any machine with network access
        # to the cluster nodes. Also add ProxyCommand alias for VS Code Remote.
        config_content = f"""Host {pod_name}
    HostName {node_ip}
    Port {ssh_port}
    User {username}
    ForwardAgent yes
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null

# Alias with ProxyCommand (for VS Code Remote or when direct SSH is blocked)
Host {pod_name}-proxy
    HostName {pod_name}
    User {username}
    ForwardAgent yes
    ProxyCommand gpu-dev-ssh-proxy %h %p
    StrictHostKeyChecking no
    UserKnownHostsFile /dev/null
"""

        try:
            config_file.write_text(config_content)
            config_file.chmod(0o600)

            # Ensure ~/.ssh/config includes gpu-dev configs
            _ensure_ssh_include()

            return str(config_file)
        except Exception:
            return None

    @staticmethod
    def remove_ssh_config(reservation_id: str) -> bool:
        """Remove SSH config file for a reservation."""
        from pathlib import Path

        short_id = reservation_id[:8]
        config_file = Path.home() / ".gpu-dev" / f"{short_id}-sshconfig"
        try:
            if config_file.exists():
                config_file.unlink()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # pod/service builders
    # ------------------------------------------------------------------

    def _build_pod_spec(
        self,
        pod_name: str,
        labels: Dict[str, str],
        annotations: Dict[str, str],
        image: str,
        ssh_pubkey: str,
        gpu_count: int,
        hours: float,
        cfg: Dict[str, Any],
        username: str = "dev",
        uid: int = 1081,
        gid: int = 1081,
    ) -> k8s_client.V1Pod:
        """Build the dev Pod spec with init container for SSH setup."""

        home_path = f"/home/{username}"

        # Resource requirements
        limits: Dict[str, str] = {
            "cpu": str(cfg["cpus"]),
            "memory": f"{cfg['memory_gb']}Gi",
        }
        requests: Dict[str, str] = {
            "cpu": cfg["cpu_request"],
            "memory": f"{cfg['mem_request_gb']}Gi",
        }
        if gpu_count > 0:
            limits["nvidia.com/gpu"] = str(gpu_count)
            requests["nvidia.com/gpu"] = str(gpu_count)

        # User identity env vars — used by both init and main containers
        user_env_vars = [
            k8s_client.V1EnvVar(name="DEV_USER", value=username),
            k8s_client.V1EnvVar(name="DEV_UID", value=str(uid)),
            k8s_client.V1EnvVar(name="DEV_GID", value=str(gid)),
            k8s_client.V1EnvVar(name="DEV_SHELL", value="/bin/zsh"),
        ]

        # Init container: set up SSH using env var (no shell injection)
        init_container = k8s_client.V1Container(
            name="ssh-setup",
            image="alpine:3.19",
            command=["/bin/sh", "-c", self._init_script()],
            env=[
                k8s_client.V1EnvVar(name="SSH_PUBKEY", value=ssh_pubkey),
            ] + user_env_vars,
            volume_mounts=[
                k8s_client.V1VolumeMount(name="dev-home", mount_path=home_path),
                k8s_client.V1VolumeMount(name="ssh-config", mount_path="/etc/ssh-gpu-dev"),
            ],
        )

        # Main container
        main_container = k8s_client.V1Container(
            name="dev",
            image=image,
            command=["/bin/sh", "-c", self._main_script()],
            env=user_env_vars,
            ports=[
                k8s_client.V1ContainerPort(container_port=22, name="ssh"),
                k8s_client.V1ContainerPort(container_port=8888, name="jupyter"),
            ],
            resources=k8s_client.V1ResourceRequirements(
                limits=limits,
                requests=requests,
            ),
            volume_mounts=[
                k8s_client.V1VolumeMount(name="dev-home", mount_path=home_path),
                k8s_client.V1VolumeMount(name="workspace", mount_path="/workspace"),
                k8s_client.V1VolumeMount(name="dshm", mount_path="/dev/shm"),
                k8s_client.V1VolumeMount(name="ssh-config", mount_path="/etc/ssh-gpu-dev"),
            ],
        )

        return k8s_client.V1Pod(
            metadata=k8s_client.V1ObjectMeta(
                name=pod_name,
                namespace=self.namespace,
                labels=labels,
                annotations=annotations,
            ),
            spec=k8s_client.V1PodSpec(
                init_containers=[init_container],
                containers=[main_container],
                active_deadline_seconds=int(hours * 3600),
                restart_policy="Never",
                volumes=[
                    k8s_client.V1Volume(name="dev-home",
                                        empty_dir=k8s_client.V1EmptyDirVolumeSource()),
                    k8s_client.V1Volume(name="workspace",
                                        empty_dir=k8s_client.V1EmptyDirVolumeSource()),
                    k8s_client.V1Volume(name="dshm",
                                        empty_dir=k8s_client.V1EmptyDirVolumeSource(
                                            medium="Memory")),
                    k8s_client.V1Volume(name="ssh-config",
                                        empty_dir=k8s_client.V1EmptyDirVolumeSource()),
                ],
            ),
        )

    @staticmethod
    def _init_script() -> str:
        """Shell script for init container.

        Sets up SSH keys, host keys, and sshd_config in /etc/ssh-gpu-dev/.
        Also creates dev user home with SSH authorized_keys.
        """
        return r"""#!/bin/sh
set -e
apk add --no-cache openssh >/dev/null 2>&1

# Create user with caller's UID (DEV_USER/DEV_UID from env)
adduser -D -u "$DEV_UID" -s /bin/sh "$DEV_USER" 2>/dev/null || true
mkdir -p /home/$DEV_USER/.ssh

# Write pubkey from env var
printf '%s\n' "$SSH_PUBKEY" > /home/$DEV_USER/.ssh/authorized_keys

chmod 700 /home/$DEV_USER/.ssh
chmod 600 /home/$DEV_USER/.ssh/authorized_keys
chown -R $DEV_UID:$DEV_UID /home/$DEV_USER/.ssh

# Generate host keys into the shared volume
mkdir -p /etc/ssh-gpu-dev
ssh-keygen -A
cp /etc/ssh/ssh_host_* /etc/ssh-gpu-dev/

# Write sshd_config
cat > /etc/ssh-gpu-dev/sshd_config <<'SSHEOF'
Port 22
PermitRootLogin no
PubkeyAuthentication yes
PasswordAuthentication no
AuthorizedKeysFile .ssh/authorized_keys
HostKey /etc/ssh-gpu-dev/ssh_host_rsa_key
HostKey /etc/ssh-gpu-dev/ssh_host_ecdsa_key
HostKey /etc/ssh-gpu-dev/ssh_host_ed25519_key
Subsystem sftp /usr/lib/openssh/sftp-server
SSHEOF

echo 'SSH setup complete'
"""

    @staticmethod
    def _main_script() -> str:
        """Shell script for main container.

        Works with two scenarios:
        1. Custom OSDC image (ssh, zsh, dev user all baked in) → just start sshd
        2. Vanilla pytorch/ubuntu image → install packages first, then start sshd

        The script detects which case it is by checking if sshd already exists.
        """
        return r"""#!/bin/sh

# --- Fast path: MSL runtime image with entrypoint-user.sh ---
# The entrypoint creates the user from DEV_USER/DEV_UID/DEV_GID env vars,
# gives sudo, then execs the given command. Zero setup time.
if [ -x /usr/local/bin/entrypoint-user.sh ]; then
  echo "MSL runtime image detected — fast startup via entrypoint-user.sh"
  exec /usr/local/bin/entrypoint-user.sh /usr/sbin/sshd -D -e -f /etc/ssh-gpu-dev/sshd_config
fi

# --- If sshd is already installed (custom OSDC image), fast path ---
if which sshd >/dev/null 2>&1 && id "$DEV_USER" >/dev/null 2>&1; then
  echo "Custom image detected — fast startup"
  passwd -u "$DEV_USER" 2>/dev/null || usermod -p '*' "$DEV_USER" 2>/dev/null || true
  echo "$DEV_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/dev-user 2>/dev/null || true
  mkdir -p /run/sshd
  exec /usr/sbin/sshd -D -e -f /etc/ssh-gpu-dev/sshd_config
fi

# --- Vanilla image: install everything ---
echo "Setting up dev environment..."

if which apt-get >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    openssh-server sudo zsh git curl wget vim htop tmux \
    2>&1 | tail -3
elif which apk >/dev/null 2>&1; then
  apk add --no-cache openssh-server sudo zsh git curl wget vim htop tmux \
    2>&1 | tail -3
elif which yum >/dev/null 2>&1; then
  yum install -y openssh-server sudo zsh git curl wget vim htop tmux \
    2>&1 | tail -3
fi

# Create user with caller's identity
SHELL_PATH=$(which zsh 2>/dev/null || echo /bin/bash)
id "$DEV_USER" >/dev/null 2>&1 || {
  useradd -m -u "$DEV_UID" -s "$SHELL_PATH" "$DEV_USER" 2>/dev/null || \
  adduser -D -u "$DEV_UID" -s "$SHELL_PATH" "$DEV_USER" 2>/dev/null || true
}
passwd -u "$DEV_USER" 2>/dev/null || usermod -p '*' "$DEV_USER" 2>/dev/null || true
echo "$DEV_USER ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/dev-user 2>/dev/null || true

# Add user to same groups as other users (for conda, cuda access)
for g in sudo root conda; do
  groupadd -f "$g" 2>/dev/null; usermod -aG "$g" "$DEV_USER" 2>/dev/null
done

# Set up PATH for user — conda, cuda, pip bins
cat > /etc/profile.d/gpu-dev.sh << PATHEOF
export PATH=/opt/conda/bin:/usr/local/cuda/bin:/home/$DEV_USER/.local/bin:\$PATH
export LD_LIBRARY_PATH=/usr/local/cuda/lib64:\${LD_LIBRARY_PATH:-}
export CUDA_HOME=/usr/local/cuda
PATHEOF
chmod 644 /etc/profile.d/gpu-dev.sh

# oh-my-zsh
if which zsh >/dev/null 2>&1 && [ ! -d /home/$DEV_USER/.oh-my-zsh ]; then
  echo "Installing oh-my-zsh..."
  su - "$DEV_USER" -c 'sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended' 2>/dev/null || true
  su - "$DEV_USER" -c '
    git clone --depth=1 https://github.com/zsh-users/zsh-autosuggestions ~/.oh-my-zsh/custom/plugins/zsh-autosuggestions 2>/dev/null
    git clone --depth=1 https://github.com/zsh-users/zsh-syntax-highlighting ~/.oh-my-zsh/custom/plugins/zsh-syntax-highlighting 2>/dev/null
  ' 2>/dev/null || true
  if [ -f /home/$DEV_USER/.zshrc ]; then
    sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting)/' /home/$DEV_USER/.zshrc 2>/dev/null
    # Source PATH setup in zshrc
    echo 'source /etc/profile.d/gpu-dev.sh' >> /home/$DEV_USER/.zshrc
    # Fix: remove any recursive exit function that oh-my-zsh might create
    sed -i '/^function exit/,/^}/d' /home/$DEV_USER/.zshrc 2>/dev/null
  fi
fi

# sshd user
id sshd >/dev/null 2>&1 || {
  useradd -r -s /usr/sbin/nologin -d /run/sshd sshd 2>/dev/null || \
  adduser -S -D -H -s /sbin/nologin -g sshd sshd 2>/dev/null || true
}

if ! which sshd >/dev/null 2>&1; then
  echo "WARNING: sshd not available — falling back to sleep (use kubectl exec)"
  exec sleep infinity
fi

mkdir -p /run/sshd /var/empty
echo "Dev environment ready. Starting sshd..."
exec /usr/sbin/sshd -D -e -f /etc/ssh-gpu-dev/sshd_config
"""

    def _build_service_spec(
        self,
        svc_name: str,
        labels: Dict[str, str],
        reservation_id: str,
        owner_pod: k8s_client.V1Pod,
    ) -> k8s_client.V1Service:
        """Build NodePort Service spec with ownerReference to the Pod.

        When the Pod is deleted (cancel or activeDeadlineSeconds expiry),
        K8s garbage-collects the Service automatically.
        """
        return k8s_client.V1Service(
            metadata=k8s_client.V1ObjectMeta(
                name=svc_name,
                namespace=self.namespace,
                labels=labels,
                owner_references=[
                    k8s_client.V1OwnerReference(
                        api_version="v1",
                        kind="Pod",
                        name=owner_pod.metadata.name,
                        uid=owner_pod.metadata.uid,
                        block_owner_deletion=False,
                    ),
                ],
            ),
            spec=k8s_client.V1ServiceSpec(
                type="NodePort",
                selector={LABEL_RESERVATION_ID: reservation_id},
                ports=[
                    k8s_client.V1ServicePort(name="ssh", port=22, target_port=22),
                    k8s_client.V1ServicePort(name="jupyter", port=8888, target_port=8888),
                ],
            ),
        )

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def get_cluster_config(self, key: str) -> Optional[str]:
        """Read a value from the gpu-dev-config ConfigMap."""
        try:
            cm = self.v1.read_namespaced_config_map("gpu-dev-config", self.namespace)
            return (cm.data or {}).get(key) or None
        except k8s_client.exceptions.ApiException:
            return None

    def set_cluster_config(self, key: str, value: str) -> None:
        """Set a value in the gpu-dev-config ConfigMap. Creates it if needed."""
        cm_name = "gpu-dev-config"
        try:
            cm = self.v1.read_namespaced_config_map(cm_name, self.namespace)
            if cm.data is None:
                cm.data = {}
            cm.data[key] = value
            self.v1.replace_namespaced_config_map(cm_name, self.namespace, cm)
        except k8s_client.exceptions.ApiException as e:
            if e.status == 404:
                cm = k8s_client.V1ConfigMap(
                    metadata=k8s_client.V1ObjectMeta(name=cm_name, namespace=self.namespace),
                    data={key: value},
                )
                self.v1.create_namespaced_config_map(self.namespace, cm)
            else:
                _raise_friendly(e, "set cluster config")

    def list_available_images(self) -> Dict[str, str]:
        """Read available images from the gpu-dev-images ConfigMap.

        Returns dict of {label: image_uri}.
        The ConfigMap is written by `gpu-dev build-image`.
        """
        try:
            cm = self.v1.read_namespaced_config_map("gpu-dev-images", self.namespace)
            return dict(cm.data) if cm.data else {}
        except k8s_client.exceptions.ApiException:
            return {}

    def save_built_image(self, label: str, image_uri: str) -> None:
        """Save a built image to the gpu-dev-images ConfigMap.

        Creates the ConfigMap if it doesn't exist.
        """
        cm_name = "gpu-dev-images"
        try:
            cm = self.v1.read_namespaced_config_map(cm_name, self.namespace)
            if cm.data is None:
                cm.data = {}
            cm.data[label] = image_uri
            self.v1.replace_namespaced_config_map(cm_name, self.namespace, cm)
        except k8s_client.exceptions.ApiException as e:
            if e.status == 404:
                cm = k8s_client.V1ConfigMap(
                    metadata=k8s_client.V1ObjectMeta(name=cm_name, namespace=self.namespace),
                    data={label: image_uri},
                )
                self.v1.create_namespaced_config_map(self.namespace, cm)
            else:
                pass  # Non-critical, don't fail the build

    def _find_registry_secret(self) -> Optional[str]:
        """Find a docker config secret for registry auth.

        Checks for these secrets in order:
        1. 'registry-push-secret' in gpu-dev namespace
        2. 'ecr-pull-secret' in gpu-dev namespace
        3. 'ecr-pull-secret' in gpu-controlplane namespace (copies to gpu-dev)

        Returns the secret name in gpu-dev namespace, or None.
        """
        # Check gpu-dev namespace first
        for name in ["registry-push-secret", "ecr-pull-secret"]:
            try:
                self.v1.read_namespaced_secret(name, self.namespace)
                return name
            except k8s_client.exceptions.ApiException:
                continue

        # Check gpu-controlplane and copy if found
        try:
            src = self.v1.read_namespaced_secret("ecr-pull-secret", "gpu-controlplane")
            copy = k8s_client.V1Secret(
                metadata=k8s_client.V1ObjectMeta(
                    name="ecr-pull-secret",
                    namespace=self.namespace,
                ),
                type=src.type,
                data=src.data,
            )
            try:
                self.v1.create_namespaced_secret(self.namespace, copy)
            except k8s_client.exceptions.ApiException as e:
                if e.status != 409:
                    return None
            return "ecr-pull-secret"
        except k8s_client.exceptions.ApiException:
            pass

        return None

    def _find_pod_by_id(self, reservation_id: str) -> Optional[Dict[str, Any]]:
        """Find a dev pod by full or partial reservation ID.

        Tries exact pod name first, then falls back to label-selector
        search for prefix matches.
        """
        # Try exact match first (fastest)
        pod_name = f"gpu-dev-{reservation_id}"
        try:
            pod = self.v1.read_namespaced_pod(pod_name, self.namespace)
            return self._pod_to_info(pod)
        except k8s_client.exceptions.ApiException as e:
            if e.status != 404:
                _raise_friendly(e, "read pod")

        # Prefix search: list all managed pods and match by label
        try:
            label_selector = f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE}"
            pods = self.v1.list_namespaced_pod(
                namespace=self.namespace, label_selector=label_selector
            )
            matches = []
            for pod in pods.items:
                pod_res_id = (pod.metadata.labels or {}).get(LABEL_RESERVATION_ID, "")
                if pod_res_id.startswith(reservation_id):
                    matches.append(pod)

            if len(matches) == 1:
                return self._pod_to_info(matches[0])
            elif len(matches) > 1:
                names = ", ".join(
                    (p.metadata.labels or {}).get(LABEL_RESERVATION_ID, "")[:8]
                    for p in matches
                )
                raise RuntimeError(
                    f"Ambiguous prefix '{reservation_id}' matches {len(matches)} "
                    f"reservations: {names}. Use a longer prefix."
                )
        except k8s_client.exceptions.ApiException as e:
            _raise_friendly(e, "list pods")

        return None

    def _annotate_connection_info(self, pod, svc_name: str) -> None:
        """Read node IP + NodePort and annotate the pod."""
        node_name = pod.spec.node_name
        if not node_name:
            return

        try:
            node = self.v1.read_node(node_name)
        except k8s_client.exceptions.ApiException:
            return

        node_ip = ""
        for addr in (node.status.addresses or []):
            if addr.type in ("ExternalIP", "InternalIP"):
                node_ip = addr.address
                if addr.type == "ExternalIP":
                    break

        ssh_port = ""
        try:
            svc = self.v1.read_namespaced_service(svc_name, self.namespace)
            for port in (svc.spec.ports or []):
                if port.name == "ssh":
                    ssh_port = str(port.node_port)
        except k8s_client.exceptions.ApiException:
            pass

        if node_ip or ssh_port:
            patch = {"metadata": {"annotations": {
                ANN_NODE_IP: node_ip,
                ANN_SSH_PORT: ssh_port,
            }}}
            try:
                self.v1.patch_namespaced_pod(
                    pod.metadata.name, self.namespace, patch
                )
            except k8s_client.exceptions.ApiException:
                pass

    def _pod_to_info(self, pod) -> Dict[str, Any]:
        """Convert a K8s Pod to the connection info dict used by the CLI."""
        labels = pod.metadata.labels or {}
        annotations = pod.metadata.annotations or {}

        phase = pod.status.phase or "Unknown"
        status_map = {
            "Running": "active",
            "Pending": "preparing",
            "Succeeded": "expired",
            "Failed": "failed",
        }
        cli_status = status_map.get(phase, "unknown")

        reservation_id = labels.get(LABEL_RESERVATION_ID, "")
        node_ip = annotations.get(ANN_NODE_IP, "")
        ssh_port = annotations.get(ANN_SSH_PORT, "")

        # Get username from label, or fall back to DEV_USER env var on containers
        username = labels.get(LABEL_USERNAME, "dev")

        ssh_command = ""
        if node_ip and ssh_port:
            ssh_command = f"ssh -p {ssh_port} {username}@{node_ip}"

        return {
            "reservation_id": reservation_id,
            "pod_name": pod.metadata.name,
            "status": cli_status,
            "user_id": labels.get(LABEL_USER, ""),
            "username": username,
            "gpu_type": labels.get(LABEL_GPU_TYPE, ""),
            "gpu_count": self._get_gpu_count_from_pod(pod),
            "instance_type": labels.get(LABEL_GPU_TYPE, "k8s-direct"),
            "created_at": annotations.get(ANN_CREATED_AT, ""),
            "launched_at": annotations.get(ANN_CREATED_AT, ""),
            "expires_at": annotations.get(ANN_EXPIRES_AT, ""),
            "node_ip": node_ip,
            "node_port": ssh_port,
            "ssh_command": ssh_command,
            "name": pod.metadata.name,
            # Compatibility fields for display code
            "ebs_volume_id": None,
            "disk_name": None,
            "jupyter_enabled": False,
            "jupyter_url": None,
            "secondary_users": [],
            "warning": "",
            "oom_count": 0,
            "last_oom_at": None,
        }

    @staticmethod
    def _get_gpu_count_from_pod(pod) -> int:
        """Extract GPU count from pod resource requests."""
        for container in (pod.spec.containers or []):
            if container.resources and container.resources.requests:
                try:
                    return int(container.resources.requests.get("nvidia.com/gpu", "0"))
                except (ValueError, TypeError):
                    pass
        return 0
