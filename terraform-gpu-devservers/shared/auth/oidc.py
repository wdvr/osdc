"""
OIDC Token Verification Module

Verifies JWT tokens from multiple OIDC providers (GitHub, Google, Okta).
Implements JWKS caching for performance and supports multiple issuers.

Usage:
    verifier = OIDCVerifier()
    verifier.add_provider(OIDCProvider(
        name="github",
        issuer="https://token.actions.githubusercontent.com",
        audience="gpu-dev-api"
    ))

    identity = await verifier.verify_token(token)
    print(f"Authenticated: {identity.subject} from {identity.issuer}")
"""

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import httpx
import jwt
from jwt import PyJWKClient, PyJWKClientError

logger = logging.getLogger(__name__)

# JWKS cache TTL (5 minutes)
JWKS_CACHE_TTL_SECONDS = 300

# Token clock skew tolerance (30 seconds)
CLOCK_SKEW_SECONDS = 30


class OIDCVerificationError(Exception):
    """Base exception for OIDC verification errors."""
    pass


class JWKSFetchError(OIDCVerificationError):
    """Failed to fetch JWKS from issuer."""
    pass


class TokenExpiredError(OIDCVerificationError):
    """Token has expired."""
    pass


class InvalidIssuerError(OIDCVerificationError):
    """Token issuer is not trusted."""
    pass


class InvalidAudienceError(OIDCVerificationError):
    """Token audience does not match expected value."""
    pass


class InvalidSignatureError(OIDCVerificationError):
    """Token signature verification failed."""
    pass


class InvalidClaimsError(OIDCVerificationError):
    """Token claims are invalid or missing."""
    pass


@dataclass
class OIDCProvider:
    """
    Configuration for an OIDC identity provider.

    Attributes:
        name: Human-readable provider name (e.g., "github", "google")
        issuer: Token issuer URL (must match `iss` claim exactly)
        audience: Expected audience (`aud` claim), can be string or list
        jwks_uri: Optional custom JWKS URI (auto-discovered if not set)
        additional_claims: Extra claims to extract from tokens
    """
    name: str
    issuer: str
    audience: str | list[str]
    jwks_uri: str | None = None
    additional_claims: list[str] = field(default_factory=list)

    def __post_init__(self):
        # Normalize issuer URL (remove trailing slash)
        self.issuer = self.issuer.rstrip("/")


@dataclass
class OIDCIdentity:
    """
    Verified identity extracted from an OIDC token.

    Attributes:
        subject: Unique user identifier (`sub` claim)
        issuer: Token issuer (`iss` claim)
        email: User email if available
        username: Username or preferred_username claim
        groups: Group memberships if available
        provider_name: Name of the OIDC provider that issued this token
        claims: All verified claims from the token
        verified_at: When the token was verified
    """
    subject: str
    issuer: str
    email: str | None
    username: str | None
    groups: list[str]
    provider_name: str
    claims: dict[str, Any]
    verified_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def display_name(self) -> str:
        """Human-readable identifier for logging."""
        return self.email or self.username or self.subject


@dataclass
class CachedJWKS:
    """Cached JWKS with expiration."""
    client: PyJWKClient
    fetched_at: float

    def is_expired(self) -> bool:
        return time.time() - self.fetched_at > JWKS_CACHE_TTL_SECONDS


