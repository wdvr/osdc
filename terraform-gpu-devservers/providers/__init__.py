"""
Cloud Provider Factory

Provides a pluggable interface for cloud-specific operations.

Environment Variables:
    CLOUD_PROVIDER: aws, gcp, or custom (default: aws)
    AWS_REGION: AWS region (for aws provider)
    GCP_PROJECT: GCP project ID (for gcp provider)
    GCP_ZONE: GCP zone (for gcp provider)

Usage:
    from providers import get_cloud_provider

    provider = get_cloud_provider()
    volume = provider.create_volume(size_gb=100, availability_zone="us-east-2a")
"""

import os
from typing import Optional

from .base import CloudProvider, AuthProvider, RegistryProvider

_cloud_provider_instance: Optional[CloudProvider] = None
_auth_provider_instance: Optional[AuthProvider] = None


def get_cloud_provider() -> CloudProvider:
    """Get the configured cloud provider instance (singleton)."""
    global _cloud_provider_instance

    if _cloud_provider_instance is not None:
        return _cloud_provider_instance

    provider_name = os.environ.get("CLOUD_PROVIDER", "aws").lower()

    if provider_name == "aws":
        from .aws import AWSProvider
        region = os.environ.get("AWS_REGION", "us-east-2")
        _cloud_provider_instance = AWSProvider(region=region)

    elif provider_name == "gcp":
        from .gcp import GCPProvider
        project = os.environ.get("GCP_PROJECT")
        zone = os.environ.get("GCP_ZONE", "us-central1-a")
        if not project:
            raise ValueError("GCP_PROJECT environment variable required for GCP provider")
        _cloud_provider_instance = GCPProvider(project=project, zone=zone)

    elif provider_name == "custom":
        from .custom import CustomProvider
        _cloud_provider_instance = CustomProvider()

    else:
        raise ValueError(f"Unknown cloud provider: {provider_name}. Supported: aws, gcp, custom")

    return _cloud_provider_instance


def get_auth_provider() -> AuthProvider:
    """Get the configured auth provider instance (singleton)."""
    global _auth_provider_instance

    if _auth_provider_instance is not None:
        return _auth_provider_instance

    # Auth provider can be configured separately
    auth_type = os.environ.get("AUTH_PROVIDER", "oidc").lower()

    if auth_type == "oidc":
        from shared.auth.oidc import OIDCAuthProvider
        _auth_provider_instance = OIDCAuthProvider()
    elif auth_type == "aws_iam":
        from .aws import AWSIAMAuthProvider
        _auth_provider_instance = AWSIAMAuthProvider()
    else:
        raise ValueError(f"Unknown auth provider: {auth_type}")

    return _auth_provider_instance


def reset_providers():
    """Reset provider instances (useful for testing)."""
    global _cloud_provider_instance, _auth_provider_instance
    _cloud_provider_instance = None
    _auth_provider_instance = None


__all__ = [
    "CloudProvider",
    "AuthProvider",
    "RegistryProvider",
    "get_cloud_provider",
    "get_auth_provider",
    "reset_providers",
]
