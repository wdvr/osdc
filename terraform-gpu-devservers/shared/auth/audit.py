"""
Audit Logging Module

Logs all user actions and tracks Bedrock/Claude token usage for traceability.
Stores audit events in PostgreSQL for querying and compliance.

Usage:
    logger = AuditLogger(pool)
    await logger.log_action(
        user_id=123,
        action="reservation.create",
        resource_type="reservation",
        resource_id="abc-123",
        details={"gpu_type": "h100", "gpu_count": 4}
    )

    # Query user's actions
    events = await logger.get_user_history(user_id=123, limit=50)
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


class AuditEventType(str, Enum):
    """Types of auditable events."""

    # Authentication events
    AUTH_LOGIN = "auth.login"
    AUTH_LOGOUT = "auth.logout"
    AUTH_KEY_CREATE = "auth.key_create"
    AUTH_KEY_REVOKE = "auth.key_revoke"
    AUTH_FAILED = "auth.failed"

    # Reservation events
    RESERVATION_CREATE = "reservation.create"
    RESERVATION_CANCEL = "reservation.cancel"
    RESERVATION_EXTEND = "reservation.extend"
    RESERVATION_EXPIRE = "reservation.expire"
    RESERVATION_ADD_USER = "reservation.add_user"

    # Disk events
    DISK_CREATE = "disk.create"
    DISK_DELETE = "disk.delete"
    DISK_ATTACH = "disk.attach"
    DISK_DETACH = "disk.detach"
    DISK_RENAME = "disk.rename"

    # LLM/AI events
    LLM_REQUEST = "llm.request"
    LLM_RESPONSE = "llm.response"
    LLM_ERROR = "llm.error"

    # Admin events
    ADMIN_USER_DISABLE = "admin.user_disable"
    ADMIN_USER_ENABLE = "admin.user_enable"
    ADMIN_CONFIG_CHANGE = "admin.config_change"

    # System events
    SYSTEM_ERROR = "system.error"
    SYSTEM_WARNING = "system.warning"


@dataclass
class AuditEvent:
    """
    Represents an audit log event.

    Attributes:
        event_id: Unique event identifier (assigned by database)
        user_id: User who performed the action
        username: Username for display
        event_type: Type of event
        resource_type: Type of resource affected (reservation, disk, etc.)
        resource_id: ID of affected resource
        action: Human-readable action description
        details: Additional event details (JSON)
        ip_address: Client IP address
        user_agent: Client user agent
        created_at: Event timestamp
    """
    event_id: int | None
    user_id: int | None
    username: str | None
    event_type: AuditEventType
    resource_type: str | None
    resource_id: str | None
    action: str
    details: dict[str, Any] = field(default_factory=dict)
    ip_address: str | None = None
    user_agent: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "event_id": self.event_id,
            "user_id": self.user_id,
            "username": self.username,
            "event_type": self.event_type.value,
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "action": self.action,
            "details": self.details,
            "ip_address": self.ip_address,
            "user_agent": self.user_agent,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class TokenUsage:
    """
    Tracks LLM token usage for billing and monitoring.

    Attributes:
        usage_id: Unique usage record ID
        user_id: User who made the request
        model: LLM model name (e.g., "claude-3-opus")
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        total_tokens: Total tokens used
        cost_usd: Estimated cost in USD
        request_id: Associated request ID
        created_at: Usage timestamp
    """
    usage_id: int | None
    user_id: int
    model: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost_usd: float | None
    request_id: str | None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


async def log_user_action(
    conn: asyncpg.Connection,
    user_id: int | None,
    username: str | None,
    event_type: AuditEventType,
    action: str,
    resource_type: str | None = None,
    resource_id: str | None = None,
    details: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> int:
    """
    Log a user action to the audit log.

    Args:
        conn: Database connection
        user_id: User performing the action (None for system events)
        username: Username for display
        event_type: Type of event
        action: Human-readable action description
        resource_type: Type of resource (reservation, disk, etc.)
        resource_id: ID of affected resource
        details: Additional details to log
        ip_address: Client IP address
        user_agent: Client user agent

    Returns:
        Event ID of the created log entry
    """
    details_json = json.dumps(details or {})

    event_id = await conn.fetchval(
        """
        INSERT INTO audit_log (
            user_id, username, event_type, action,
            resource_type, resource_id, details,
            ip_address, user_agent
        ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8, $9)
        RETURNING event_id
        """,
        user_id,
        username,
        event_type.value,
        action,
        resource_type,
        resource_id,
        details_json,
        ip_address,
        user_agent,
    )

    logger.debug(
        f"Audit log: {event_type.value} by {username or 'system'} - {action}"
    )

    return event_id


async def log_token_usage(
    conn: asyncpg.Connection,
    user_id: int,
    model: str,
    input_tokens: int,
    output_tokens: int,
    request_id: str | None = None,
    cost_usd: float | None = None,
) -> int:
    """
    Log LLM token usage.

    Args:
        conn: Database connection
        user_id: User who made the request
        model: LLM model name
        input_tokens: Number of input tokens
        output_tokens: Number of output tokens
        request_id: Request identifier for correlation
        cost_usd: Estimated cost (optional)

    Returns:
        Usage record ID
    """
    total_tokens = input_tokens + output_tokens

    usage_id = await conn.fetchval(
        """
        INSERT INTO token_usage (
            user_id, model, input_tokens, output_tokens,
            total_tokens, cost_usd, request_id
        ) VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING usage_id
        """,
        user_id,
        model,
        input_tokens,
        output_tokens,
        total_tokens,
        cost_usd,
        request_id,
    )

    logger.debug(
        f"Token usage: user={user_id} model={model} "
        f"tokens={total_tokens} (in={input_tokens}, out={output_tokens})"
    )

    return usage_id


async def get_user_audit_log(
    conn: asyncpg.Connection,
    user_id: int,
    limit: int = 100,
    offset: int = 0,
    event_types: list[AuditEventType] | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[AuditEvent]:
    """
    Get audit log entries for a user.

    Args:
        conn: Database connection
        user_id: User ID to query
        limit: Maximum entries to return
        offset: Offset for pagination
        event_types: Filter by event types
        since: Only events after this time
        until: Only events before this time

    Returns:
        List of AuditEvent objects
    """
    conditions = ["user_id = $1"]
    params: list[Any] = [user_id]
    param_idx = 2

    if event_types:
        placeholders = ", ".join(f"${i}" for i in range(param_idx, param_idx + len(event_types)))
        conditions.append(f"event_type IN ({placeholders})")
        params.extend(et.value for et in event_types)
        param_idx += len(event_types)

    if since:
        conditions.append(f"created_at >= ${param_idx}")
        params.append(since)
        param_idx += 1

    if until:
        conditions.append(f"created_at <= ${param_idx}")
        params.append(until)
        param_idx += 1

    where_clause = " AND ".join(conditions)

    rows = await conn.fetch(
        f"""
        SELECT
            event_id, user_id, username, event_type, action,
            resource_type, resource_id, details,
            ip_address, user_agent, created_at
        FROM audit_log
        WHERE {where_clause}
        ORDER BY created_at DESC
        LIMIT ${param_idx} OFFSET ${param_idx + 1}
        """,
        *params,
        limit,
        offset,
    )

    return [
        AuditEvent(
            event_id=row['event_id'],
            user_id=row['user_id'],
            username=row['username'],
            event_type=AuditEventType(row['event_type']),
            resource_type=row['resource_type'],
            resource_id=row['resource_id'],
            action=row['action'],
            details=row['details'] or {},
            ip_address=row['ip_address'],
            user_agent=row['user_agent'],
            created_at=row['created_at'],
        )
        for row in rows
    ]


async def get_resource_audit_log(
    conn: asyncpg.Connection,
    resource_type: str,
    resource_id: str,
    limit: int = 100,
) -> list[AuditEvent]:
    """
    Get audit log entries for a specific resource.

    Args:
        conn: Database connection
        resource_type: Type of resource (reservation, disk, etc.)
        resource_id: Resource identifier
        limit: Maximum entries to return

    Returns:
        List of AuditEvent objects
    """
    rows = await conn.fetch(
        """
        SELECT
            event_id, user_id, username, event_type, action,
            resource_type, resource_id, details,
            ip_address, user_agent, created_at
        FROM audit_log
        WHERE resource_type = $1 AND resource_id = $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        resource_type,
        resource_id,
        limit,
    )

    return [
        AuditEvent(
            event_id=row['event_id'],
            user_id=row['user_id'],
            username=row['username'],
            event_type=AuditEventType(row['event_type']),
            resource_type=row['resource_type'],
            resource_id=row['resource_id'],
            action=row['action'],
            details=row['details'] or {},
            ip_address=row['ip_address'],
            user_agent=row['user_agent'],
            created_at=row['created_at'],
        )
        for row in rows
    ]


