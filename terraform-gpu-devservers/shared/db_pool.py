"""
Database Connection Pool Manager
Provides connection pooling and safe connection handling for PostgreSQL
"""

import logging
import os
import threading
import time
from contextlib import contextmanager
from typing import Optional

import psycopg2
from psycopg2 import pool
from psycopg2.extras import RealDictCursor

logger = logging.getLogger(__name__)

# Global connection pool (initialized once)
_connection_pool: Optional[pool.ThreadedConnectionPool] = None
_pool_lock = threading.Lock()

# Default connection acquisition timeout (seconds)
DEFAULT_CONNECTION_TIMEOUT = 30

# Health check configuration
ENABLE_HEALTH_CHECK = os.environ.get("DB_POOL_HEALTH_CHECK", "true").lower() == "true"
HEALTH_CHECK_MAX_RETRIES = 3


class ConnectionPoolExhaustedError(Exception):
    """Raised when connection pool is exhausted and timeout is reached"""
    pass


class ConnectionHealthCheckError(Exception):
    """Raised when connection health check fails after max retries"""
    pass


def init_connection_pool(
    minconn: int = 1,
    maxconn: int = 50,  # Increased from 20 to support multinode parallel processing
    host: Optional[str] = None,
    port: Optional[int] = None,
    user: Optional[str] = None,
    password: Optional[str] = None,
    database: Optional[str] = None
) -> pool.ThreadedConnectionPool:
    """
    Initialize the global connection pool.
    
    Thread-safe: Can be called multiple times safely (subsequent calls are no-ops).
    
    Args:
        minconn: Minimum number of connections to maintain
        maxconn: Maximum number of connections allowed
        host: PostgreSQL host (default: from POSTGRES_HOST env)
        port: PostgreSQL port (default: from POSTGRES_PORT env)
        user: Database user (default: from POSTGRES_USER env)
        password: Database password (default: from POSTGRES_PASSWORD env)
        database: Database name (default: from POSTGRES_DB env)
    
    Returns:
        ThreadedConnectionPool instance
        
    Raises:
        ValueError: If required environment variables are missing
        RuntimeError: If pool is already initialized with different parameters
    """
    global _connection_pool
    
    # Check if already initialized (without lock for performance)
    if _connection_pool is not None:
        logger.debug("Connection pool already initialized")
        return _connection_pool
    
    # Use provided values or fall back to environment variables
    host = host or os.environ.get("POSTGRES_HOST", "postgres-primary.controlplane.svc.cluster.local")
    port_str = os.environ.get("POSTGRES_PORT", "5432")
    user = user or os.environ.get("POSTGRES_USER", "gpudev")
    password = password or os.environ.get("POSTGRES_PASSWORD")
    database = database or os.environ.get("POSTGRES_DB", "gpudev")
    
    # Validate required parameters with helpful error messages
    missing_vars = []
    
    if not password or (isinstance(password, str) and not password.strip()):
        missing_vars.append("POSTGRES_PASSWORD")
    
    if not host or (isinstance(host, str) and not host.strip()):
        missing_vars.append("POSTGRES_HOST")
    
    if not user or (isinstance(user, str) and not user.strip()):
        missing_vars.append("POSTGRES_USER")
    
    if not database or (isinstance(database, str) and not database.strip()):
        missing_vars.append("POSTGRES_DB")
    
    if missing_vars:
        raise ValueError(
            f"Missing required environment variable(s): {', '.join(missing_vars)}. "
            f"Please set them before initializing the connection pool. "
            f"Example: export POSTGRES_PASSWORD='your-password'"
        )
    
    # Validate and convert port
    if port is None:
        try:
            port = int(port_str)
            if port < 1 or port > 65535:
                raise ValueError(f"POSTGRES_PORT must be between 1 and 65535, got: {port}")
        except ValueError as e:
            raise ValueError(
                f"Invalid POSTGRES_PORT: '{port_str}'. Must be a valid integer between 1-65535. "
                f"Error: {e}"
            )
    
    logger.info(f"Initializing connection pool: {user}@{host}:{port}/{database} (min={minconn}, max={maxconn})")
    
    try:
        _connection_pool = pool.ThreadedConnectionPool(
            minconn,
            maxconn,
            host=host,
            port=port,
            user=user,
            password=password,
            dbname=database,
            cursor_factory=RealDictCursor,
            # Connection timeout
            connect_timeout=10,
            # Set application name for monitoring
            application_name=os.environ.get("APP_NAME", "gpu-dev-shared")
        )
        
        logger.info("Connection pool initialized successfully")
        return _connection_pool
        
    except Exception as e:
        logger.error(f"Failed to initialize connection pool: {e}")
        raise


def _check_connection_health(conn: psycopg2.extensions.connection) -> bool:
    """
    Check if a connection is healthy by executing a simple query.
    
    Args:
        conn: Database connection to check
        
    Returns:
        True if connection is healthy, False otherwise
    """
    try:
        # Quick health check - SELECT 1 is very fast
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            result = cur.fetchone()
            return result is not None
    except Exception as e:
        logger.debug(f"Connection health check failed: {e}")
        return False


