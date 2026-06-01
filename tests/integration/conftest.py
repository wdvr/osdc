"""Integration-test harness — drives REAL reservations on a live cluster.

All tests here are marked ``integration`` (skipped unless ``--run-integration`` /
``GPU_DEV_RUN_INTEGRATION=1``). They target the env named by ``GPU_DEV_TEST_ENV``
(default ``staging``) via the CLI's ReservationManager, and SKIP cleanly if that
env isn't reachable (e.g. staging not deployed). Every reservation is cancelled in
a finally, so a failed test never leaks a pod.

Run (once staging is applied):
    GPU_DEV_TEST_ENV=staging GPU_DEV_GITHUB_USER=<you> \
        pytest -m integration --run-integration -q
"""
import os
import time
import contextlib
import subprocess

import pytest

TEST_ENV = os.environ.get("GPU_DEV_TEST_ENV", "staging")
ACTIVE_TIMEOUT_MIN = float(os.environ.get("GPU_DEV_TEST_TIMEOUT_MIN", "15"))


@pytest.fixture(scope="session", autouse=True)
def _select_env():
    # Point the CLI Config at the target environment for this process.
    os.environ.setdefault("GPU_DEV_ENVIRONMENT", TEST_ENV)
    yield


@pytest.fixture(scope="session")
def manager():
    """CLI ReservationManager for the target env; skips if unreachable/unauth'd."""
    from gpu_dev_cli.config import load_config
    from gpu_dev_cli.reservations import ReservationManager
    from gpu_dev_cli.auth import authenticate_user

    cfg = load_config()
    try:
        # Reachability: the env's reservations table must exist + be describable.
        status = cfg.dynamodb.Table(cfg.reservations_table).table_status
        assert status in ("ACTIVE", "UPDATING"), status
        user = authenticate_user(cfg)
    except Exception as e:  # not deployed / no creds / no github_user
        pytest.skip(f"env '{TEST_ENV}' not reachable via {cfg.reservations_table}: {e}")

    mgr = ReservationManager(cfg)
    mgr._test_user = user
    return mgr


def _ssh_exec(ssh_command: str, remote: str, timeout: int = 120):
    """Run a command on the pod via its ssh_command; returns (rc, output)."""
    if not ssh_command or ssh_command.startswith("ssh user@"):
        raise RuntimeError(f"no usable ssh_command: {ssh_command!r}")
    if "StrictHostKeyChecking" not in ssh_command:
        ssh_command = ssh_command.replace(
            "ssh ",
            "ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR ",
            1)
    import shlex
    p = subprocess.run(f"{ssh_command} {shlex.quote(remote)}", shell=True,
                       capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout + p.stderr)


@contextlib.contextmanager
def reserved(manager, gpu_type, gpu_count, hours=0.5):
    """Reserve -> wait active -> yield connection info -> ALWAYS cancel."""
    user_id = manager._test_user["user_id"]
    github_user = manager._test_user["github_user"]
    rid = manager.create_reservation(
        user_id=user_id, gpu_count=gpu_count, gpu_type=gpu_type,
        duration_hours=hours, name="itest", github_user=github_user,
        no_persistent_disk=True)
    assert rid, "create_reservation returned no id"
    try:
        manager.wait_for_reservation_completion(
            reservation_id=rid, timeout_minutes=ACTIVE_TIMEOUT_MIN, verbose=False)
        conn = manager.get_connection_info(rid, user_id) or {}
        yield rid, conn
    finally:
        with contextlib.suppress(Exception):
            manager.cancel_reservation(rid, user_id)