async def get_user_token_usage(
    conn: asyncpg.Connection,
    user_id: int,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    """
    Get token usage summary for a user.

    Args:
        conn: Database connection
        user_id: User ID
        since: Start of period
        until: End of period

    Returns:
        Usage summary with totals by model
    """
    conditions = ["user_id = $1"]
    params: list[Any] = [user_id]
    param_idx = 2

    if since:
        conditions.append(f"created_at >= ${param_idx}")
        params.append(since)
        param_idx += 1

    if until:
        conditions.append(f"created_at <= ${param_idx}")
        params.append(until)
        param_idx += 1

    where_clause = " AND ".join(conditions)

    # Get totals by model
    rows = await conn.fetch(
        f"""
        SELECT
            model,
            COUNT(*) as request_count,
            SUM(input_tokens) as total_input_tokens,
            SUM(output_tokens) as total_output_tokens,
            SUM(total_tokens) as total_tokens,
            SUM(COALESCE(cost_usd, 0)) as total_cost_usd
        FROM token_usage
        WHERE {where_clause}
        GROUP BY model
        ORDER BY total_tokens DESC
        """,
        *params,
    )

    by_model = {
        row['model']: {
            "request_count": row['request_count'],
            "input_tokens": row['total_input_tokens'],
            "output_tokens": row['total_output_tokens'],
            "total_tokens": row['total_tokens'],
            "cost_usd": float(row['total_cost_usd']) if row['total_cost_usd'] else 0.0,
        }
        for row in rows
    }

    # Calculate grand totals
    total_requests = sum(m['request_count'] for m in by_model.values())
    total_input = sum(m['input_tokens'] for m in by_model.values())
    total_output = sum(m['output_tokens'] for m in by_model.values())
    total_tokens = sum(m['total_tokens'] for m in by_model.values())
    total_cost = sum(m['cost_usd'] for m in by_model.values())

    return {
        "user_id": user_id,
        "period": {
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "totals": {
            "request_count": total_requests,
            "input_tokens": total_input,
            "output_tokens": total_output,
            "total_tokens": total_tokens,
            "cost_usd": total_cost,
        },
        "by_model": by_model,
    }


async def cleanup_old_audit_logs(
    conn: asyncpg.Connection,
    days_to_keep: int = 90,
) -> int:
    """
    Delete audit log entries older than specified days.

    Args:
        conn: Database connection
        days_to_keep: Number of days to retain

    Returns:
        Number of entries deleted
    """
    cutoff = datetime.now(UTC) - timedelta(days=days_to_keep)

    result = await conn.execute(
        """
        DELETE FROM audit_log
        WHERE created_at < $1
        """,
        cutoff,
    )

    deleted = int(result.split()[-1])

    if deleted > 0:
        logger.info(
            f"Cleaned up {deleted} audit log entries "
            f"older than {days_to_keep} days"
        )

    return deleted


class AuditLogger:
    """
    High-level audit logging interface.

    Provides convenient methods for logging various event types.

    Usage:
        audit = AuditLogger(pool)
        await audit.log_login(user_id, username, ip="192.168.1.1")
        await audit.log_reservation_create(user_id, username, reservation_id, details)
    """

    def __init__(self, pool: asyncpg.Pool):
        """
        Initialize audit logger.

        Args:
            pool: asyncpg connection pool
        """
        self._pool = pool

    async def log_action(
        self,
        event_type: AuditEventType,
        action: str,
        user_id: int | None = None,
        username: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> int:
        """Log a generic action."""
        async with self._pool.acquire() as conn:
            return await log_user_action(
                conn,
                user_id=user_id,
                username=username,
                event_type=event_type,
                action=action,
                resource_type=resource_type,
                resource_id=resource_id,
                details=details,
                ip_address=ip_address,
                user_agent=user_agent,
            )

    async def log_login(
        self,
        user_id: int,
        username: str,
        provider: str | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> int:
        """Log successful login."""
        return await self.log_action(
            AuditEventType.AUTH_LOGIN,
            f"User logged in via {provider or 'unknown'}",
            user_id=user_id,
            username=username,
            details={"provider": provider} if provider else None,
            ip_address=ip_address,
            user_agent=user_agent,
        )

    async def log_auth_failed(
        self,
        username: str | None = None,
        reason: str = "Unknown",
        ip_address: str | None = None,
    ) -> int:
        """Log failed authentication attempt."""
        return await self.log_action(
            AuditEventType.AUTH_FAILED,
            f"Authentication failed: {reason}",
            username=username,
            details={"reason": reason},
            ip_address=ip_address,
        )

    async def log_key_create(
        self,
        user_id: int,
        username: str,
        key_prefix: str,
        ttl_hours: int,
    ) -> int:
        """Log API key creation."""
        return await self.log_action(
            AuditEventType.AUTH_KEY_CREATE,
            f"Created API key {key_prefix}...",
            user_id=user_id,
            username=username,
            details={"key_prefix": key_prefix, "ttl_hours": ttl_hours},
        )

    async def log_reservation_create(
        self,
        user_id: int,
        username: str,
        reservation_id: str,
        details: dict[str, Any] | None = None,
    ) -> int:
        """Log reservation creation."""
        return await self.log_action(
            AuditEventType.RESERVATION_CREATE,
            "Created GPU reservation",
            user_id=user_id,
            username=username,
            resource_type="reservation",
            resource_id=reservation_id,
            details=details,
        )

    async def log_reservation_cancel(
        self,
        user_id: int,
        username: str,
        reservation_id: str,
        reason: str | None = None,
    ) -> int:
        """Log reservation cancellation."""
        return await self.log_action(
            AuditEventType.RESERVATION_CANCEL,
            f"Cancelled reservation{f': {reason}' if reason else ''}",
            user_id=user_id,
            username=username,
            resource_type="reservation",
            resource_id=reservation_id,
            details={"reason": reason} if reason else None,
        )

    async def log_token_usage(
        self,
        user_id: int,
        model: str,
        input_tokens: int,
        output_tokens: int,
        request_id: str | None = None,
        cost_usd: float | None = None,
    ) -> int:
        """Log LLM token usage."""
        async with self._pool.acquire() as conn:
            return await log_token_usage(
                conn,
                user_id=user_id,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                request_id=request_id,
                cost_usd=cost_usd,
            )

    async def get_user_history(
        self,
        user_id: int,
        limit: int = 100,
        offset: int = 0,
        event_types: list[AuditEventType] | None = None,
    ) -> list[AuditEvent]:
        """Get user's audit history."""
        async with self._pool.acquire() as conn:
            return await get_user_audit_log(
                conn,
                user_id=user_id,
                limit=limit,
                offset=offset,
                event_types=event_types,
            )

    async def get_resource_history(
        self,
        resource_type: str,
        resource_id: str,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """Get audit history for a resource."""
        async with self._pool.acquire() as conn:
            return await get_resource_audit_log(
                conn,
                resource_type=resource_type,
                resource_id=resource_id,
                limit=limit,
            )

    async def get_user_token_summary(
        self,
        user_id: int,
        days: int = 30,
    ) -> dict[str, Any]:
        """Get token usage summary for user."""
        since = datetime.now(UTC) - timedelta(days=days)
        async with self._pool.acquire() as conn:
            return await get_user_token_usage(conn, user_id, since=since)

    async def cleanup(self, days_to_keep: int = 90) -> int:
        """Clean up old audit logs."""
        async with self._pool.acquire() as conn:
            return await cleanup_old_audit_logs(conn, days_to_keep)
