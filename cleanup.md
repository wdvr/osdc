# GPU Dev Server Codebase Cleanup Report

## Executive Summary

This report identifies opportunities for code cleanup across the GPU Dev Server codebase, including duplicate code, dead code, code smells, and architecture issues. Total estimated effort: **32-48 hours**.

---

## 1. DUPLICATE CODE

### 1.1 GPU_CONFIG Dictionary (HIGH PRIORITY)

**Category:** Duplicate Code
**Files:**
- `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py` (lines 809-820)
- `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/reservations.py` (lines 88-100)
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py` (lines 97-110)

**Code snippet (cli.py):**
```python
gpu_configs = {
    "t4": {"max_gpus": 4, "instance_type": "g4dn.12xlarge"},
    "l4": {"max_gpus": 4, "instance_type": "g6.12xlarge"},
    "a10g": {"max_gpus": 4, "instance_type": "g5.12xlarge"},
    "t4-small": {"max_gpus": 1, "instance_type": "g4dn.xlarge"},
    "a100": {"max_gpus": 8, "instance_type": "p4d.24xlarge"},
    "h100": {"max_gpus": 8, "instance_type": "p5.48xlarge"},
    "h200": {"max_gpus": 8, "instance_type": "p5e.48xlarge"},
    "b200": {"max_gpus": 8, "instance_type": "p6-b200.48xlarge"},
    "cpu-arm": {"max_gpus": 0, "instance_type": "c7g.4xlarge"},
    "cpu-x86": {"max_gpus": 0, "instance_type": "c7i.4xlarge"},
}
```

**Problem:** GPU configuration is defined in 3+ places and must be kept in sync manually. Adding a new GPU type requires changes in multiple files, risking inconsistency.

**Recommendation:**
1. Create a shared GPU config module at `terraform-gpu-devservers/shared/gpu_config.py`
2. CLI should fetch config from API or use a generated config file
3. Use the database `gpu_types` table as the single source of truth

**Estimated effort:** 4-6 hours

---

### 1.2 `ensure_utc()` Function (MEDIUM PRIORITY)

**Category:** Duplicate Code
**Files:**
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py` (lines 60-85)
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/disk_reconciler.py` (lines 30-58)

**Code snippet:**
```python
def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    Ensure a datetime is timezone-aware and in UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)
```

**Problem:** Same utility function duplicated in multiple files.

**Recommendation:** Move to `terraform-gpu-devservers/shared/datetime_utils.py` and import where needed.

**Estimated effort:** 1-2 hours

---

### 1.3 `trigger_availability_update()` Function (MEDIUM PRIORITY)

**Category:** Duplicate Code
**Files:**
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-expiry-service/expiry/main.py` (lines 79-108)
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py` (lines 972-1004)

**Code snippet:**
```python
def trigger_availability_update():
    """Trigger the availability updater Lambda function"""
    try:
        availability_function_name = os.environ.get(
            "AVAILABILITY_UPDATER_FUNCTION_NAME"
        )
        if not availability_function_name:
            logger.warning(
                "AVAILABILITY_UPDATER_FUNCTION_NAME not set, skipping availability update"
            )
            return
        # ... same implementation in both files
```

**Problem:** Identical function copied between services.

**Recommendation:** Move to `terraform-gpu-devservers/shared/availability_utils.py`.

**Estimated effort:** 1-2 hours

---

### 1.4 `get_k8s_client()` Singleton Pattern (MEDIUM PRIORITY)

**Category:** Duplicate Code
**Files:**
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-expiry-service/expiry/main.py` (lines 69-76)
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/availability-updater-service/updater/main.py` (lines 46-53)
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py` (lines 219-230)

**Problem:** Same singleton pattern implemented three times with nearly identical code.

**Recommendation:**
- The shared `k8s_client.py` already has `setup_kubernetes_client()`
- Add a `get_cached_client()` function to shared module that handles singleton pattern

**Estimated effort:** 2-3 hours

---

### 1.5 `_extract_ip_from_reservation()` Function (LOW PRIORITY)

**Category:** Duplicate Code
**Files:**
- `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/cli.py` (lines 106-118)
- `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/reservations.py` (lines 49-60)

