"""Integration: T4 GPU reservation lifecycle on a real (staging) cluster.

reserve t4 (1 GPU) -> active -> nvidia-smi over SSH -> auto-cancel. Staging T4 is
spot with instance_count=0, so the node may scale up (~10 min) — the active wait
timeout is GPU_DEV_TEST_TIMEOUT_MIN (default 15). Skipped unless --run-integration
and the env is reachable.
"""
import pytest

from .conftest import reserved, _ssh_exec

pytestmark = pytest.mark.integration


def test_t4_reserve_nvidia_smi_cancel(manager):
    with reserved(manager, gpu_type="t4", gpu_count=1, hours=0.5) as (rid, conn):
        assert conn.get("ssh_command"), f"no ssh_command for {rid}"
        rc, out = _ssh_exec(
            conn["ssh_command"],
            "nvidia-smi --query-gpu=name --format=csv,noheader || nvidia-smi",
            timeout=180)
        assert rc == 0, out
        assert "T4" in out or "Tesla" in out, out


def test_t4_torch_cuda_available(manager):
    """If torch is present (prebuilt/staged), CUDA should see the T4."""
    with reserved(manager, gpu_type="t4", gpu_count=1, hours=0.5) as (rid, conn):
        rc, out = _ssh_exec(
            conn["ssh_command"],
            "python -c 'import torch; print(\"CUDA\", torch.cuda.is_available())' 2>/dev/null "
            "|| echo NO_TORCH",
            timeout=180)
        assert rc == 0, out
        assert ("CUDA True" in out) or ("NO_TORCH" in out), out
