# Database Connection Pool Usage Guide

This document explains how to use the PostgreSQL connection pool in the shared utilities.

## Overview

The `db_pool` module provides a thread-safe connection pool for PostgreSQL that handles:
- Connection pooling (reuse connections efficiently)
- Automatic transaction management (commit/rollback)
- Safe connection cleanup (no leaks)
- Context managers for clean code

## Quick Start

### Simple Queries (Recommended)

For most use cases, use `get_db_cursor()` context manager:

```python
from shared.db_pool import get_db_cursor

# Write query (INSERT, UPDATE, DELETE)
with get_db_cursor() as cur:
    cur.execute("""
        INSERT INTO users (user_id, email)
        VALUES (%s, %s)
    """, (user_id, email))
    # Auto-commits on success, auto-rollback on exception

# Read query (SELECT)
with get_db_cursor(readonly=True) as cur:
    cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
    user = cur.fetchone()
    # Auto-commits (readonly is an optimization hint)
```

### Manual Transaction Control

If you need more control over transactions:

```python
from shared.db_pool import get_db_transaction

with get_db_transaction() as conn:
    with conn.cursor() as cur:
        cur.execute("INSERT INTO ...")
        # Do more work
        cur.execute("UPDATE ...")
        # Auto-commits on success, auto-rollback on exception
```

### Direct Connection Access (Advanced)

For maximum control, manage connections directly:

```python
from shared.db_pool import get_db_connection

with get_db_connection() as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT ...")
        results = cur.fetchall()
    conn.commit()  # YOU must commit explicitly
    # Connection automatically returned to pool
```

## Connection Pool Configuration

The pool is initialized automatically on first use with environment variables:

```bash
POSTGRES_HOST=postgres-primary.controlplane.svc.cluster.local
POSTGRES_PORT=5432
POSTGRES_USER=gpudev
POSTGRES_PASSWORD=your_password  # REQUIRED
POSTGRES_DB=gpudev
```

Default pool settings:
- Minimum connections: 1
- Maximum connections: 20
- Connection acquisition timeout: 30 seconds
- Health check enabled: Yes (configurable via `DB_POOL_HEALTH_CHECK`)
- Health check max retries: 3

### Connection Health Checks

Connections are automatically tested for health before being returned from the pool. This prevents errors from stale connections due to:
- Network issues
- Database restarts
- Idle connection timeouts
- Connection drops

**How it works**:
1. When getting a connection, execute `SELECT 1` to verify it's alive
2. If check fails, close the stale connection and get another one
3. Retry up to 3 times to find a healthy connection
4. If all attempts fail, raise `ConnectionHealthCheckError`

**Configuration**:
```bash
# Disable health checks (not recommended, but available for performance)
export DB_POOL_HEALTH_CHECK=false
```

**Performance**: Health checks add ~1-2ms per connection acquisition from pool.

### Connection State Management

Connections are automatically cleaned before being returned to the pool:

✅ **Automatically cleared**:
- Uncommitted transactions (rollback is always called)
- SET LOCAL variables (transaction-scoped)
- Temporary tables created with ON COMMIT DROP
- Transaction isolation level changes
- Savepoints

⚠️ **Persists across uses** (session-scoped, rare in practice):
- SET variables (without LOCAL keyword)
- PREPARE statements
- Temporary tables with ON COMMIT PRESERVE ROWS

This means you can safely use connection pooling without worrying about state leaking between different uses of the same connection.

### Custom Initialization (Optional)

You can explicitly initialize the pool with custom settings:

```python
from shared.db_pool import init_connection_pool

init_connection_pool(
    minconn=2,
    maxconn=50,
    host="custom-host",
    port=5432
)
```

## Best Practices

### ✅ DO

1. **Use context managers** - They handle cleanup automatically:
   ```python
   with get_db_cursor() as cur:
       cur.execute(...)
   ```

2. **Use readonly=True for SELECT queries** - It's an optimization:
   ```python
   with get_db_cursor(readonly=True) as cur:
       cur.execute("SELECT ...")
   ```

