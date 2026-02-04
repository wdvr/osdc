"""
OIDC Authentication Module

Provides cloud-agnostic authentication using OpenID Connect (OIDC) tokens
from multiple identity providers (GitHub, Google, Okta, etc.).

Components:
- oidc: JWT token verification with JWKS caching
- api_keys: API key management from OIDC identities
- audit: User action and token usage logging
"""

from .oidc import (
    OIDCVerifier,
    OIDCProvider,
    OIDCIdentity,
    OIDCVerificationError,
    JWKSFetchError,
    TokenExpiredError,
    InvalidIssuerError,
    InvalidAudienceError,
)

from .api_keys import (
    APIKeyManager,
    APIKeyInfo,
    create_api_key_from_oidc,
    validate_api_key,
    revoke_api_key,
    get_user_api_keys,
    cleanup_expired_keys,
)

from .audit import (
    AuditLogger,
    AuditEvent,
    AuditEventType,
    log_user_action,
    log_token_usage,
    get_user_audit_log,
    get_resource_audit_log,
)

__all__ = [
    # OIDC
    "OIDCVerifier",
    "OIDCProvider",
    "OIDCIdentity",
    "OIDCVerificationError",
    "JWKSFetchError",
    "TokenExpiredError",
    "InvalidIssuerError",
    "InvalidAudienceError",
    # API Keys
    "APIKeyManager",
    "APIKeyInfo",
    "create_api_key_from_oidc",
    "validate_api_key",
    "revoke_api_key",
    "get_user_api_keys",
    "cleanup_expired_keys",
    # Audit
    "AuditLogger",
    "AuditEvent",
    "AuditEventType",
    "log_user_action",
    "log_token_usage",
    "get_user_audit_log",
    "get_resource_audit_log",
]