def _get_connection_with_timeout(
    pool_instance: pool.ThreadedConnectionPool, 
    timeout: float,
    check_health: bool = True
) -> psycopg2.extensions.connection:
    """
    Get a connection from the pool with timeout and optional health check.
    
    Args:
        pool_instance: The connection pool
        timeout: Maximum seconds to wait for a connection
        check_health: If True, verify connection is healthy before returning
        
    Returns:
        Healthy database connection
        
    Raises:
        ConnectionPoolExhaustedError: If timeout is reached
        ConnectionHealthCheckError: If unable to get healthy connection after retries
    """
    start_time = time.time()
    last_error = None
    retry_interval = 0.1  # Start with 100ms between retries
    max_retry_interval = 1.0  # Cap at 1 second
    health_check_attempts = 0
    
    while True:
        try:
            # Try to get a connection
            conn = pool_instance.getconn()
            
            # Health check if enabled
            if check_health and ENABLE_HEALTH_CHECK:
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
                        # Return connection to pool, marking it as bad so it gets closed
                        # This properly notifies the pool that this connection slot is free
                        pool_instance.putconn(conn, close=True)
                    except Exception as close_error:
                        logger.debug(f"Error returning stale connection to pool: {close_error}")
                    
                    # Check if we've exceeded max health check retries
                    if health_check_attempts >= HEALTH_CHECK_MAX_RETRIES:
                        raise ConnectionHealthCheckError(
                            f"Unable to get healthy connection after {HEALTH_CHECK_MAX_RETRIES} attempts. "
                            f"Database may be down or network issues present."
                        )
                    
                    # Don't count this as pool exhaustion, just retry immediately
                    continue
            else:
                # Health check disabled or not requested, return connection as-is
                elapsed = time.time() - start_time
                if elapsed > 1.0:
                    logger.info(f"Acquired connection after {elapsed:.2f}s")
                return conn
                
        except pool.PoolError as e:
            # Pool is exhausted, check timeout
            last_error = e
            elapsed = time.time() - start_time
            
            if elapsed >= timeout:
                logger.error(f"Connection pool exhausted after {elapsed:.2f}s (timeout: {timeout}s)")
                raise ConnectionPoolExhaustedError(
                    f"Connection pool exhausted - no connections available after {timeout}s. "
                    f"Consider increasing maxconn or investigating connection leaks."
                ) from e
            
            # Log warning if we've been waiting a while
            if elapsed > 5.0 and int(elapsed) % 5 == 0:
                logger.warning(f"Still waiting for connection... ({elapsed:.1f}s elapsed)")
            
            # Exponential backoff with cap
            time.sleep(retry_interval)
            retry_interval = min(retry_interval * 1.5, max_retry_interval)


def get_connection_pool() -> pool.ThreadedConnectionPool:
    """
    Get the global connection pool, initializing it if necessary.
    
    Thread-safe: Uses double-check locking to prevent race conditions.
    
    Returns:
        ThreadedConnectionPool instance
        
    Raises:
        RuntimeError: If pool initialization fails
    """
    global _connection_pool
    
    # Fast path: pool already exists (no lock needed)
    if _connection_pool is not None:
        return _connection_pool
    
    # Slow path: need to initialize (acquire lock)
    with _pool_lock:
        # Double-check: another thread might have initialized while we waited
        if _connection_pool is None:
            try:
                init_connection_pool()
            except Exception as e:
                raise RuntimeError(f"Failed to initialize connection pool: {e}")
    
    return _connection_pool


@contextmanager
def get_db_connection(timeout: Optional[float] = None, check_health: bool = True):
    """
    Context manager for getting a database connection from the pool.
    
    Automatically returns the connection to the pool when done.
    Does NOT commit or rollback - use get_db_transaction for that.
    
    Connections are automatically health-checked to detect stale/broken connections.
    
    Args:
        timeout: Maximum seconds to wait for a connection (default: 30s)
        check_health: If True, verify connection is healthy (default: True)
    
    Usage:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT ...")
                results = cur.fetchall()
            conn.commit()  # You must commit manually
    
    Yields:
        psycopg2 connection with RealDictCursor factory
        
    Raises:
        ConnectionPoolExhaustedError: If timeout is reached waiting for connection
        ConnectionHealthCheckError: If unable to get healthy connection after retries
    """
    pool_instance = get_connection_pool()
    timeout = timeout if timeout is not None else DEFAULT_CONNECTION_TIMEOUT
    conn = None
    
    try:
        conn = _get_connection_with_timeout(pool_instance, timeout, check_health=check_health)
        logger.debug("Connection acquired from pool")
        yield conn
    finally:
        if conn:
            # Clean up connection state before returning to pool
            try:
                # Rollback any uncommitted transaction to ensure clean state
                # This also clears SET LOCAL variables and drops temporary tables
                conn.rollback()
            except Exception as e:
                # Connection might be in a bad state, but still return it
                # Pool will handle broken connections on next getconn()
                logger.debug(f"Error during connection cleanup: {e}")
            
            # Return connection to pool
            pool_instance.putconn(conn)
            logger.debug("Connection returned to pool")