3. **Use parameterized queries** - Prevents SQL injection:
   ```python
   cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
   ```

4. **Let exceptions propagate** - The context manager handles rollback:
   ```python
   try:
       with get_db_cursor() as cur:
           cur.execute("INSERT ...")
   except Exception as e:
       logger.error(f"Failed: {e}")
       # Rollback already happened automatically
   ```

### ❌ DON'T

1. **Don't create connections manually** - Use the pool:
   ```python
   # ❌ BAD
   conn = psycopg2.connect(...)
   
   # ✅ GOOD
   with get_db_connection() as conn:
       ...
   ```

2. **Don't forget to close cursors** - Use context managers:
   ```python
   # ❌ BAD
   conn = get_db_connection_simple()
   cur = conn.cursor()
   cur.execute(...)
   # Forgot to close cursor and return connection!
   
   # ✅ GOOD
   with get_db_connection() as conn:
       with conn.cursor() as cur:
           cur.execute(...)
   ```

3. **Don't mix pool and direct connections** - Pick one approach:
   ```python
   # ❌ BAD
   conn = psycopg2.connect(...)  # Bypasses pool
   
   # ✅ GOOD
   with get_db_connection() as conn:
       ...
   ```

4. **Don't use global connections** - Get fresh connections from pool:
   ```python
   # ❌ BAD
   global_conn = get_db_connection_simple()
   
   # ✅ GOOD
   def my_function():
       with get_db_connection() as conn:
           ...
   ```

5. **Don't nest context managers expecting same transaction** - They get different connections:
   ```python
   # ❌ BAD - Different connections, separate transactions
   with get_db_cursor() as cur1:
       cur1.execute("INSERT INTO users ...")
       with get_db_cursor() as cur2:
           # This won't see cur1's uncommitted insert!
           cur2.execute("SELECT * FROM users ...")
   
   # ✅ GOOD - Same connection, same transaction
   with get_db_transaction() as conn:
       with conn.cursor() as cur1:
           cur1.execute("INSERT INTO users ...")
       with conn.cursor() as cur2:
           # This sees the insert - same transaction
           cur2.execute("SELECT * FROM users ...")
   ```

## ⚠️ Important: Nested Context Managers Get Different Connections

**Critical concept**: Each call to `get_db_cursor()` or `get_db_transaction()` gets a **different connection** from the pool, creating **separate, independent transactions**.

### ❌ Common Mistake: Expecting Nested Transactions

```python
# This does NOT work as expected!
with get_db_cursor() as cur1:
    cur1.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    # cur1 transaction has uncommitted insert
    
    with get_db_cursor() as cur2:
        # cur2 is a DIFFERENT connection/transaction!
        cur2.execute("SELECT * FROM users WHERE id = 1")
        user = cur2.fetchone()
        # user is None! cur2 can't see cur1's uncommitted data
```

**Why this happens**: PostgreSQL transaction isolation prevents one transaction from seeing uncommitted changes from another transaction.

### ✅ Correct Pattern: Multiple Operations in Same Transaction

**Option 1: Use get_db_transaction() with multiple cursors**

```python
with get_db_transaction() as conn:
    # All cursors share the same connection/transaction
    with conn.cursor() as cur1:
        cur1.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    
    with conn.cursor() as cur2:
        cur2.execute("SELECT * FROM users WHERE id = 1")
        user = cur2.fetchone()
        # user is {'id': 1, 'name': 'Alice'} ✓ Works!
# Everything commits together atomically
```

**Option 2: Reuse the same cursor**

```python
with get_db_cursor() as cur:
    # All operations use same cursor/transaction
    cur.execute("INSERT INTO users (id, name) VALUES (1, 'Alice')")
    cur.execute("SELECT * FROM users WHERE id = 1")
    user = cur.fetchone()
    # user is {'id': 1, 'name': 'Alice'} ✓ Works!
# Everything commits together
```

### When Nested Connections Are Acceptable

**Separate, independent operations**:

