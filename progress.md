# Bug Fixes and Security Improvements

**Date:** 2026-02-09
**Branch:** feat/helm-chart

## Completed Fixes (Session 1)

### Critical Bugs Fixed

#### 1. BUG-001: Race Condition in Reservation Status Update
**File:** `terraform-gpu-devservers/shared/reservation_db.py`
**Problem:** `cur.rowcount` was accessed outside the `get_db_cursor()` context manager, after the cursor was closed.
**Fix:** Captured `rows_updated = cur.rowcount` inside the context manager before exiting.

#### 2. BUG-002: Connection Pool Leak on Health Check Failure
**File:** `terraform-gpu-devservers/shared/db_pool.py`
**Problem:** Stale connections were closed with `conn.close()` directly instead of returning them to the pool, causing pool exhaustion.
**Fix:** Changed to `pool_instance.putconn(conn, close=True)` to properly notify the pool.

#### 3. Extension Timeout Bug (Pre-existing on main)
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Problem:** `gpu-dev edit --extend` command timed out because the API service sent `action: "extend"` but the handler expected `action == "extend_reservation"`. This caused extension messages to fall through to the default `else` branch which processed them as new reservations.
**Fix:** Changed handler at line 1158 to accept both action names: `elif action in ["extend_reservation", "extend"]:`

### Security Issues Fixed

#### 4. HIGH-002: Missing Authorization Check on Job Actions
**File:** `terraform-gpu-devservers/api-service/app/main.py`
**Problem:** Job action endpoints (cancel, extend, jupyter enable/disable, add user) didn't verify the user owned the job before queuing the action.
**Fix:** Added authorization checks to all 5 endpoints that verify ownership before allowing the action:
- `POST /v1/jobs/{job_id}/cancel`
- `POST /v1/jobs/{job_id}/extend`
- `POST /v1/jobs/{job_id}/jupyter/enable`
- `POST /v1/jobs/{job_id}/jupyter/disable`
- `POST /v1/jobs/{job_id}/users`

#### 5. MEDIUM-006: SSH Proxy Domain Validation
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Problem:** Domain validation used `in` which could allow malicious hostnames like `fake.devservers.io.attacker.com`.
**Fix:** Changed to `endswith()` for proper suffix matching.

#### 6. BUG-010: SQL Field Name Injection
**File:** `terraform-gpu-devservers/shared/disk_db.py`
**Problem:** Field names were directly interpolated into SQL queries without validation.
**Fix:** Added whitelist of allowed field names to prevent SQL injection.

### Low Priority Fixes

#### 7. BUG-023: Unused Import
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Fix:** Removed unused `ssl as ssl_module` import.

#### 8. Extension Hours Validation
**Files:** `cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`, `api_client.py`, `interactive.py`, `reservations.py`
**Problem:** Float 0.5 hours was converted to int 0 via `int()`, causing API to reject with "must be >= 1".
**Fix:** Added CLI validation for minimum 1 hour, changed `int()` to `round()` in api_client.

## Completed Fixes (Session 2)

### SSH Proxy

#### 9. BUG-003: WebSocket Cleanup Race Condition
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Problem:** `asyncio.gather()` didn't handle task cancellation properly when one task failed, causing unhandled exceptions during WebSocket cleanup.
**Fix:** Replaced `asyncio.gather()` with `asyncio.wait(FIRST_COMPLETED)` + explicit task cancellation for clean shutdown.

#### 10. BUG-019: Deprecated asyncio API
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Problem:** Used deprecated `asyncio.get_event_loop()` which emits DeprecationWarning in Python 3.10+.
**Fix:** Changed to `asyncio.get_running_loop()`.

### CLI Fixes

#### 11. BUG-004: Silent Exception Swallowing in Disk Operations
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/disks.py`
**Problem:** Catch-all `except Exception` silently swallowed all errors when checking disk status.
**Fix:** Replaced with specific `HTTPError` (404→False, others re-raise), `ConnectionError`, and `Timeout` handlers.

#### 12. BUG-008: Infinite Auth Retry Loop
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/api_client.py`
**Problem:** `_make_request` would infinitely retry authentication on 401/403 if re-auth also returned 401/403.
**Fix:** Added `_retry_auth: bool = True` parameter; recursive call passes `_retry_auth=False` to prevent infinite loop.

#### 13. BUG-009: Uninitialized explicit_no_disk Variable
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`
**Problem:** `explicit_no_disk` was only initialized inside conditional branches, could be undefined.
**Fix:** Initialized `explicit_no_disk = False` once before all branches.

#### 14. BUG-014: Timezone-Naive Datetime Comparison
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`
**Problem:** `datetime.fromisoformat()` could return timezone-naive datetime, causing crash when compared with timezone-aware `datetime.now(timezone.utc)`.
**Fix:** Added `if created_dt.tzinfo is None: created_dt = created_dt.replace(tzinfo=timezone.utc)` at both comparison points.

#### 15. BUG-021: Division by Zero in GPU Count Validation
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/interactive.py`
**Problem:** `count % max_gpus == 0` would crash with ZeroDivisionError when `max_gpus` was 0.
**Fix:** Added `if max_gpus > 0:` guard before modulo operation.

#### 16. Dead Code: Duplicate _extract_ip_from_reservation
**File:** `cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`
**Fix:** Removed duplicate function, now imports from `reservations` module.

### Backend Fixes

#### 17. BUG-011: Invalid Message ID Handling
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/poller.py`
**Problem:** `rebuild_active_jobs_from_k8s` didn't validate msg_id before use, could pass invalid IDs to queue operations.
**Fix:** Added positive `msg_id` validation with clear error messages.

