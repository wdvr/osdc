# GPU Dev Server Bug Report

This document contains a comprehensive bug analysis of the GPU Dev Server codebase, including bugs found in the CLI tool, API service, job processor, and shared utilities.

**Analysis Date:** 2026-02-09

---

## Summary

| Severity | Count |
|----------|-------|
| Critical | 2 |
| High     | 6 |
| Medium   | 10 |
| Low      | 5 |

---

## Critical Bugs

### BUG-001: Race Condition in Reservation Status Update After History Append
**Severity:** Critical
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/reservation_db.py`
**Lines:** 546-561

**Code:**
```python
        return cur.rowcount > 0

    except Exception as e:
        logger.error(f"Error updating reservation status for {reservation_id}: {e}", exc_info=True)
        return False


# add to history if requested and update was successful
if cur.rowcount > 0 and add_to_history:
    status_entry = {
        'status': new_status,
        'timestamp': datetime.now(UTC).isoformat(),
    }
```

**Problem:**
The `cur.rowcount` is accessed AFTER the context manager (`get_db_cursor()`) has exited on line 546. At this point, the cursor is closed and the `rowcount` value may be unreliable or raise an exception. The logic at line 549 checks `cur.rowcount > 0` outside the cursor context.

**Expected Behavior:**
The `rowcount` should be captured within the cursor context before the context manager exits.

**Suggested Fix:**
```python
with get_db_cursor() as cur:
    cur.execute(query, params)
    rows_updated = cur.rowcount  # Capture before context exits

    if rows_updated == 0:
        # Check if reservation exists and is in terminal state
        cur.execute("""
            SELECT status FROM reservations
            WHERE reservation_id = %s
        """, (reservation_id,))
        # ... rest of logic

# Use captured value outside context
if rows_updated > 0 and add_to_history:
    # ...
```

---

### BUG-002: Connection Pool Leak on Health Check Failure
**Severity:** Critical
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/db_pool.py`
**Lines:** 206-224

**Code:**
```python
if _check_connection_health(conn):
    # Connection is healthy
    elapsed = time.time() - start_time
    if elapsed > 1.0:  # Only log if we had to wait
        logger.info(f"Acquired healthy connection after {elapsed:.2f}s")
    return conn
else:
    # Connection is stale/broken
    health_check_attempts += 1
    logger.warning(f"Stale connection detected (attempt {health_check_attempts}), closing and retrying")

    try:
        # Close the bad connection (removes from pool)
        conn.close()
    except Exception as close_error:
        logger.debug(f"Error closing stale connection: {close_error}")

    # Check if we've exceeded max health check retries
    if health_check_attempts >= HEALTH_CHECK_MAX_RETRIES:
        raise ConnectionHealthCheckError(...)

    # Don't count this as pool exhaustion, just retry immediately
    continue
```

**Problem:**
When `conn.close()` is called on a stale connection, the connection is closed but NOT returned to the pool with `putconn()`. The `ThreadedConnectionPool` tracks connections it hands out. When `close()` is called directly instead of `putconn()`, the pool doesn't know the connection is gone, which can lead to:
1. Pool exhaustion (pool thinks all connections are in use)
2. Memory leaks (pool maintains reference to closed connection)

**Expected Behavior:**
Bad connections should be returned to the pool (with close=True) or the pool should be notified of the connection's removal.

**Suggested Fix:**
```python
try:
    # Return connection to pool, marking it as bad
    pool_instance.putconn(conn, close=True)
except Exception as close_error:
    logger.debug(f"Error returning stale connection to pool: {close_error}")
```

---

## High Severity Bugs

### BUG-003: Unhandled Exception in SSH Proxy WebSocket Cleanup
**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Lines:** 49-60

**Code:**
```python
async def stdin_to_ws():
    """Forward stdin to WebSocket"""
    try:
        while True:
            data = await reader.read(8192)
            if not data:
                break
            await websocket.send(data)
    except Exception as e:
        print(f"Error in stdin_to_ws: {e}", file=sys.stderr)
    finally:
        await websocket.close()
```

**Problem:**
Both `stdin_to_ws()` and `ws_to_stdout()` are run concurrently via `asyncio.gather()`. If `stdin_to_ws()` completes first and closes the websocket in its `finally` block, `ws_to_stdout()` may still be iterating over `async for message in websocket:`. This creates a race condition where the websocket is closed while another coroutine is still using it. While the `ConnectionClosed` exception is caught, this is a design issue that could lead to inconsistent behavior.

**Expected Behavior:**
Proper cancellation handling should be implemented so that when one direction terminates, the other is properly cancelled.