@contextmanager
def get_db_transaction(readonly: bool = False, timeout: Optional[float] = None, check_health: bool = True):
    """
    Context manager for a database transaction.
    
    Automatically commits on success, rolls back on exception.
    Always returns connection to pool.
    
    Connections are automatically health-checked to detect stale/broken connections.
    
    Args:
        readonly: If True, sets transaction to readonly mode
        timeout: Maximum seconds to wait for a connection (default: 30s)
        check_health: If True, verify connection is healthy (default: True)
    
    Usage:
        with get_db_transaction() as conn:
            with conn.cursor() as cur:
                cur.execute("INSERT INTO ...")
                # Auto-commits on success, auto-rollback on exception
    
    Yields:
        psycopg2 connection with RealDictCursor factory
        
    Raises:
        ConnectionPoolExhaustedError: If timeout is reached waiting for connection
        ConnectionHealthCheckError: If unable to get healthy connection after retries
    """
    pool_instance = get_connection_pool()
    timeout = timeout if timeout is not None else DEFAULT_CONNECTION_TIMEOUT
    conn = None
    
    try:
        conn = _get_connection_with_timeout(pool_instance, timeout, check_health=check_health)
        logger.debug("Connection acquired from pool for transaction")
        
        # If readonly, set transaction to read-only using SQL (not set_session which can't be used in a transaction)
        if readonly:
            with conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
        
        yield conn
        
        # Success - commit the transaction
        conn.commit()
        logger.debug("Transaction committed")
        
    except Exception as e:
        # Error - rollback the transaction
        if conn:
            conn.rollback()
            logger.debug(f"Transaction rolled back due to: {e}")
        raise
        
    finally:
        if conn:
            # Clean up connection state before returning to pool
            try:
                # Always ensure no transaction is pending (rollback is no-op if already committed)
                # This also clears SET LOCAL variables and drops temporary tables
                # Note: No need to reset readonly - it was set per-transaction, not per-connection
                conn.rollback()
            except Exception as e:
                # Connection might be in a bad state, but still return it
                # Pool will handle broken connections on next getconn()
                logger.debug(f"Error during connection cleanup: {e}")
            
            # Return connection to pool
            pool_instance.putconn(conn)
            logger.debug("Connection returned to pool")


@contextmanager
def get_db_cursor(readonly: bool = False, timeout: Optional[float] = None, check_health: bool = True):
    """
    Convenience context manager that provides a cursor with automatic transaction handling.
    
    This is the simplest way to execute queries - just use the cursor.
    Automatically commits on success, rolls back on exception.
    
    Connections are automatically health-checked to detect stale/broken connections.
    
    Args:
        readonly: If True, sets transaction to readonly mode
        timeout: Maximum seconds to wait for a connection (default: 30s)
        check_health: If True, verify connection is healthy (default: True)
    
    Usage:
        with get_db_cursor() as cur:
            cur.execute("INSERT INTO ...")
            # Auto-commits on success, auto-rollback on exception
        
        # For read-only queries (optimization)
        with get_db_cursor(readonly=True) as cur:
            cur.execute("SELECT ...")
            results = cur.fetchall()
        
        # With custom timeout
        with get_db_cursor(timeout=60) as cur:
            cur.execute("SELECT ...")
        
        # Skip health check for performance (not recommended)
        with get_db_cursor(check_health=False) as cur:
            cur.execute("SELECT ...")
    
    Yields:
        psycopg2 cursor (RealDictCursor)
        
    Raises:
        ConnectionPoolExhaustedError: If timeout is reached waiting for connection
        ConnectionHealthCheckError: If unable to get healthy connection after retries
    """
    with get_db_transaction(readonly=readonly, timeout=timeout, check_health=check_health) as conn:
        with conn.cursor() as cur:
            yield cur


def close_connection_pool():
    """
    Close all connections in the pool.
    
    Thread-safe: Uses lock to prevent closing while other threads are initializing.
    Should be called when shutting down the application.
    """
    global _connection_pool
    
    with _pool_lock:
        if _connection_pool:
            logger.info("Closing connection pool")
            _connection_pool.closeall()
            _connection_pool = None
            logger.info("Connection pool closed")


def get_pool_stats() -> dict:
    """
    Get current connection pool statistics.
    
    Returns:
        Dictionary with pool statistics (for monitoring/debugging)
    """
    pool_instance = get_connection_pool()
    
    # ThreadedConnectionPool doesn't expose stats directly,
    # but we can provide basic info
    return {
        "minconn": pool_instance.minconn,
        "maxconn": pool_instance.maxconn,
        "closed": pool_instance.closed,
    }


# Backward compatibility: simple connection getter (not recommended for new code)
def get_db_connection_simple():
    """
    Get a connection from the pool (without context manager).
    
    WARNING: You MUST manually return the connection with pool.putconn(conn)
    
    Use get_db_connection() or get_db_transaction() context managers instead!
    This function exists only for backward compatibility.
    
    Returns:
        psycopg2 connection
    """
    logger.warning("Using get_db_connection_simple() - consider using context managers instead")
    pool_instance = get_connection_pool()
    return pool_instance.getconn()