```python
# Reading committed reference data is fine
with get_db_cursor() as cur:
    cur.execute("INSERT INTO orders ...")
    
    # Look up reference data (separate query, already committed)
    with get_db_cursor(readonly=True) as ref_cur:
        ref_cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
        product = ref_cur.fetchone()
```

**Fire-and-forget logging**:

```python
# Audit log should commit even if main operation fails
with get_db_cursor() as cur:
    cur.execute("UPDATE sensitive_data ...")
    
    # Log the access (separate transaction, commits independently)
    with get_db_cursor() as log_cur:
        log_cur.execute("INSERT INTO audit_log ...")
```

### Pool Exhaustion Risk

```python
# ❌ BAD: Can exhaust pool with deep nesting
def recursive_query(depth):
    with get_db_cursor() as cur:  # Takes a connection
        if depth > 0:
            recursive_query(depth - 1)  # Takes another connection!
        cur.execute("SELECT ...")

recursive_query(25)  # Could exhaust 20-connection pool!

# ✅ GOOD: Pass connection through
def recursive_query(cur, depth):
    if depth > 0:
        recursive_query(cur, depth - 1)
    cur.execute("SELECT ...")

with get_db_cursor() as cur:
    recursive_query(cur, 25)  # Only uses 1 connection
```

### Summary

| Pattern | Connections Used | Transactions | Sees Uncommitted Data? |
|---------|------------------|--------------|------------------------|
| Nested `get_db_cursor()` | Different (2+) | Separate | ❌ No |
| Multiple cursors on same conn | Same (1) | Same | ✅ Yes |
| Reuse same cursor | Same (1) | Same | ✅ Yes |

**Rule of thumb**: If operations need to be atomic (all succeed or all fail together), use ONE connection/transaction.

---

## Common Patterns

### Insert with ON CONFLICT (Upsert)

```python
from shared.db_pool import get_db_cursor

with get_db_cursor() as cur:
    cur.execute("""
        INSERT INTO users (user_id, email, name)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id)
        DO UPDATE SET
            email = EXCLUDED.email,
            name = EXCLUDED.name
    """, (user_id, email, name))
```

### Batch Insert

```python
from shared.db_pool import get_db_cursor

records = [(1, "user1"), (2, "user2"), (3, "user3")]

with get_db_cursor() as cur:
    cur.executemany("""
        INSERT INTO users (user_id, name)
        VALUES (%s, %s)
    """, records)
```

### Query with Results

```python
from shared.db_pool import get_db_cursor

with get_db_cursor(readonly=True) as cur:
    cur.execute("SELECT * FROM users WHERE active = %s", (True,))
    users = cur.fetchall()
    
    for user in users:
        print(f"User: {user['user_id']} - {user['email']}")
```

### Multiple Operations in One Transaction

```python
from shared.db_pool import get_db_transaction

with get_db_transaction() as conn:
    with conn.cursor() as cur:
        # Operation 1
        cur.execute("INSERT INTO orders (...) VALUES (...)")
        cur.execute("SELECT lastval()")
        order_id = cur.fetchone()['lastval']
        
        # Operation 2
        cur.execute("INSERT INTO order_items (...) VALUES (...)", 
                   (order_id, ...))
        
        # Operation 3
        cur.execute("UPDATE inventory SET quantity = quantity - 1 WHERE ...")
        
        # All operations commit together, or all rollback on error
```

### Handling Specific Errors

```python
from shared.db_pool import (
    get_db_cursor, 
    ConnectionPoolExhaustedError,
    ConnectionHealthCheckError
)
import psycopg2

try:
    with get_db_cursor() as cur:
        cur.execute("INSERT INTO users ...")
except ConnectionHealthCheckError as e:
    logger.error(f"Unable to get healthy connection: {e}")
    # Database may be down, network issues, or all connections are broken
    # Consider: retry after delay, alert ops, use fallback mechanism
except ConnectionPoolExhaustedError as e:
    logger.error(f"Connection pool exhausted: {e}")
    # Pool is at capacity - consider increasing maxconn or investigating leaks
except psycopg2.IntegrityError as e:
    logger.error(f"Duplicate key or constraint violation: {e}")
except psycopg2.OperationalError as e:
    logger.error(f"Database connection issue: {e}")
except Exception as e:
    logger.error(f"Unexpected error: {e}")
```

