# Nested Context Managers - Important Behavioral Notes

## ⚠️ Critical Concept

**Each call to `get_db_cursor()`, `get_db_transaction()`, or `get_db_connection()` acquires a DIFFERENT connection from the pool, creating SEPARATE, INDEPENDENT transactions.**

This is **by design**, not a bug, but can be surprising if you expect nested transaction behavior.

---

## The Problem

### Example of Unexpected Behavior

```python
# This code looks like it should work, but doesn't!
with get_db_cursor() as cur1:
    cur1.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    print("Inserted Alice")
    
    with get_db_cursor() as cur2:
        cur2.execute("SELECT * FROM users WHERE id = 1")
        user = cur2.fetchone()
        print(f"Found user: {user}")  # Prints: Found user: None

# Why? cur2 is in a different transaction and can't see cur1's uncommitted insert!
```

**Output**:
```
Inserted Alice
Found user: None  ← Unexpected!
```

### Why This Happens

1. **cur1 and cur2 are from different connections**
   - `get_db_cursor()` called twice → 2 connections from pool
   - Each connection has its own independent transaction

2. **PostgreSQL transaction isolation**
   - Default isolation level: READ COMMITTED
   - Transactions can't see uncommitted changes from other transactions
   - cur1's INSERT is uncommitted when cur2's SELECT runs

3. **Independent commits**
   - cur2 completes first, commits its (read-only) transaction
   - cur1 completes second, commits its INSERT
   - No atomicity between the two operations

---

## Correct Patterns

### Pattern 1: Single Transaction with Multiple Cursors

**Use `get_db_transaction()` and create cursors on the same connection:**

```python
with get_db_transaction() as conn:
    with conn.cursor() as cur1:
        cur1.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    
    with conn.cursor() as cur2:
        cur2.execute("SELECT * FROM users WHERE id = 1")
        user = cur2.fetchone()
        print(f"Found user: {user}")  # Prints: Found user: {'id': 1, 'name': 'Alice'}

# Both operations commit together atomically
```

### Pattern 2: Reuse Same Cursor

**Simplest approach for multiple operations:**

```python
with get_db_cursor() as cur:
    cur.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    cur.execute("SELECT * FROM users WHERE id = 1")
    user = cur.fetchone()
    print(f"Found user: {user}")  # Prints: Found user: {'id': 1, 'name': 'Alice'}

# All operations in same transaction, commit together
```

### Pattern 3: Atomic Multi-Step Operation

**When you need all-or-nothing behavior:**

```python
def create_order_with_items(order_data, items):
    """Create order and items atomically"""
    try:
        with get_db_transaction() as conn:
            with conn.cursor() as cur:
                # Insert order
                cur.execute("""
                    INSERT INTO orders (user_id, total) 
                    VALUES (%s, %s) 
                    RETURNING order_id
                """, (order_data['user_id'], order_data['total']))
                order_id = cur.fetchone()['order_id']
                
                # Insert order items
                for item in items:
                    cur.execute("""
                        INSERT INTO order_items (order_id, product_id, quantity)
                        VALUES (%s, %s, %s)
                    """, (order_id, item['product_id'], item['quantity']))
                
                # Update inventory
                for item in items:
                    cur.execute("""
                        UPDATE products 
                        SET stock = stock - %s 
                        WHERE product_id = %s
                    """, (item['quantity'], item['product_id']))
        
        # All operations succeeded - all committed together
        return order_id
        
    except Exception as e:
        # Any failure rolls back EVERYTHING
        logger.error(f"Order creation failed: {e}")
        raise
```

---

## When Nested Connections Are Acceptable

### Use Case 1: Reading Committed Reference Data

```python
with get_db_cursor() as cur:
    # Main operation
    cur.execute("INSERT INTO orders (user_id, ...) VALUES (...)")
    
    # Lookup reference data (already committed, separate concern)
    with get_db_cursor(readonly=True) as ref_cur:
        ref_cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        product = ref_cur.fetchone()
    
    # Use product data in main transaction
    cur.execute("INSERT INTO order_items ...")
```

**Why this is OK**: Reference data is already committed, not part of current transaction's changes.

### Use Case 2: Independent Audit Logging

```python
def update_sensitive_data(user_id, new_data):
    """Update data and log access independently"""
    
    # Log the access attempt (commits independently)
    try:
        with get_db_cursor() as log_cur:
            log_cur.execute("""
                INSERT INTO audit_log (user_id, action, timestamp)
                VALUES (%s, 'data_update', NOW())
            """, (user_id,))
    except Exception as e:
        logger.warning(f"Audit logging failed: {e}")
        # Don't let logging failure stop the main operation
    
    # Update the data (separate transaction)
    with get_db_cursor() as cur:
        cur.execute("""
            UPDATE sensitive_data 
            SET data = %s 
            WHERE user_id = %s
        """, (new_data, user_id))
```

**Why this is OK**: Audit log should commit even if main operation fails (or vice versa).

### Use Case 3: Cached/Materialized View Updates

```python
with get_db_cursor() as cur:
    # Main write operation
    cur.execute("INSERT INTO events ...")
    
# Main operation committed

# Update cache in separate transaction (failure doesn't affect main operation)
try:
    with get_db_cursor() as cache_cur:
        cache_cur.execute("REFRESH MATERIALIZED VIEW event_summary")
except Exception as e:
    logger.warning(f"Cache refresh failed: {e}")
```

---

## Common Pitfalls

### Pitfall 1: Partial Commits