class OIDCVerifier:
    """
    Verifies OIDC tokens from multiple identity providers.

    Thread-safe and supports concurrent token verification.
    Caches JWKS for performance.

    Usage:
        verifier = OIDCVerifier()
        verifier.add_provider(OIDCProvider(...))
        identity = await verifier.verify_token(token)
    """

    def __init__(self):
        self._providers: dict[str, OIDCProvider] = {}
        self._jwks_cache: dict[str, CachedJWKS] = {}
        self._lock = asyncio.Lock()
        self._http_client: httpx.AsyncClient | None = None

    def add_provider(self, provider: OIDCProvider) -> None:
        """
        Register an OIDC provider.

        Args:
            provider: Provider configuration
        """
        self._providers[provider.issuer] = provider
        logger.info(f"Registered OIDC provider: {provider.name} ({provider.issuer})")

    def remove_provider(self, issuer: str) -> bool:
        """
        Remove an OIDC provider.

        Args:
            issuer: Provider issuer URL

        Returns:
            True if provider was removed, False if not found
        """
        if issuer in self._providers:
            del self._providers[issuer]
            if issuer in self._jwks_cache:
                del self._jwks_cache[issuer]
            return True
        return False

    @property
    def providers(self) -> list[OIDCProvider]:
        """List all registered providers."""
        return list(self._providers.values())

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client for JWKS fetching."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=10.0,
                follow_redirects=True,
                headers={"User-Agent": "gpu-dev-oidc-verifier/1.0"}
            )
        return self._http_client

    async def _discover_jwks_uri(self, issuer: str) -> str:
        """
        Discover JWKS URI from OIDC discovery document.

        Args:
            issuer: OIDC issuer URL

        Returns:
            JWKS URI

        Raises:
            JWKSFetchError: If discovery fails
        """
        discovery_url = f"{issuer}/.well-known/openid-configuration"

        try:
            client = await self._get_http_client()
            response = await client.get(discovery_url)
            response.raise_for_status()

            config = response.json()
            jwks_uri = config.get("jwks_uri")

            if not jwks_uri:
                raise JWKSFetchError(
                    f"No jwks_uri in discovery document for {issuer}"
                )

            logger.debug(f"Discovered JWKS URI for {issuer}: {jwks_uri}")
            return jwks_uri

        except httpx.HTTPError as e:
            raise JWKSFetchError(
                f"Failed to fetch OIDC discovery document from {issuer}: {e}"
            ) from e

    async def _get_jwks_client(self, provider: OIDCProvider) -> PyJWKClient:
        """
        Get JWKS client for provider, using cache if available.

        Args:
            provider: OIDC provider configuration

        Returns:
            PyJWKClient for the provider
        """
        async with self._lock:
            cached = self._jwks_cache.get(provider.issuer)

            if cached and not cached.is_expired():
                return cached.client

            # Discover or use configured JWKS URI
            jwks_uri = provider.jwks_uri
            if not jwks_uri:
                jwks_uri = await self._discover_jwks_uri(provider.issuer)

            # Create new JWKS client
            try:
                client = PyJWKClient(
                    jwks_uri,
                    cache_jwk_set=True,
                    lifespan=JWKS_CACHE_TTL_SECONDS
                )

                self._jwks_cache[provider.issuer] = CachedJWKS(
                    client=client,
                    fetched_at=time.time()
                )

                logger.debug(f"Created JWKS client for {provider.issuer}")
                return client

            except Exception as e:
                raise JWKSFetchError(
                    f"Failed to create JWKS client for {provider.issuer}: {e}"
                ) from e

    def _find_provider_for_token(self, token: str) -> OIDCProvider:
        """
        Find the appropriate provider for a token by decoding without verification.

        Args:
            token: JWT token

        Returns:
            Matching OIDCProvider

        Raises:
            InvalidIssuerError: If no matching provider found
        """
        try:
            # Decode without verification to get issuer
            unverified = jwt.decode(
                token,
                options={"verify_signature": False},
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]
            )

            issuer = unverified.get("iss", "").rstrip("/")

            if issuer in self._providers:
                return self._providers[issuer]

            raise InvalidIssuerError(
                f"No trusted provider for issuer: {issuer}. "
                f"Trusted issuers: {list(self._providers.keys())}"
            )

        except jwt.DecodeError as e:
            raise OIDCVerificationError(f"Invalid token format: {e}") from e

    def _validate_audience(
        self,
        token_audience: str | list[str],
        expected: str | list[str]
    ) -> bool:
        """
        Validate token audience against expected values.

        Args:
            token_audience: Audience from token (aud claim)
            expected: Expected audience value(s)

        Returns:
            True if audience is valid
        """
        # Normalize to lists
        if isinstance(token_audience, str):
            token_audiences = [token_audience]
        else:
            token_audiences = token_audience

        if isinstance(expected, str):
            expected_audiences = [expected]
        else:
            expected_audiences = expected

        # Check if any token audience matches any expected audience
        return bool(set(token_audiences) & set(expected_audiences))

    async def verify_token(self, token: str) -> OIDCIdentity:
        """
        Verify an OIDC token and extract identity.

        Args:
            token: JWT token string

        Returns:
            Verified OIDCIdentity

        Raises:
            OIDCVerificationError: If verification fails
            InvalidIssuerError: If issuer is not trusted
            TokenExpiredError: If token has expired
            InvalidAudienceError: If audience doesn't match
            InvalidSignatureError: If signature is invalid
        """
        # Find provider for this token
        provider = self._find_provider_for_token(token)

        # Get JWKS client
        jwks_client = await self._get_jwks_client(provider)

        try:
            # Get signing key from JWKS
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            # Verify and decode token
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"],
                issuer=provider.issuer,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iat": True,
                    "verify_iss": True,
                    "require": ["sub", "iss", "exp", "iat"],
                },
                leeway=CLOCK_SKEW_SECONDS
            )

            # Validate audience manually for more control
            token_audience = claims.get("aud")
            if token_audience and not self._validate_audience(
                token_audience, provider.audience
            ):
                raise InvalidAudienceError(
                    f"Token audience {token_audience} does not match "
                    f"expected {provider.audience}"
                )

            # Extract identity claims
            identity = self._extract_identity(claims, provider)

            logger.info(
                f"Verified OIDC token for {identity.display_name} "
                f"from {provider.name}"
            )

            return identity

        except jwt.ExpiredSignatureError as e:
            raise TokenExpiredError("Token has expired") from e
        except jwt.InvalidIssuerError as e:
            raise InvalidIssuerError(f"Invalid issuer: {e}") from e
        except jwt.InvalidSignatureError as e:
            raise InvalidSignatureError(f"Invalid signature: {e}") from e
        except jwt.InvalidAudienceError as e:
            raise InvalidAudienceError(f"Invalid audience: {e}") from e
        except PyJWKClientError as e:
            raise JWKSFetchError(f"Failed to get signing key: {e}") from e
        except jwt.PyJWTError as e:
            raise OIDCVerificationError(f"Token verification failed: {e}") from e

    def _extract_identity(
        self,
        claims: dict[str, Any],
        provider: OIDCProvider
    ) -> OIDCIdentity:
        """
        Extract OIDCIdentity from verified claims.

        Handles provider-specific claim names.
        """
        subject = claims["sub"]
        issuer = claims["iss"]

        # Extract email (various claim names)
        email = (
            claims.get("email") or
            claims.get("preferred_username") or
            claims.get("upn")  # Azure AD
        )

        # Extract username (various claim names)
        username = (
            claims.get("preferred_username") or
            claims.get("name") or
            claims.get("nickname") or  # GitHub
            claims.get("login") or  # GitHub
            claims.get("sub")  # Fallback to subject
        )

        # Extract groups (various claim names)
        groups = []
        for claim_name in ["groups", "roles", "cognito:groups", "custom:groups"]:
            if claim_name in claims:
                group_value = claims[claim_name]
                if isinstance(group_value, list):
                    groups.extend(group_value)
                elif isinstance(group_value, str):
                    groups.append(group_value)

        # Extract additional provider-specific claims
        extracted_claims = dict(claims)
        for claim_name in provider.additional_claims:
            if claim_name in claims:
                extracted_claims[claim_name] = claims[claim_name]

        return OIDCIdentity(
            subject=subject,
            issuer=issuer,
            email=email if email and "@" in str(email) else None,
            username=username,
            groups=groups,
            provider_name=provider.name,
            claims=extracted_claims
        )

    async def close(self) -> None:
        """Close HTTP client and cleanup resources."""
        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None
        self._jwks_cache.clear()