**Suggested Fix:**
```python
async def tunnel_ssh(target_host: str, target_port: int):
    # ... setup code ...

    async def stdin_to_ws(cancel_event):
        try:
            while not cancel_event.is_set():
                data = await asyncio.wait_for(reader.read(8192), timeout=0.1)
                if not data:
                    break
                await websocket.send(data)
        except asyncio.TimeoutError:
            pass  # Check cancel_event again
        except Exception as e:
            print(f"Error in stdin_to_ws: {e}", file=sys.stderr)
        finally:
            cancel_event.set()

    # Similar for ws_to_stdout with cancel_event
```

---

### BUG-004: Silent Exception Swallowing in Disk Operations
**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/disks.py`
**Lines:** 30-35

**Code:**
```python
except Exception as e:
    # If disk doesn't exist or API error, assume not in use
    # This matches the old behavior of returning False on errors
    return False, None
```

**Problem:**
The function `get_disk_in_use_status()` catches ALL exceptions and returns `(False, None)`, which indicates the disk is not in use. This is dangerous because:
1. Network errors (API unreachable) would falsely indicate disk is available
2. Authentication errors would be silently ignored
3. Server errors would be hidden from the user
4. This could lead to data corruption if two reservations think a disk is available

**Expected Behavior:**
Network/auth errors should propagate or be handled differently from "disk doesn't exist" errors.

**Suggested Fix:**
```python
except requests.exceptions.HTTPError as e:
    if e.response.status_code == 404:
        # Disk doesn't exist - OK to return False
        return False, None
    # Re-raise other HTTP errors
    raise
except Exception as e:
    # Log and re-raise unexpected errors
    print(f"Error checking disk status: {e}")
    raise
```

---

### BUG-005: Type Mismatch in GPU Count Handling
**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Lines:** (Referenced in CLAUDE.md at ~3034 and ~3117)

**Code (documented fix):**
```python
# From CLAUDE.md - this bug was previously identified and fixed
# Problem: `unsupported operand type(s) for *: 'decimal.Decimal' and 'float'`
# Root Cause: DynamoDB returns numbers as `Decimal` type
# Fix: Added `gpu_count = int(gpu_count)` at start of functions
```

**Problem:**
While this specific instance was fixed, there may be other places in the codebase where `Decimal` values from the database are not properly converted before arithmetic operations. The API service uses asyncpg which returns proper Python types, but any code that still interfaces with DynamoDB (legacy paths) or receives JSON data may encounter this issue.

**Expected Behavior:**
All numeric values from external sources should be explicitly cast to appropriate Python numeric types.

**Suggested Fix:**
Add a data validation layer that ensures all numeric fields are converted to proper types before processing.

---

### BUG-006: Missing Transaction Rollback on Partial Failure
**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/snapshot_utils.py`
**Lines:** 117-170

**Code:**
```python
# Update PostgreSQL to mark disk as backing up
# CRITICAL: If this fails, we must not return success, even though snapshot was created
if disk_name:
    try:
        logger.debug(f"Updating database: marking disk '{disk_name}' as backing up")
        with get_db_cursor() as cur:
            cur.execute("""
                UPDATE disks
                SET is_backing_up = TRUE,
                    pending_snapshot_count = COALESCE(pending_snapshot_count, 0) + 1
                WHERE user_id = %s AND disk_name = %s
            """, (user_id, disk_name))

            # Verify the update actually affected a row
            if cur.rowcount == 0:
                raise Exception(f"Disk '{disk_name}' not found in database for user {user_id}")
```

**Problem:**
The code creates a cloud snapshot first, then updates the database. If the database update fails, the code attempts to delete the snapshot. However, if the snapshot deletion also fails (as logged at line 147-150), the system is left in an inconsistent state:
- Cloud snapshot exists but is not tracked in the database
- No mechanism exists to reconcile this state later
- The error at line 168 is raised, but the caller doesn't know a dangling resource exists

**Expected Behavior:**
Either implement a two-phase commit pattern, or implement a reconciliation mechanism that can detect and clean up orphaned snapshots.

**Suggested Fix:**
```python
# Consider implementing a "pending operations" table that tracks
# operations that need reconciliation, or use a saga pattern with
# compensation actions
```

---

### BUG-007: Integer Division Truncation in Wait Time Calculation
**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/interactive.py`
**Lines:** 83-94

**Code:**
```python
elif est_wait < 60:
    wait_display = f"{int(est_wait)}min"
    status_indicator = "..."