```python
# ❌ DANGER: Partial commits possible
try:
    with get_db_cursor() as cur1:
        cur1.execute("INSERT INTO orders ...")
    # Order committed here ✓
    
    with get_db_cursor() as cur2:
        cur2.execute("INSERT INTO order_items ...")
        raise Exception("Oops!")
    # Order items rolled back ✗
    
except Exception:
    # Order exists but has no items - data inconsistency!
    pass
```

**Fix**: Use single transaction:

```python
# ✅ CORRECT: All-or-nothing
try:
    with get_db_transaction() as conn:
        with conn.cursor() as cur:
            cur.execute("INSERT INTO orders ...")
            cur.execute("INSERT INTO order_items ...")
            # Both commit together or both rollback
except Exception:
    # Neither exists - data is consistent
    pass
```

### Pitfall 2: Connection Pool Exhaustion

```python
# ❌ DANGER: Can exhaust pool
def process_recursively(items, depth=0):
    with get_db_cursor() as cur:  # Gets a connection
        cur.execute("SELECT ...")
        
        if depth < len(items):
            # Recursive call gets another connection!
            process_recursively(items, depth + 1)
            # Now holding 2+ connections...

# With 20-item list and 20-max pool, this fails!
process_recursively(items)
```

**Fix**: Pass connection through:

```python
# ✅ CORRECT: Reuse connection
def process_recursively(cur, items, depth=0):
    cur.execute("SELECT ...")
    
    if depth < len(items):
        process_recursively(cur, items, depth + 1)

with get_db_cursor() as cur:
    process_recursively(cur, items)
```

### Pitfall 3: Deadlock Risk

```python
# ❌ DANGER: Deadlock risk
with get_db_cursor() as cur1:
    cur1.execute("UPDATE users SET ... WHERE id = 1")  # Locks user 1
    
    with get_db_cursor() as cur2:
        cur2.execute("UPDATE users SET ... WHERE id = 2")  # Locks user 2
        
        # If another process has locks in opposite order: DEADLOCK!
```

**Fix**: Single transaction:

```python
# ✅ CORRECT: Single transaction, deterministic lock order
with get_db_cursor() as cur:
    # Both locks acquired in same transaction
    cur.execute("UPDATE users SET ... WHERE id = 1")
    cur.execute("UPDATE users SET ... WHERE id = 2")
```

---

## Isolation Levels and Visibility

### Default: READ COMMITTED

```python
# Transaction 1
with get_db_cursor() as cur1:
    cur1.execute("INSERT INTO users VALUES (1, 'Alice')")
    # Not committed yet
    
    # Transaction 2 (nested context manager)
    with get_db_cursor() as cur2:
        cur2.execute("SELECT * FROM users WHERE id = 1")
        # Returns None - can't see uncommitted data
        
    # Transaction 2 completes and commits (nothing to commit)
    
# Transaction 1 commits here
# Now the insert is visible to other transactions
```

### What Each Transaction Sees

| Time | Transaction 1 | Transaction 2 | What T2 Sees |
|------|---------------|---------------|--------------|
| T0 | BEGIN | - | - |
| T1 | INSERT user 1 | - | - |
| T2 | (uncommitted) | BEGIN | No user 1 (uncommitted) |
| T3 | (uncommitted) | SELECT user 1 | No user 1 (isolation) |
| T4 | (uncommitted) | COMMIT | - |
| T5 | COMMIT | - | - |
| T6 | - | BEGIN | User 1 visible (committed) |

---

## PostgreSQL Savepoints (Advanced)

For true nested transaction behavior, use savepoints:

```python
with get_db_transaction() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO orders ...")
        
        # Create savepoint for "nested transaction"
        cur.execute("SAVEPOINT items_savepoint")
        
        try:
            # Operations that might fail
            cur.execute("INSERT INTO order_items ...")
            cur.execute("UPDATE inventory ...")
        except Exception as e:
            # Rollback to savepoint (keeps order insert)
            logger.warning(f"Items failed: {e}")
            cur.execute("ROLLBACK TO SAVEPOINT items_savepoint")
        else:
            # Success - release savepoint
            cur.execute("RELEASE SAVEPOINT items_savepoint")
        
        # Continue with main transaction
        cur.execute("UPDATE user_stats ...")
    
# Everything commits (including order even if items failed)
```

---

## Quick Reference

### ❌ Don't Do This

```python
# Nested cursors expecting same transaction
with get_db_cursor() as cur1:
    cur1.execute("INSERT ...")
    with get_db_cursor() as cur2:
        cur2.execute("SELECT ...")  # Won't see insert!
```

### ✅ Do This Instead

```python
# Option 1: Single cursor
with get_db_cursor() as cur:
    cur.execute("INSERT ...")
    cur.execute("SELECT ...")  # Sees insert

# Option 2: Multiple cursors, same connection
with get_db_transaction() as conn:
    with conn.cursor() as cur1:
        cur1.execute("INSERT ...")
    with conn.cursor() as cur2:
        cur2.execute("SELECT ...")  # Sees insert
```

---

## Summary

**Key Points**:

1. Each context manager call = new connection = new transaction
2. Nested context managers = separate transactions (can't see each other's uncommitted changes)
3. For atomic operations: use ONE transaction with multiple cursors or cursor reuse
4. Nested connections are OK for:
   - Reading committed reference data
   - Independent logging/auditing
   - Fire-and-forget operations
5. Watch out for:
   - Partial commits (data inconsistency)
   - Connection pool exhaustion
   - Deadlock risks

**When in doubt**: Use a single `with get_db_cursor()` or `with get_db_transaction()` for related operations.

