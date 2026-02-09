# Bug Fixes and Security Improvements

**Date:** 2026-02-09
**Branch:** feat/helm-chart

## Completed Fixes

### Critical Bugs Fixed

#### 1. BUG-001: Race Condition in Reservation Status Update
**File:** `terraform-gpu-devservers/shared/reservation_db.py`
**Problem:** `cur.rowcount` was accessed outside the `get_db_cursor()` context manager, after the cursor was closed.
**Fix:** Captured `rows_updated = cur.rowcount` inside the context manager before exiting.

#### 2. BUG-002: Connection Pool Leak on Health Check Failure
**File:** `terraform-gpu-devservers/shared/db_pool.py`
**Problem:** Stale connections were closed with `conn.close()` directly instead of returning them to the pool, causing pool exhaustion.
**Fix:** Changed to `pool_instance.putconn(conn, close=True)` to properly notify the pool.

### Security Issues Fixed

#### 3. HIGH-002: Missing Authorization Check on Job Actions
**File:** `terraform-gpu-devservers/api-service/app/main.py`
**Problem:** Job action endpoints (cancel, extend, jupyter enable/disable, add user) didn't verify the user owned the job before queuing the action.
**Fix:** Added authorization checks to all 5 endpoints that verify ownership before allowing the action:
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/jobs/{job_id}/extend`
- `POST /v1/jobs/{job_id}/jupyter/enable`
- `POST /v1/jobs/{job_id}/jupyter/disable`
- `POST /v1/jobs/{job_id}/users`

#### 4. MEDIUM-006: SSH Proxy Domain Validation
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Problem:** Domain validation used `in` which could allow malicious hostnames like `fake.devservers.io.attacker.com`.
**Fix:** Changed to `endswith()` for proper suffix matching.

#### 5. BUG-010: SQL Field Name Injection
**File:** `terraform-gpu-devservers/shared/disk_db.py`
**Problem:** Field names were directly interpolated into SQL queries without validation.
**Fix:** Added whitelist of allowed field names to prevent SQL injection.

### Low Priority Fixes

#### 6. BUG-023: Unused Import
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Fix:** Removed unused `ssl as ssl_module` import.

## E2E Test Results

### Reservation Flow Test (PASSED)
- Create reservation: ✅
- List reservations: ✅
- Extend reservation: ✅
- Cancel reservation: ✅

### Persistent Disk Test (PARTIAL)
- Disk operations: ✅ (testt disk works, snapshots created)
- Delete with --yes flag: ⏳ (takes a long time due to cloud operations)

## Remaining Work from Reviews

### From security.md (21 findings)
- CRITICAL-002: Privileged BuildKit containers (documented/intentional)
- HIGH-004: No Network Policies defined in Helm chart
- HIGH-005: GPU pods run with CAP_SYS_ADMIN (documented/intentional for profiling)

### From bugs.md (23 bugs)
- BUG-003: Unhandled exception in SSH proxy WebSocket cleanup
- BUG-004: Silent exception swallowing in disk operations
- BUG-005-008: Various error handling issues
- BUG-009-018: Medium priority bugs

### From cleanup.md
- 8,000+ line file to split (reservation_handler.py)
- 6 duplicate code patterns (GPU_CONFIG in 3 places)

### From feature_parity.md
- Monitoring: 0% (completely missing from Helm chart)
- AWS-specific: 40% parity

## Files Changed

```
cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py    |   6 +-
terraform-gpu-devservers/api-service/app/main.py  |  88 +++++++++++++++++-
terraform-gpu-devservers/shared/db_pool.py        |   7 +-
terraform-gpu-devservers/shared/disk_db.py        |  18 +++-
terraform-gpu-devservers/shared/reservation_db.py |  12 +-
5 files changed, 113 insertions(+), 18 deletions(-)
```