else:
    hours = int(est_wait // 60)
    minutes = int(est_wait % 60)
    if minutes == 0:
        wait_display = f"{hours}h"
    else:
        wait_display = f"{hours}h {minutes}min"
```

**Problem:**
The code uses `int()` on values that are already integers from `//` operations, but the issue is that `est_wait` might be a float. If `est_wait = 59.9`, the display shows "59min" when the user might actually wait closer to 60 minutes. This is a UX issue where estimates could be misleading.

**Expected Behavior:**
Either round consistently or show ranges instead of exact times for estimates.

---

### BUG-008: API Client Retry Loop Can Create Infinite Loop
**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/api_client.py`
**Lines:** 255-266

**Code:**
```python
# Handle 401/403 by trying to re-authenticate once
if response.status_code in (401, 403):
    self.authenticate(force=True)
    headers["Authorization"] = f"Bearer {self.api_key}"
    response = requests.request(
        method=method,
        url=url,
        headers=headers,
        json=data,
        params=params,
        timeout=30
    )
```

**Problem:**
If the re-authentication succeeds but the subsequent request still returns 401/403 (e.g., due to incorrect permissions, not expired token), the code will raise an HTTP error. However, if `_make_request()` is called again (e.g., in a retry loop by the caller), the `authenticate(force=True)` will be called again, potentially causing excessive API calls or rate limiting.

**Expected Behavior:**
Track whether re-authentication was already attempted for this request to prevent repeated auth attempts.

---

## Medium Severity Bugs

### BUG-009: Variable Scope Issue with `explicit_no_disk`
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`
**Lines:** 920-933, 1166

**Code:**
```python
# Line 920 (interactive mode):
explicit_no_disk = False

# Line 1166 (non-interactive mode):
explicit_no_disk = False
```

**Problem:**
The variable `explicit_no_disk` is initialized in both interactive and non-interactive branches independently. In the non-interactive path (line 1166), it's re-initialized, which would shadow or override any value set earlier. Additionally, if the code path doesn't go through either initialization, `explicit_no_disk` would be undefined when accessed later.

**Expected Behavior:**
Initialize `explicit_no_disk = False` once at the beginning of the function, before any conditional branches.

---

### BUG-010: Unsafe String Format in SQL Query Construction
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/disk_db.py`
**Lines:** 287-302

**Code:**
```python
# Build SET clause dynamically
set_clauses = []
params = []

for field, value in updates.items():
    set_clauses.append(f"{field} = %s")
    params.append(value)

# ...

query = """
    UPDATE disks
    SET """ + ', '.join(set_clauses) + """
    WHERE user_id = %s AND disk_name = %s
"""
```

**Problem:**
While the VALUES are parameterized (safe), the FIELD NAMES are directly interpolated into the query string using f-strings. If an attacker could control the keys of the `updates` dictionary, they could inject arbitrary SQL. This is a potential SQL injection vulnerability.

**Expected Behavior:**
Field names should be validated against a whitelist of allowed column names.

**Suggested Fix:**
```python
ALLOWED_DISK_FIELDS = {'size_gb', 'last_used', 'in_use', 'reservation_id',
                       'is_backing_up', 'is_deleted', ...}

for field, value in updates.items():
    if field not in ALLOWED_DISK_FIELDS:
        raise ValueError(f"Invalid field name: {field}")
    set_clauses.append(f"{field} = %s")
```

---

### BUG-011: Missing Validation in Poller Job Recovery
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/poller.py`
**Lines:** 156-189

**Code:**
```python
def rebuild_active_jobs_from_k8s(job_manager: JobManager):
    """Rebuild active jobs tracking from existing K8s jobs."""
    try:
        jobs = job_manager.batch_api.list_namespaced_job(...)

        for job in jobs.items:
            if job.status.active and job.status.active > 0:
                job_name = job.metadata.name
                # Extract msg_id from job name: "reservation-worker-123"
                try:
                    msg_id = int(job_name.split("-")[-1])
                    active_jobs[msg_id] = {...}
```

**Problem:**
The code assumes job names follow the format `reservation-worker-<msg_id>`. If a job has a name that ends with a non-numeric string (e.g., `reservation-worker-abc` or a manually created job), the `int()` conversion will fail. While this is caught, legitimate jobs with unexpected naming could be missed, leading to duplicate job creation.

**Expected Behavior:**
Use a more robust job name parsing mechanism or store msg_id as a label/annotation on the job.

---

### BUG-012: Inconsistent Error Handling in Worker Message Processing
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/worker.py`
**Lines:** 151-158

**Code:**
```python
# Delete message from queue on success
if delete_message(msg_id):
    logger.info(f"Message {msg_id} deleted from queue")
    return True
else:
    logger.error(f"Failed to delete message {msg_id} - will retry")
    # Return False so job fails and message becomes visible again
    return False
```

**Problem:**
If the message processing succeeded (handler returned 200) but the message deletion failed, the function returns `False`. This causes the Kubernetes job to fail, and PGMQ will make the message visible again for retry. However, the actual work (pod creation, etc.) has already been done successfully. This could lead to duplicate work being performed.

**Expected Behavior:**
Message deletion failure after successful processing should be handled differently - perhaps with a warning but still returning success, or implementing idempotent processing.

---

### BUG-013: Potential Deadlock in Connection Pool With NOWAIT
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/disk_db.py`
**Lines:** 217-225

**Code:**
```python
cur.execute("""
    SELECT in_use, reservation_id as current_reservation,
           is_deleted, is_backing_up
    FROM disks
    WHERE user_id = %s AND disk_name = %s
    FOR UPDATE NOWAIT
""", (user_id, disk_name))
```

**Problem:**
Using `FOR UPDATE NOWAIT` is good for failing fast, but the exception handling only checks for `pgcode == '55P03'` (lock_not_available). However, there are other PostgreSQL error codes related to locking (e.g., deadlock detection `40P01`). These would be re-raised as generic exceptions, potentially confusing users.

**Expected Behavior:**
Handle additional lock-related error codes with appropriate user-friendly messages.

---

### BUG-014: Timezone-Naive Datetime Comparison in CLI
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`
**Lines:** 1587-1599

**Code:**
```python
from datetime import datetime, timezone, timedelta
now = datetime.now(timezone.utc)
one_hour_ago = now - timedelta(hours=1)

for reservation in reservations:
    # ...
    created_at = reservation.get("created_at")
    if created_at:
        # Multiple datetime parsing attempts...
```

**Problem:**
The code attempts to parse `created_at` with multiple format assumptions. If the parsing fails or results in a naive datetime, comparisons with `one_hour_ago` (which is timezone-aware) will raise a `TypeError`. While there's a try-except somewhere, the specific error handling for this comparison isn't visible.

**Expected Behavior:**
Always ensure parsed datetimes are timezone-aware before comparison.

---

### BUG-015: Missing Input Sanitization for GitHub Username
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/interactive.py`
**Lines:** 534-546

**Code:**
```python
def _validate_github_username(username: str) -> bool:
    """Validate GitHub username format"""
    if not username or not username.strip():
        return "GitHub username cannot be empty"

    username = username.strip()
    if not username.replace("-", "").replace("_", "").replace(".", "").isalnum():
        return "Invalid GitHub username format"
```

**Problem:**
The validation allows `.` (dots) in usernames, but GitHub usernames cannot contain dots (only hyphens). Also, GitHub usernames cannot start or end with a hyphen, and cannot have consecutive hyphens. This could lead to failed SSH key lookups or confusing error messages when the GitHub API rejects the username.

**Expected Behavior:**
Implement correct GitHub username validation: alphanumeric and single hyphens (not at start/end).

---

### BUG-016: Unhandled Case in GPU Config Lookup
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py`
**Lines:** 809-820

**Code:**
```python
gpu_configs = {
    "t4": {"max_gpus": 4, "instance_type": "g4dn.12xlarge"},
    # ...
    "cpu-arm": {"max_gpus": 0, "instance_type": "c7g.4xlarge"},
    "cpu-x86": {"max_gpus": 0, "instance_type": "c7i.4xlarge"},
}
```

**Problem:**
The `gpu_configs` dictionary in the CLI differs from the `GPU_CONFIG` dictionary in the reservation handler. For example:
- CLI: `"cpu-arm": {"instance_type": "c7g.4xlarge"}`
- Handler: `"cpu-arm": {"instance_type": "c7g.8xlarge"}`

This inconsistency could lead to validation passing in the CLI but failing in the backend, or incorrect resource allocation.

**Expected Behavior:**
GPU configurations should be defined in a single source of truth and shared between CLI and backend.

---

### BUG-017: Race Condition in Check Job Status
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/poller.py`
**Lines:** 103-134

**Code:**
```python
def check_job_status(job_manager: JobManager, msg_id: int, job_info: Dict[str, Any]):
    # ...
    if not status:
        logger.warning(f"Job {job_name} (msg {msg_id}) not found - removing from tracking")
        del active_jobs[msg_id]
        return

    if status["phase"] == "Succeeded":
        logger.info(f"Job {job_name} (msg {msg_id}) succeeded")
        del active_jobs[msg_id]
```

**Problem:**
The `active_jobs` dictionary is modified (`del active_jobs[msg_id]`) without any locking mechanism. While Python's GIL provides some protection, the poller iterates over `list(active_jobs.keys())` and modifies the dict within the loop. If another thread were to modify `active_jobs` concurrently, this could cause issues.

**Expected Behavior:**
Use a thread-safe data structure or implement proper locking around dictionary modifications.

---

### BUG-018: Error Message Leaks Internal Details
**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`
**Lines:** 715-723

**Code:**
```python
except Exception as e:
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Failed to verify AWS credentials"
    ) from e