# Pre-configured providers for common OIDC issuers
def create_github_provider(
    audience: str = "gpu-dev-api",
    organization: str | None = None
) -> OIDCProvider:
    """
    Create GitHub Actions OIDC provider configuration.

    Args:
        audience: Expected audience (matches `aud` claim)
        organization: Optional GitHub org to restrict access

    Returns:
        Configured OIDCProvider for GitHub Actions
    """
    return OIDCProvider(
        name="github",
        issuer="https://token.actions.githubusercontent.com",
        audience=audience,
        additional_claims=[
            "repository",
            "repository_owner",
            "actor",
            "workflow",
            "ref"
        ]
    )


def create_google_provider(
    client_id: str,
    hd: str | None = None
) -> OIDCProvider:
    """
    Create Google OIDC provider configuration.

    Args:
        client_id: Google OAuth client ID (becomes audience)
        hd: Optional hosted domain restriction

    Returns:
        Configured OIDCProvider for Google
    """
    return OIDCProvider(
        name="google",
        issuer="https://accounts.google.com",
        audience=client_id,
        additional_claims=["hd", "picture"] if hd else ["picture"]
    )


def create_okta_provider(
    issuer: str,
    audience: str
) -> OIDCProvider:
    """
    Create Okta OIDC provider configuration.

    Args:
        issuer: Okta issuer URL (e.g., https://your-domain.okta.com)
        audience: Expected audience

    Returns:
        Configured OIDCProvider for Okta
    """
    return OIDCProvider(
        name="okta",
        issuer=issuer,
        audience=audience,
        additional_claims=["groups", "preferred_username"]
    )


def create_azure_ad_provider(
    tenant_id: str,
    client_id: str
) -> OIDCProvider:
    """
    Create Azure AD OIDC provider configuration.

    Args:
        tenant_id: Azure AD tenant ID
        client_id: Application (client) ID

    Returns:
        Configured OIDCProvider for Azure AD
    """
    return OIDCProvider(
        name="azure_ad",
        issuer=f"https://login.microsoftonline.com/{tenant_id}/v2.0",
        audience=client_id,
        additional_claims=["groups", "roles", "upn", "tid"]
    )
