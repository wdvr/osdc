"""
API Key Management Module

Creates and validates API keys from OIDC identities.
Tracks key usage for auditing and supports key revocation.

Usage:
    # Create key from OIDC identity
    key_info = await create_api_key_from_oidc(identity, conn, ttl_hours=2)
    print(f"API Key: {key_info.key}")

    # Validate key
    user_info = await validate_api_key(api_key, conn)
"""

import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from .oidc import OIDCIdentity

logger = logging.getLogger(__name__)

# API key length (64 bytes = 86 base64 characters)
API_KEY_LENGTH = 64

# Default TTL for API keys
DEFAULT_TTL_HOURS = 2

# Key prefix length for identification
KEY_PREFIX_LENGTH = 8


@dataclass
class APIKeyInfo:
    """
    Information about a created API key.

    Attributes:
        key: The API key (only available at creation time)
        key_id: Database ID of the key
        key_prefix: First 8 characters for identification
        user_id: Owner user ID
        username: Owner username
        expires_at: Key expiration timestamp
        created_at: Key creation timestamp
    """
    key: str
    key_id: int
    key_prefix: str
    user_id: int
    username: str
    expires_at: datetime
    created_at: datetime

    def to_response(self) -> dict[str, Any]:
        """Convert to API response format."""
        return {
            "api_key": self.key,
            "key_prefix": self.key_prefix,
            "user_id": self.user_id,
            "username": self.username,
            "expires_at": self.expires_at.isoformat(),
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class UserInfo:
    """
    User information from API key validation.

    Attributes:
        user_id: Database user ID
        username: Username
        email: User email
        oidc_subject: OIDC subject identifier
        oidc_issuer: OIDC issuer URL
    """
    user_id: int
    username: str
    email: str | None
    oidc_subject: str | None
    oidc_issuer: str | None


def hash_api_key(api_key: str) -> str:
    """
    Hash API key for secure storage.

    Uses SHA-256 for consistent, irreversible hashing.
    """
    return hashlib.sha256(api_key.encode()).hexdigest()


def generate_api_key() -> tuple[str, str]:
    """
    Generate a new API key and its prefix.

    Returns:
        Tuple of (full_key, prefix)
    """
    key = secrets.token_urlsafe(API_KEY_LENGTH)
    prefix = key[:KEY_PREFIX_LENGTH]
    return key, prefix


async def get_or_create_user(
    conn: asyncpg.Connection,
    identity: OIDCIdentity,
) -> int:
    """
    Get existing user or create new one from OIDC identity.

    Links OIDC identity to user via oidc_subject and oidc_issuer columns.

    Args:
        conn: Database connection
        identity: Verified OIDC identity

    Returns:
        User ID
    """
    # Try to find user by OIDC identity
    existing = await conn.fetchrow(
        """
        SELECT user_id FROM api_users
        WHERE oidc_subject = $1 AND oidc_issuer = $2
        """,
        identity.subject,
        identity.issuer
    )

    if existing:
        logger.debug(
            f"Found existing user {existing['user_id']} for {identity.display_name}"
        )
        return existing['user_id']

    # Try to find by email (for linking existing accounts)
    if identity.email:
        existing = await conn.fetchrow(
            """
            SELECT user_id FROM api_users
            WHERE email = $1 AND oidc_subject IS NULL
            """,
            identity.email
        )

        if existing:
            # Link OIDC identity to existing user
            await conn.execute(
                """
                UPDATE api_users
                SET oidc_subject = $1, oidc_issuer = $2
                WHERE user_id = $3
                """,
                identity.subject,
                identity.issuer,
                existing['user_id']
            )
            logger.info(
                f"Linked OIDC identity to existing user {existing['user_id']}"
            )
            return existing['user_id']

    # Create new user
    username = identity.username or identity.email or identity.subject
    # Ensure username uniqueness by appending subject hash if needed
    base_username = username[:200]

    user_id = await conn.fetchval(
        """
        INSERT INTO api_users (username, email, oidc_subject, oidc_issuer)
        VALUES ($1, $2, $3, $4)
        ON CONFLICT (username) DO UPDATE
        SET username = EXCLUDED.username || '-' || substr(md5(EXCLUDED.oidc_subject), 1, 8)
        RETURNING user_id
        """,
        base_username,
        identity.email,
        identity.subject,
        identity.issuer
    )

    logger.info(f"Created new user {user_id} for {identity.display_name}")
    return user_id


async def create_api_key_from_oidc(
    identity: OIDCIdentity,
    conn: asyncpg.Connection,
    ttl_hours: int = DEFAULT_TTL_HOURS,
    description: str | None = None,
) -> APIKeyInfo:
    """
    Create a new API key from verified OIDC identity.

    Args:
        identity: Verified OIDC identity
        conn: Database connection
        ttl_hours: Key time-to-live in hours (default: 2)
        description: Optional key description

    Returns:
        APIKeyInfo with the new key (key only available here)
    """
    # Get or create user
    user_id = await get_or_create_user(conn, identity)

    # Generate key
    api_key, key_prefix = generate_api_key()
    key_hash = hash_api_key(api_key)

    # Calculate expiration
    now = datetime.now(UTC)
    expires_at = now + timedelta(hours=ttl_hours)

    # Build description
    if not description:
        description = f"OIDC key from {identity.provider_name}"

    # Store key
    key_id = await conn.fetchval(
        """
        INSERT INTO api_keys (
            user_id, key_hash, key_prefix, expires_at, description
        ) VALUES ($1, $2, $3, $4, $5)
        RETURNING key_id
        """,
        user_id,
        key_hash,
        key_prefix,
        expires_at,
        description
    )

    # Get username for response
    username = await conn.fetchval(
        "SELECT username FROM api_users WHERE user_id = $1",
        user_id
    )

    logger.info(
        f"Created API key {key_prefix}... for user {username} "
        f"(expires: {expires_at.isoformat()})"
    )

    return APIKeyInfo(
        key=api_key,
        key_id=key_id,
        key_prefix=key_prefix,
        user_id=user_id,
        username=username,
        expires_at=expires_at,
        created_at=now,
    )


async def validate_api_key(
    api_key: str,
    conn: asyncpg.Connection,
    update_last_used: bool = True,
) -> UserInfo:
    """
    Validate an API key and return user information.

    Args:
        api_key: The API key to validate
        conn: Database connection
        update_last_used: Whether to update last_used_at timestamp

    Returns:
        UserInfo for the key owner

    Raises:
        ValueError: If key is invalid, expired, or revoked
    """
    # Basic format validation
    if not api_key or len(api_key) < 16 or len(api_key) > 256:
        raise ValueError("Invalid API key format")

    key_hash = hash_api_key(api_key)

    # Look up key and user
    row = await conn.fetchrow(
        """
        SELECT
            u.user_id, u.username, u.email, u.is_active as user_active,
            u.oidc_subject, u.oidc_issuer,
            k.key_id, k.expires_at, k.is_active as key_active
        FROM api_keys k
        JOIN api_users u ON k.user_id = u.user_id
        WHERE k.key_hash = $1
        """,
        key_hash
    )

    if not row:
        raise ValueError("Invalid API key")

    # Check user status
    if not row['user_active']:
        raise ValueError("User account is disabled")

    # Check key status
    if not row['key_active']:
        raise ValueError("API key has been revoked")

    # Check expiration
    expires_at = row['expires_at']
    if expires_at:
        # Handle timezone-aware comparison
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        else:
            expires_at = expires_at.astimezone(UTC)

        if expires_at < datetime.now(UTC):
            raise ValueError("API key has expired")

    # Update last used timestamp
    if update_last_used:
        await conn.execute(
            """
            UPDATE api_keys
            SET last_used_at = CURRENT_TIMESTAMP
            WHERE key_id = $1
            """,
            row['key_id']
        )

    return UserInfo(
        user_id=row['user_id'],
        username=row['username'],
        email=row['email'],
        oidc_subject=row['oidc_subject'],
        oidc_issuer=row['oidc_issuer'],
    )


async def revoke_api_key(
    key_prefix: str,
    user_id: int,
    conn: asyncpg.Connection,
) -> bool:
    """
    Revoke an API key by prefix.

    Args:
        key_prefix: Key prefix to revoke
        user_id: Owner user ID (for authorization)
        conn: Database connection

    Returns:
        True if key was revoked, False if not found
    """
    result = await conn.execute(
        """
        UPDATE api_keys
        SET is_active = false
        WHERE key_prefix = $1 AND user_id = $2 AND is_active = true
        """,
        key_prefix,
        user_id
    )

    # asyncpg returns "UPDATE N" where N is affected rows
    affected = int(result.split()[-1])

    if affected > 0:
        logger.info(f"Revoked API key {key_prefix}... for user {user_id}")
        return True

    return False


async def revoke_all_user_keys(
    user_id: int,
    conn: asyncpg.Connection,
) -> int:
    """
    Revoke all API keys for a user.

    Args:
        user_id: User ID
        conn: Database connection

    Returns:
        Number of keys revoked
    """
    result = await conn.execute(
        """
        UPDATE api_keys
        SET is_active = false
        WHERE user_id = $1 AND is_active = true
        """,
        user_id
    )

    affected = int(result.split()[-1])

    if affected > 0:
        logger.info(f"Revoked {affected} API keys for user {user_id}")

    return affected


async def get_user_api_keys(
    user_id: int,
    conn: asyncpg.Connection,
    include_revoked: bool = False,
) -> list[dict[str, Any]]:
    """
    List API keys for a user.

    Args:
        user_id: User ID
        conn: Database connection
        include_revoked: Include revoked keys

    Returns:
        List of key info dicts (without actual key values)
    """
    where_clause = "WHERE user_id = $1"
    if not include_revoked:
        where_clause += " AND is_active = true"

    rows = await conn.fetch(
        f"""
        SELECT
            key_id, key_prefix, created_at, expires_at,
            last_used_at, is_active, description
        FROM api_keys
        {where_clause}
        ORDER BY created_at DESC
        """,
        user_id
    )

    return [
        {
            "key_id": row['key_id'],
            "key_prefix": row['key_prefix'],
            "created_at": row['created_at'].isoformat() if row['created_at'] else None,
            "expires_at": row['expires_at'].isoformat() if row['expires_at'] else None,
            "last_used_at": row['last_used_at'].isoformat() if row['last_used_at'] else None,
            "is_active": row['is_active'],
            "description": row['description'],
        }
        for row in rows
    ]


async def cleanup_expired_keys(
    conn: asyncpg.Connection,
    older_than_hours: int = 24,
) -> int:
    """
    Delete expired API keys older than specified hours.

    Args:
        conn: Database connection
        older_than_hours: Delete keys expired longer than this

    Returns:
        Number of keys deleted
    """
    cutoff = datetime.now(UTC) - timedelta(hours=older_than_hours)

    result = await conn.execute(
        """
        DELETE FROM api_keys
        WHERE expires_at < $1
        """,
        cutoff
    )

    deleted = int(result.split()[-1])

    if deleted > 0:
        logger.info(
            f"Cleaned up {deleted} expired API keys "
            f"(older than {older_than_hours} hours)"
        )

    return deleted


class APIKeyManager:
    """
    High-level API key management.

    Provides a convenient interface for key operations with connection pooling.

    Usage:
        manager = APIKeyManager(pool)
        key_info = await manager.create_from_oidc(identity)
        user_info = await manager.validate(api_key)
    """

    def __init__(self, pool: asyncpg.Pool, default_ttl_hours: int = DEFAULT_TTL_HOURS):
        """
        Initialize API key manager.

        Args:
            pool: asyncpg connection pool
            default_ttl_hours: Default TTL for new keys
        """
        self._pool = pool
        self._default_ttl = default_ttl_hours

    async def create_from_oidc(
        self,
        identity: OIDCIdentity,
        ttl_hours: int | None = None,
        description: str | None = None,
    ) -> APIKeyInfo:
        """Create API key from OIDC identity."""
        async with self._pool.acquire() as conn:
            return await create_api_key_from_oidc(
                identity,
                conn,
                ttl_hours=ttl_hours or self._default_ttl,
                description=description,
            )

    async def validate(
        self,
        api_key: str,
        update_last_used: bool = True,
    ) -> UserInfo:
        """Validate API key and return user info."""
        async with self._pool.acquire() as conn:
            return await validate_api_key(api_key, conn, update_last_used)

    async def revoke(self, key_prefix: str, user_id: int) -> bool:
        """Revoke an API key."""
        async with self._pool.acquire() as conn:
            return await revoke_api_key(key_prefix, user_id, conn)

    async def revoke_all(self, user_id: int) -> int:
        """Revoke all keys for a user."""
        async with self._pool.acquire() as conn:
            return await revoke_all_user_keys(user_id, conn)

    async def list_keys(
        self,
        user_id: int,
        include_revoked: bool = False,
    ) -> list[dict[str, Any]]:
        """List user's API keys."""
        async with self._pool.acquire() as conn:
            return await get_user_api_keys(user_id, conn, include_revoked)

    async def cleanup(self, older_than_hours: int = 24) -> int:
        """Clean up expired keys."""
        async with self._pool.acquire() as conn:
            return await cleanup_expired_keys(conn, older_than_hours)