```

**Problem:**
While the user-facing message is generic, the `from e` clause chains the original exception. In some configurations, this could leak internal stack traces to clients, potentially revealing implementation details.

**Expected Behavior:**
Log the full exception internally but don't chain it to the HTTP exception.

---

## Low Severity Bugs

### BUG-019: Deprecated asyncio.get_event_loop() Usage
**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Lines:** 37

**Code:**
```python
loop = asyncio.get_event_loop()
```

**Problem:**
`asyncio.get_event_loop()` is deprecated since Python 3.10 when called from a coroutine. It should be replaced with `asyncio.get_running_loop()`.

**Expected Behavior:**
Use `asyncio.get_running_loop()` inside async functions.

---

### BUG-020: Hardcoded Magic Numbers
**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/poller.py`
**Lines:** 152, 317

**Code:**
```python
if age_seconds > 300:  # 5 minutes
    logger.warning(...)

if poll_count % 12 == 0:  # Every minute (12 * 5s)
    logger.debug(...)
```

**Problem:**
Magic numbers like 300 and 12 are hardcoded without clear documentation. The comment `# 5 minutes` helps, but these should be named constants.

**Expected Behavior:**
Define named constants like `PENDING_JOB_WARNING_THRESHOLD_SECONDS = 300`.

---