### Using Custom Timeout

```python
from shared.db_pool import get_db_cursor

# Use a longer timeout for potentially slow operations
try:
    with get_db_cursor(timeout=60) as cur:
        cur.execute("SELECT * FROM large_table WHERE ...")
        results = cur.fetchall()
except ConnectionPoolExhaustedError:
    logger.error("Could not get connection within 60 seconds")
    # Handle pool exhaustion - maybe retry later or alert
```

## Monitoring

Check pool statistics:

```python
from shared.db_pool import get_pool_stats

stats = get_pool_stats()
print(f"Pool: min={stats['minconn']}, max={stats['maxconn']}, closed={stats['closed']}")
```

## Shutdown

Close the pool when shutting down (in main application):

```python
from shared.db_pool import close_connection_pool

# At application shutdown
close_connection_pool()
```

## Migration from Old Code

If you have old code that creates connections directly:

### Before (Old)

```python
import psycopg2

conn = psycopg2.connect(
    host=os.environ.get("POSTGRES_HOST"),
    ...
)
try:
    with conn.cursor() as cur:
        cur.execute(...)
    conn.commit()
except Exception as e:
    conn.rollback()
    raise
finally:
    conn.close()
```

### After (New)

```python
from shared.db_pool import get_db_cursor

with get_db_cursor() as cur:
    cur.execute(...)
# That's it! Automatic commit/rollback/cleanup
```

## Troubleshooting

### "Failed to initialize connection pool"
- Check that `POSTGRES_PASSWORD` environment variable is set
- Verify network connectivity to PostgreSQL host
- Check PostgreSQL logs for connection issues

### "ConnectionPoolExhaustedError: Connection pool exhausted after 30s"
**Cause**: All connections in the pool are in use and none became available within the timeout.

**Solutions**:
1. **Increase pool size**: Call `init_connection_pool(maxconn=50)` at startup
2. **Increase timeout**: Use `get_db_cursor(timeout=60)` for operations that may need to wait longer
3. **Find connection leaks**: Check for code not using context managers or holding connections too long
4. **Optimize queries**: Look for long-running queries blocking connections
5. **Monitor usage**: Use `get_pool_stats()` to see pool configuration

**Investigation**:
```python
# Check pool stats
from shared import get_pool_stats
stats = get_pool_stats()
print(f"Pool: max={stats['maxconn']}, closed={stats['closed']}")

# Check PostgreSQL for active connections
# SELECT count(*) FROM pg_stat_activity WHERE application_name = 'gpu-dev-shared';
```

### "ConnectionHealthCheckError: Unable to get healthy connection"
**Cause**: All connection attempts returned stale/broken connections after 3 retries.

**Solutions**:
1. **Check database availability**: Database may be down or unreachable
2. **Check network**: Network issues between app and database
3. **Check database logs**: Look for connection errors or resource limits
4. **Restart application**: Clears pool and establishes fresh connections
5. **Verify credentials**: Connection parameters might be incorrect

**Investigation**:
```bash
# Check if database is up
psql -h postgres-host -U gpudev -d gpudev -c "SELECT 1"

# Check network connectivity
ping postgres-host
telnet postgres-host 5432

# Check application logs for warnings about stale connections
grep "Stale connection detected" logs/
```

### "Stale connection" or "Server closed connection"
- ✅ **Now handled automatically** - Health checks detect and replace stale connections
- If `ConnectionHealthCheckError` is raised, database may be down
- Check database and network connectivity

## Thread Safety

All pool operations are thread-safe. You can safely use the pool from:
- Multiple threads in a single process
- Multiple Kubernetes pod replicas (each has its own pool)
- CronJobs and Deployments

Each thread/request should get its own connection from the pool using context managers.

