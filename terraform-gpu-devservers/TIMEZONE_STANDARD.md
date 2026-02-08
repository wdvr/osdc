# Timezone Handling Standard

## 🌍 Project-Wide Timezone Policy

**RULE: Always use timezone-aware datetime objects with UTC timezone.**

This project follows a strict timezone handling policy to avoid subtle bugs:

1. ✅ **Always use `datetime.now(UTC)`** for current time
2. ❌ **Never use `datetime.utcnow()`** (returns naive datetime)
3. ❌ **Never use `datetime.now()`** without timezone (returns naive datetime)
4. ✅ **PostgreSQL schema uses `TIMESTAMP WITH TIME ZONE`**
5. ✅ **All datetime comparisons use timezone-aware datetimes**

---

## 📚 Background: Why This Matters

### The Problem with Naive Datetimes

Python's `datetime` can be either:
- **Naive**: No timezone information (`tzinfo=None`)
- **Aware**: Has timezone information (`tzinfo` set)

Mixing naive and aware datetimes causes:
- ❌ `TypeError` when comparing naive vs aware
- ❌ Incorrect time calculations across timezones
- ❌ DST (Daylight Saving Time) bugs
- ❌ Data corruption when times are misinterpreted

### PostgreSQL and Timezones

PostgreSQL `TIMESTAMP WITH TIME ZONE`:
- Stores all times internally as UTC
- Converts input to UTC automatically
- Returns timezone-aware datetimes via psycopg2/asyncpg
- **Requires timezone-aware Python datetimes for consistency**

---

## ✅ Correct Patterns

### Getting Current Time

```python
from datetime import datetime, UTC, timedelta

# ✅ CORRECT - Timezone-aware UTC datetime
now = datetime.now(UTC)
later = datetime.now(UTC) + timedelta(hours=1)
timestamp = datetime.now(UTC).isoformat()

# ❌ WRONG - Naive datetime (no timezone)
now = datetime.utcnow()  # Returns naive datetime!
now = datetime.now()     # Returns naive datetime in local time!
```

### Creating Specific Datetimes

```python
from datetime import datetime, UTC

# ✅ CORRECT - Explicitly set UTC timezone
specific_time = datetime(2024, 1, 15, 12, 30, 0, tzinfo=UTC)

# ✅ CORRECT - Parse ISO string with timezone
from datetime import datetime
dt = datetime.fromisoformat("2024-01-15T12:30:00+00:00")

# ❌ WRONG - Naive datetime
specific_time = datetime(2024, 1, 15, 12, 30, 0)  # No tzinfo!
```

### Comparing Datetimes

```python
from datetime import datetime, UTC

# ✅ CORRECT - Both timezone-aware
expires_at = datetime.now(UTC) + timedelta(hours=24)
if datetime.now(UTC) > expires_at:
    print("Expired")

# ❌ WRONG - TypeError: can't compare offset-naive and offset-aware datetimes
expires_at = datetime.utcnow() + timedelta(hours=24)  # Naive
if datetime.now(UTC) > expires_at:  # Comparing aware to naive - ERROR!
    print("Expired")
```

### Database Operations

```python
from datetime import datetime, UTC

# ✅ CORRECT - Store timezone-aware datetime
created_at = datetime.now(UTC)
cur.execute("""
    INSERT INTO reservations (reservation_id, created_at)
    VALUES (%s, %s)
""", (reservation_id, created_at))

# ✅ CORRECT - PostgreSQL returns timezone-aware
cur.execute("SELECT created_at FROM reservations WHERE id = %s", (rid,))
row = cur.fetchone()
created_at = row['created_at']  # Already timezone-aware from PostgreSQL
assert created_at.tzinfo is not None  # ✅ Has timezone info
```

### Defensive Timezone Handling

For cases where you might receive naive datetimes from legacy code:

```python
from datetime import datetime, UTC

def ensure_utc(dt: datetime | None) -> datetime | None:
    """
    Ensure a datetime is timezone-aware and in UTC.
    
    Defensive function to handle potential naive datetimes from legacy
    code or external sources.
    """
    if dt is None:
        return None
    
    # If already timezone-aware, convert to UTC
    if dt.tzinfo is not None:
        return dt.astimezone(UTC)
    
    # If naive, assume it's already in UTC and make it aware
    # WARNING: This assumes naive datetimes are in UTC!
    return dt.replace(tzinfo=UTC)

# Usage:
expires_at = ensure_utc(some_datetime_from_legacy_code)
if datetime.now(UTC) > expires_at:
    print("Expired")
```

---

## 🔍 Finding and Fixing Issues

### Search for Problems

```bash
# Find datetime.utcnow() usage (WRONG)
grep -rn "datetime.utcnow()" --include="*.py" .

# Find datetime.now() without UTC (WRONG)
grep -rn "datetime.now()" --include="*.py" . | grep -v "datetime.now(UTC)"

# Find correct usage (VERIFY)
grep -rn "datetime.now(UTC)" --include="*.py" .
```

### Replacement Patterns