### BUG-021: Potential Division by Zero in Queue Wait Estimation
**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/interactive.py`
**Lines:** 163-164

**Code:**
```python
# Add multinode options (multiples of max_gpus)
multinode_counts = [
    count for count in multinode_counts if count % max_gpus == 0]
```

**Problem:**
If `max_gpus` is 0 (for CPU instances), this will raise a `ZeroDivisionError`. While CPU instances likely don't have multinode options, the code path could theoretically be reached.

**Expected Behavior:**
Add a guard: `if max_gpus > 0:` before the modulo operation.

---

### BUG-022: Inconsistent Return Types
**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/disks.py`
**Lines:** 179-239

**Code:**
```python
def poll_disk_operation(...) -> Tuple[bool, str]:
    # ...
    if is_completed:
        if operation_status == 'completed':
            # ...
            return True, f"Disk '{disk_name}' created successfully"
    # ...
    # Timeout
    if operation_type == 'create':
        return False, f"Timed out waiting..."
```

**Problem:**
The return type is documented as `Tuple[bool, str]`, but in Python typing it should be `tuple[bool, str]` (lowercase since Python 3.9). More importantly, all code paths should be verified to return the correct type structure.

**Expected Behavior:**
Use consistent typing and ensure all code paths return the documented type.

---

### BUG-023: Unused Import and Dead Code
**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Lines:** 10

**Code:**
```python
import ssl as ssl_module
```

**Problem:**
The `ssl_module` is imported but never used in the file. This is dead code that should be cleaned up.

**Expected Behavior:**
Remove unused imports.

---

## Recommendations

1. **Implement Centralized Configuration**: GPU configurations should be defined in a single source and shared between CLI and backend to prevent inconsistencies.

2. **Add Input Validation Layer**: Create a dedicated validation module for user inputs (GitHub usernames, disk names, etc.) with comprehensive tests.

3. **Implement Reconciliation Service**: Add a background service that detects and cleans up orphaned cloud resources (snapshots, volumes) that aren't tracked in the database.

4. **Improve Error Handling Strategy**: Create a consistent error handling strategy that distinguishes between recoverable errors (retry), unrecoverable errors (fail fast), and user errors (provide helpful messages).

5. **Add Integration Tests**: Many of these bugs would be caught by integration tests that exercise the full code path from CLI to database.

6. **Implement Circuit Breaker Pattern**: For external service calls (AWS APIs, K8s API), implement circuit breakers to prevent cascade failures.

7. **Add Metrics and Alerting**: Implement metrics for connection pool usage, queue depth, and error rates to detect issues before they become critical.

---

*Generated by Claude Code Bug Analysis*