#### 18. BUG-012: False Failure on Queue Deletion
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/worker.py`
**Problem:** Worker returned `False` (indicating failure) when job processed successfully but queue message deletion failed, causing unnecessary retries.
**Fix:** Returns `True` on successful processing; logs warning about queue deletion failure.

#### 19. BUG-013: Missing Deadlock Handling
**File:** `terraform-gpu-devservers/shared/disk_db.py`
**Problem:** Only handled pgcode `55P03` (lock_not_available) but not `40P01` (deadlock_detected).
**Fix:** Added deadlock error code `40P01` handling alongside `55P03`.

#### 20. BUG-017: Undocumented Thread Safety Assumption
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/poller.py`
**Fix:** Added comment explaining single-threaded safety of `list()` snapshot iteration.

#### 21. BUG-018: Internal Error Details Leaked in API Responses
**File:** `terraform-gpu-devservers/api-service/app/main.py`
**Problem:** `raise HTTPException(...) from e` leaked internal exception details in API error responses.
**Fix:** Removed `from e`, added `logger.error(f"...: {e}", exc_info=True)` for server-side logging.

#### 22. BUG-020: Magic Numbers in Poller
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/poller.py`
**Fix:** Replaced magic numbers with named constants: `PENDING_JOB_WARNING_THRESHOLD_SECONDS=300`, `STATUS_LOG_INTERVAL=12`.

#### 23. SyntaxWarning: Invalid Escape Sequences
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Problem:** `\$` in embedded bash scripts caused Python SyntaxWarning (invalid escape sequence).
**Fix:** Removed unnecessary backslashes before `$` signs in f-string bash heredocs.

#### 24. Dead Code Removal
**File:** `terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Fix:** Removed `_REMOVED` DynamoDB functions and migration comments.

## E2E Test Results

### Session 1: Full CLI Test Suite (14/14 PASSED)

| Test # | Test Name | Result |
|--------|-----------|--------|
| 1 | Configuration Check | PASSED |
| 2 | GPU Availability | PASSED |
| 3 | List Reservations | PASSED |
| 4 | Show Reservation Details | PASSED |
| 5 | Create T4 Reservation | PASSED |
| 6 | Verify in List | PASSED |
| 7 | Show Detailed Info | PASSED |
| 8 | Disk Operations | PASSED |
| 9 | Cluster Status | PASSED |
| 10 | Cancel Reservation | PASSED |
| 11 | Verify Cancellation | PASSED |
| 12 | Edit Command Help | PASSED |
| 13 | Connect Command Help | PASSED |
| 14 | Reserve Command Help | PASSED |

### Session 2: Syntax Checks (11/11 PASSED)

All modified Python files compile with zero warnings:
- ssh_proxy.py, disks.py, interactive.py, api_client.py, cli.py, reservations.py
- main.py (api-service), poller.py, worker.py, disk_db.py, reservation_handler.py

### Session 2: CLI Operations (9/9 PASSED)

| Test | Command | Result |
|------|---------|--------|
| 1 | `gpu-dev config show` | PASSED |
| 2 | `gpu-dev avail` | PASSED |
| 3 | `gpu-dev list` | PASSED |
| 4 | `gpu-dev --help` | PASSED |
| 5 | `gpu-dev reserve --help` | PASSED |
| 6 | `gpu-dev edit --help` | PASSED |
| 7 | `gpu-dev connect --help` | PASSED |
| 8 | `gpu-dev disk list` | PASSED |
| 9 | `gpu-dev status` | PASSED |

### Session 2: Reservation Flow (4/5 PASSED, 1 INCONCLUSIVE)

| Test | Result | Notes |
|------|--------|-------|
| Create T4 reservation | PASSED | 1 GPU, 1 hour, no-persist |
| List reservations | PASSED | New reservation visible |
| Show reservation | PASSED | Full details returned |
| Cancel reservation | PASSED | CLI confirms cancellation |
| Verify cancellation | INCONCLUSIVE | Backend not redeployed yet |

---

## Open Issues

### From security.md (Remaining - Documented/Intentional)
1. **CRITICAL-002**: Privileged BuildKit containers (documented/intentional)
2. **HIGH-004**: No Network Policies defined in Helm chart
3. **HIGH-005**: GPU pods run with CAP_SYS_ADMIN (documented/intentional for profiling)

### From bugs.md (Remaining - Not Fixed)
1. **BUG-005**: Hardcoded SSH proxy domain (low priority, works as-is)
2. **BUG-006**: Missing input sanitization on reservation names (low priority)

### From cleanup.md (Remaining)
1. **Code Complexity**: 8,000+ line file to split (reservation_handler.py)
2. **Duplicate Code**: GPU_CONFIG defined in 3 places

### From feature_parity.md
1. **Monitoring**: 0% (completely missing from Helm chart)
2. **AWS-specific**: 40% parity

### Minor UI Issues Found in E2E Tests
1. **Warning spam**: Every `gpu-dev list` shows "Filtering by specific user not yet supported via API"
2. **Table rendering**: Reservation list table columns appear truncated with extra empty columns
3. **Disk list-content**: Returns "No snapshot contents available" even for disks with snapshots

### Deployment Note
Backend fixes (BUG-011, BUG-012, BUG-013, BUG-017, BUG-018, BUG-020, dead code) require `tofu apply` in `terraform-gpu-devservers/` to deploy. CLI fixes are immediately active from local source.