**Code snippet:**
```python
def _extract_ip_from_reservation(reservation: dict) -> str:
    """Extract IP:Port from reservation data"""
    node_ip = reservation.get("node_ip")
    node_port = reservation.get("node_port")

    if node_ip and node_port:
        return f"{node_ip}:{node_port}"
    elif node_ip:
        return node_ip
    return "N/A"
```

**Recommendation:** Keep in `reservations.py` only and import in `cli.py`.

**Estimated effort:** 0.5 hours

---

### 1.6 `create_message_metadata()` Function (MEDIUM PRIORITY)

**Category:** Duplicate Code
**Files:**
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py` (lines 39-49)
- `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/shared/retry_utils.py` (lines 70-84)

**Code snippet (api-service):**
```python
# Note: This would work if shared module is in PYTHONPATH
# For now, we'll inline the function
# from shared import create_message_metadata

def create_message_metadata(max_retries: int = 3) -> dict[str, Any]:
    """Create initial message metadata for PGMQ messages."""
    return {
        "retry_count": 0,
        "created_at": datetime.now(UTC).isoformat(),
        "max_retries": max_retries
    }
```

**Problem:** Function intentionally duplicated because shared module not in PYTHONPATH for API service.

**Recommendation:**
1. Add shared module to API service Docker image PYTHONPATH
2. Remove inlined function and use `from shared import create_message_metadata`

**Estimated effort:** 2-3 hours

---

## 2. DEAD CODE

### 2.1 `_REMOVED` Functions (HIGH PRIORITY)

**Category:** Dead Code
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Lines:** 1070-1083

**Code snippet:**
```python
def query_user_reservations_with_prefix_REMOVED(table, user_id: str, reservation_prefix: str) -> list:
    """REMOVED: Query user reservations using UserIndex GSI and filter by prefix"""
    # This function has been removed as part of the PostgreSQL migration
    raise NotImplementedError("This function has been migrated to PostgreSQL.")

def scan_all_reservations_with_prefix_REMOVED(table, reservation_prefix: str) -> list:
    """REMOVED: Scan all reservations with prefix - fallback when no user_id provided"""
    raise NotImplementedError("This function has been migrated to PostgreSQL.")
```

**Problem:** Legacy DynamoDB functions left in codebase after PostgreSQL migration. They only raise `NotImplementedError`.

**Recommendation:** Delete these functions entirely. They serve no purpose and clutter the codebase.

**Estimated effort:** 0.5 hours

---

### 2.2 Commented Migration Code

**Category:** Dead Code
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Lines:** 1067-1068, 1077-1078

**Code snippet:**
```python
# query_user_reservations_with_prefix removed - DynamoDB-specific function no longer needed
# Use list_reservations_by_user() from shared.reservation_db instead

# scan_all_reservations_with_prefix removed - DynamoDB-specific function no longer needed
# Use get_reservation() with LIKE queries in PostgreSQL instead
```

**Recommendation:** Remove comment blocks along with the dead functions.

**Estimated effort:** Included in 2.1

---

## 3. CODE SMELLS

### 3.1 `reservation_handler.py` - MASSIVE FILE (CRITICAL)

**Category:** Code Smell - Long File
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`
**Lines:** 8,075 lines total

**Problem:** This single file is 8,075 lines long, making it extremely difficult to:
- Navigate and understand
- Test in isolation
- Maintain and debug
- Review in code reviews

**Recommendation:** Split into multiple focused modules:

1. `reservation_handler.py` (main handler, ~500 lines)
   - `handler()` function
   - Message routing logic

2. `ebs_operations.py` (~800 lines)
   - `check_ebs_migration_needed()`
   - `migrate_ebs_across_az()`
   - `get_latest_completed_snapshot()`
   - `restore_ebs_from_existing_snapshot()`

3. `efs_operations.py` (~400 lines)
   - `create_or_find_user_efs()`
   - `ensure_efs_mount_target()`
   - `get_efs_mount_dns()`

4. `multinode_handler.py` (~800 lines)
   - `process_multinode_reservation_request()`
   - `coordinate_multinode_reservation()`
   - `process_multinode_individual_node()`
   - Multinode lock functions

