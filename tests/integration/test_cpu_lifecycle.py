"""Integration: full CPU reservation lifecycle on a real (staging) cluster.

reserve cpu -> become active -> exec a command over SSH -> auto-cancel.
Skipped unless --run-integration AND the target env is reachable.
"""
import pytest

from .conftest import reserved, _ssh_exec

pytestmark = pytest.mark.integration


def test_cpu_reserve_exec_cancel(manager):
    with reserved(manager, gpu_type="cpu-x86", gpu_count=0, hours=0.5) as (rid, conn):
        assert conn.get("ssh_command"), f"no ssh_command for {rid}"
        rc, out = _ssh_exec(conn["ssh_command"], "echo gpu-dev-itest-ok && nproc")
        assert rc == 0, out
        assert "gpu-dev-itest-ok" in out
        nums = [int(x) for x in out.split() if x.isdigit()]
        assert nums and nums[0] >= 1, out


def test_cpu_reservation_visible_while_active(manager):
    user_id = manager._test_user["user_id"]
    with reserved(manager, gpu_type="cpu-x86", gpu_count=0, hours=0.5) as (rid, _conn):
        active = manager.list_reservations(
            user_filter=user_id, statuses_to_include=["active", "preparing"])
        ids = {r.get("reservation_id") for r in (active or [])}
        assert rid in ids, f"{rid} not in active list {ids}"