```python
# OLD (WRONG):
datetime.utcnow()
datetime.utcnow().isoformat()
datetime.utcnow() + timedelta(hours=1)

# NEW (CORRECT):
datetime.now(UTC)
datetime.now(UTC).isoformat()
datetime.now(UTC) + timedelta(hours=1)
```

---

## 📋 Migration Checklist

When adding new code or reviewing existing code:

- [ ] All `datetime.now()` calls have `UTC` argument
- [ ] No `datetime.utcnow()` calls exist
- [ ] All datetime objects are timezone-aware
- [ ] All datetime comparisons use aware datetimes
- [ ] Database TIMESTAMP columns use `WITH TIME ZONE`
- [ ] Imports include: `from datetime import datetime, UTC, timedelta`

---

## 🏗️ Architecture Standards

### Database Schema
```sql
-- ✅ CORRECT - Store with timezone
created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
expires_at TIMESTAMP WITH TIME ZONE

-- ❌ WRONG - No timezone info
created_at TIMESTAMP DEFAULT NOW()
```

### Python Imports
```python
# ✅ CORRECT - Import UTC
from datetime import datetime, UTC, timedelta

# ✅ ALSO ACCEPTABLE (older Python)
from datetime import datetime, timezone, timedelta
UTC = timezone.utc

# ❌ WRONG - Missing UTC
from datetime import datetime, timedelta
```

### API Responses
```python
# ✅ CORRECT - ISO format includes timezone
{
    "created_at": "2024-01-15T12:30:00+00:00",  # Has +00:00 timezone
    "expires_at": "2024-01-16T12:30:00Z"        # Z means UTC
}

# ❌ WRONG - No timezone indicator
{
    "created_at": "2024-01-15T12:30:00",  # Ambiguous!
    "expires_at": "2024-01-16T12:30:00"
}
```

---

## 🧪 Testing Timezone Handling

```python
import pytest
from datetime import datetime, UTC

def test_datetime_is_aware():
    """Verify all datetimes are timezone-aware"""
    now = datetime.now(UTC)
    
    # Should not raise
    assert now.tzinfo is not None
    assert now.tzinfo == UTC

def test_datetime_comparison():
    """Verify datetime comparisons work correctly"""
    past = datetime.now(UTC)
    future = datetime.now(UTC) + timedelta(hours=1)
    
    # Should not raise TypeError
    assert future > past
    assert past < future

def test_database_returns_aware_datetime(db_cursor):
    """Verify PostgreSQL returns timezone-aware datetimes"""
    cur.execute("SELECT NOW() as current_time")
    row = cur.fetchone()
    
    assert row['current_time'].tzinfo is not None
```

---

## 🚨 Common Mistakes to Avoid

### Mistake 1: Using datetime.utcnow()
```python
# ❌ WRONG - Returns naive datetime
now = datetime.utcnow()
print(now.tzinfo)  # Prints: None

# ✅ CORRECT - Returns aware datetime
now = datetime.now(UTC)
print(now.tzinfo)  # Prints: UTC
```

### Mistake 2: Comparing naive and aware
```python
# ❌ WRONG - TypeError
naive = datetime.utcnow()
aware = datetime.now(UTC)
if naive < aware:  # ERROR: can't compare offset-naive and offset-aware
    pass

# ✅ CORRECT - Both aware
time1 = datetime.now(UTC)
time2 = datetime.now(UTC)
if time1 < time2:  # Works perfectly
    pass
```

### Mistake 3: Forgetting timezone in constructor
```python
# ❌ WRONG - Creates naive datetime
dt = datetime(2024, 1, 15, 12, 30)
print(dt.tzinfo)  # Prints: None

# ✅ CORRECT - Creates aware datetime
dt = datetime(2024, 1, 15, 12, 30, tzinfo=UTC)
print(dt.tzinfo)  # Prints: UTC
```

### Mistake 4: Using local timezone
```python
# ❌ WRONG - Different results on different servers
dt = datetime.now()  # Uses server's local timezone!

# ✅ CORRECT - Consistent everywhere
dt = datetime.now(UTC)  # Always UTC
```

---

## 📖 References

- **api-service/app/main.py** - Reference implementation with `ensure_utc()` helper
- **PostgreSQL Documentation** - [TIMESTAMP WITH TIME ZONE](https://www.postgresql.org/docs/current/datatype-datetime.html)
- **Python datetime** - [Aware and Naive Objects](https://docs.python.org/3/library/datetime.html#aware-and-naive-objects)
- **PEP 615** - [Support for the IANA Time Zone Database](https://peps.python.org/pep-0615/)

---

## 🎯 Summary

**Golden Rule:** 
```python
from datetime import datetime, UTC

# Always use:
datetime.now(UTC)

# Never use:
datetime.utcnow()  # ❌
datetime.now()     # ❌
```

**Why it matters:**
- Prevents TypeError in comparisons
- Ensures correct behavior across timezones
- Works seamlessly with PostgreSQL
- Makes time calculations reliable
- Eliminates DST bugs

**When in doubt:** Use `datetime.now(UTC)` - it's always correct! ✅