5. `pod_creation.py` (~2000 lines)
   - Pod spec generation
   - Container configuration
   - Volume mounting

6. `action_handlers.py` (~500 lines)
   - `process_jupyter_action()`
   - `process_add_user_action()`
   - `process_extend_reservation_action()`
   - `process_cancellation_request()`

7. `validation.py` (~200 lines)
   - `validate_reservation_request()`
   - `validate_cli_version()`

**Estimated effort:** 16-24 hours (high impact, should be done incrementally)

---

### 3.2 `expiry/main.py` - Long File (MEDIUM PRIORITY)

**Category:** Code Smell - Long File
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-expiry-service/expiry/main.py`
**Lines:** 1,794 lines

**Recommendation:** Split into:
1. `main.py` - Entry point and orchestration (~200 lines)
2. `expiry_checks.py` - `run_expiry_checks()` and related (~500 lines)
3. `snapshot_sync.py` - Snapshot syncing functions (~300 lines)
4. `pod_cleanup.py` - `cleanup_pod()` and related (~500 lines)
5. `warnings.py` - Warning message functions (~200 lines)

**Estimated effort:** 8-12 hours

---

### 3.3 Magic Numbers and Strings

**Category:** Code Smell - Magic Numbers
**Files:** Multiple

**Examples:**
```python
# reservation_handler.py
WaiterConfig={"Delay": 15, "MaxAttempts": 240}  # Up to 1 hour
WaiterConfig={"Delay": 5, "MaxAttempts": 60}

# expiry/main.py
PREPARING_TIMEOUT_SECONDS = 3600  # 1 hour (good - has constant)
FAILED_CLEANUP_WINDOW = 24 * 3600  # 24 hours
EXPIRED_CLEANUP_WINDOW = 7 * 24 * 3600  # 7 days
```

**Recommendation:** Create a `constants.py` file:
```python
# terraform-gpu-devservers/shared/constants.py
SNAPSHOT_WAIT_DELAY_SECONDS = 15
SNAPSHOT_WAIT_MAX_ATTEMPTS = 240  # ~1 hour
VOLUME_WAIT_DELAY_SECONDS = 5
VOLUME_WAIT_MAX_ATTEMPTS = 60  # ~5 minutes

PREPARING_TIMEOUT_SECONDS = 3600  # 1 hour
FAILED_CLEANUP_WINDOW_SECONDS = 24 * 3600  # 24 hours
EXPIRED_CLEANUP_WINDOW_SECONDS = 7 * 24 * 3600  # 7 days
STALE_QUEUE_THRESHOLD_SECONDS = 48 * 3600  # 48 hours
```

**Estimated effort:** 2-3 hours

---

### 3.4 Missing Type Hints (LOW PRIORITY)

**Category:** Code Smell - Missing Type Hints
**Files:** Multiple functions lack complete type hints

**Example:**
```python
# reservation_handler.py - many functions without return type hints
def migrate_ebs_across_az(user_id, current_volume_id, current_az, target_az):
    # Missing parameter and return type hints
```

**Recommendation:** Add type hints incrementally, starting with public API functions.

**Estimated effort:** 4-6 hours (ongoing)

---

## 4. ARCHITECTURE ISSUES

### 4.1 API Service Not Using Shared Module (HIGH PRIORITY)

**Category:** Architecture - Missing Abstraction
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`

**Problem:** API service cannot import from shared module due to Docker build context/PYTHONPATH issues, leading to code duplication.

**Current state (line 31-33):**
```python
# Note: This would work if shared module is in PYTHONPATH
# For now, we'll inline the function
# from shared import create_message_metadata
```

**Recommendation:**
1. Update API service Dockerfile to copy shared module
2. Add shared to PYTHONPATH in container
3. Remove duplicated code

**Estimated effort:** 2-4 hours

---

### 4.2 Inconsistent Error Handling Patterns

**Category:** Architecture - Inconsistent Patterns
**Files:** Multiple services

**Example patterns found:**
```python
# Pattern 1: Raise exception
raise RuntimeError(f"Error: {e}")

# Pattern 2: Log and continue
logger.error(f"Error: {e}")
# (continues execution)

# Pattern 3: Return tuple
return False, None, None

# Pattern 4: Return dict with error
return {"error": str(e), "success": False}
```

**Recommendation:** Standardize on a consistent error handling pattern:
1. Use custom exception classes for business logic errors
2. Use result objects or status enums for expected failure conditions
3. Document error handling policy

**Estimated effort:** 8-12 hours (refactoring across multiple services)

---

### 4.3 Database Access Pattern Inconsistency

**Category:** Architecture - Inconsistent Patterns
**Files:** Various services

**Problem:** Some code uses shared DB functions, others use raw SQL queries.

**Example of inconsistency:**
```python
# Good - using shared function
from shared.reservation_db import get_reservation
reservation = get_reservation(reservation_id)

# Inconsistent - inline SQL
with get_db_cursor() as cur:
    cur.execute("""
        SELECT * FROM reservations
        WHERE reservation_id LIKE %s || '%%'
    """, (reservation_id_prefix,))
```

**Recommendation:** Ensure all database access goes through shared DB modules (`reservation_db.py`, `disk_db.py`, etc.)

**Estimated effort:** 4-6 hours

---

### 4.4 Kubernetes Client Initialization Pattern

**Category:** Architecture - Inconsistent Patterns
**Files:** Multiple services

**Problem:** Each service implements its own singleton pattern for K8s client.

**Recommendation:**
1. Add `get_or_create_client()` to `shared/k8s_client.py`
2. Handle module-level caching there
3. All services import and use this single function

**Estimated effort:** 2-3 hours

---

## 5. PRIORITY MATRIX

| Issue | Priority | Effort | Impact |
|-------|----------|--------|--------|
| 3.1 Split reservation_handler.py | CRITICAL | 16-24h | High |
| 1.1 GPU_CONFIG duplication | HIGH | 4-6h | High |
| 2.1 Remove _REMOVED functions | HIGH | 0.5h | Low |
| 4.1 API service shared module | HIGH | 2-4h | Medium |
| 3.2 Split expiry/main.py | MEDIUM | 8-12h | Medium |
| 1.3 trigger_availability_update() | MEDIUM | 1-2h | Low |
| 1.4 get_k8s_client() singleton | MEDIUM | 2-3h | Low |
| 1.6 create_message_metadata() | MEDIUM | 2-3h | Low |
| 3.3 Magic numbers | MEDIUM | 2-3h | Medium |
| 1.2 ensure_utc() | LOW | 1-2h | Low |
| 1.5 _extract_ip_from_reservation | LOW | 0.5h | Low |
| 3.4 Type hints | LOW | 4-6h | Low |
| 4.2 Error handling patterns | LOW | 8-12h | Medium |
| 4.3 DB access consistency | LOW | 4-6h | Medium |

---

## 6. RECOMMENDED ACTION PLAN

### Phase 1: Quick Wins (1-2 days)
1. Remove dead `_REMOVED` functions
2. Consolidate `_extract_ip_from_reservation()`
3. Create shared `constants.py` for magic numbers

### Phase 2: Shared Module Improvements (1 week)
1. Move `ensure_utc()` to shared
2. Move `trigger_availability_update()` to shared
3. Standardize K8s client singleton pattern
4. Fix API service shared module import

### Phase 3: Major Refactoring (2-3 weeks)
1. Split `reservation_handler.py` into focused modules
2. Split `expiry/main.py` into focused modules
3. Consolidate GPU_CONFIG to single source of truth

### Phase 4: Ongoing Improvements
1. Add type hints incrementally
2. Standardize error handling patterns
3. Ensure all DB access uses shared functions

---

## 7. CONCLUSION

The codebase has grown organically with several areas needing cleanup. The most critical issue is the 8,000+ line `reservation_handler.py` file, which should be prioritized for splitting. The duplicate GPU configuration is a maintenance risk and should also be addressed soon.

Total estimated cleanup effort: **32-48 hours**

Quick wins can be completed in 1-2 days, while major refactoring should be done incrementally over 2-3 weeks to minimize risk.
