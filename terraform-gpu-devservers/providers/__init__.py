"""
Cloud Provider Factory

This module provides a factory function to get the appropriate cloud provider
based on configuration. The provider abstraction allows the GPU reservation
system to work with multiple cloud platforms without modifying core business logic.

Usage:
    from providers import get_cloud_provider

    provider = get_cloud_provider()

    # Storage operations
    volume = provider.create_volume(size_gb=100, availability_zone='us-east-2a')

    # Snapshot operations
    snapshot = provider.create_snapshot(volume.volume_id)

    # Object storage
    uri = provider.upload_to_object_storage('bucket', 'key', b'content')

Configuration:
    Set CLOUD_PROVIDER environment variable:
    - 'aws' (default): Amazon Web Services
    - 'gcp': Google Cloud Platform
    - 'custom': Custom/on-premises provider

    Provider-specific configuration via environment variables:
    - AWS: AWS_REGION, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY
    - GCP: GCP_PROJECT, GCP_ZONE, GOOGLE_APPLICATION_CREDENTIALS
    - Custom: CUSTOM_STORAGE_BACKEND, CUSTOM_AUTH_BACKEND
"""

import logging
import os
from typing import Optional

from .base import (
    AuthProvider,
    AuthenticationError,
    AuthorizationError,
    CloudProvider,
    NodeInfo,
    ProviderError,
    QuotaExceededError,
    SnapshotInfo,
    SnapshotNotFoundError,
    VolumeInfo,
    VolumeInUseError,
    VolumeNotFoundError,
)

logger = logging.getLogger(__name__)

# Cached provider instance
_provider_instance: Optional[CloudProvider] = None


def get_cloud_provider(
    provider_name: Optional[str] = None,
    force_new: bool = False,
    **kwargs
) -> CloudProvider:
    """
    Get the configured cloud provider instance.

    This factory function returns the appropriate provider based on configuration.
    The provider instance is cached for performance; use force_new=True to
    create a new instance.

    Args:
        provider_name: Override the provider (defaults to CLOUD_PROVIDER env var)
        force_new: Force creation of new instance (bypass cache)
        **kwargs: Provider-specific configuration options

    Returns:
        CloudProvider instance (AWSProvider, GCPProvider, or CustomProvider)

    Raises:
        ValueError: If provider name is not recognized

    Example:
        # Use default provider from environment
        provider = get_cloud_provider()

        # Override provider for testing
        provider = get_cloud_provider('custom')

        # Force new instance with custom config
        provider = get_cloud_provider('aws', force_new=True, region='us-west-2')
    """
    global _provider_instance

    # Use cached instance if available and not forcing new
    if _provider_instance is not None and not force_new and provider_name is None:
        return _provider_instance

    # Determine provider name
    name = provider_name or os.environ.get("CLOUD_PROVIDER", "aws")
    name = name.lower()

    logger.info(f"Initializing cloud provider: {name}")

    if name == "aws":
        from .aws import AWSProvider
        region = kwargs.get("region") or os.environ.get("AWS_REGION", "us-east-2")
        provider = AWSProvider(region=region)

    elif name == "gcp":
        from .gcp import GCPProvider
        project = kwargs.get("project") or os.environ.get("GCP_PROJECT", "")
        zone = kwargs.get("zone") or os.environ.get("GCP_ZONE", "us-central1-a")
        if not project:
            raise ValueError(
                "GCP_PROJECT environment variable must be set for GCP provider"
            )
        provider = GCPProvider(project=project, zone=zone)

    elif name == "custom":
        from .custom import CustomProvider
        provider = CustomProvider()

    else:
        raise ValueError(
            f"Unknown cloud provider: {name}. "
            f"Valid options: aws, gcp, custom"
        )

    # Cache the instance
    if not force_new:
        _provider_instance = provider

    return provider


def get_auth_provider(
    provider_name: Optional[str] = None,
    **kwargs
) -> AuthProvider:
    """
    Get an authentication provider instance.

    Args:
        provider_name: Override the provider (defaults to CLOUD_PROVIDER env var)
        **kwargs: Provider-specific configuration options

    Returns:
        AuthProvider instance
    """
    name = provider_name or os.environ.get("CLOUD_PROVIDER", "aws")
    name = name.lower()

    if name == "aws":
        from .aws import AWSIAMAuthProvider
        region = kwargs.get("region") or os.environ.get("AWS_REGION", "us-east-2")
        return AWSIAMAuthProvider(region=region)

    elif name == "gcp":
        raise NotImplementedError("GCP auth provider not implemented")

    elif name == "custom":
        from .custom import CustomAuthProvider
        return CustomAuthProvider()

    else:
        raise ValueError(f"Unknown auth provider: {name}")


def clear_provider_cache():
    """Clear the cached provider instance."""
    global _provider_instance
    _provider_instance = None


__all__ = [
    # Factory functions
    "get_cloud_provider",
    "get_auth_provider",
    "clear_provider_cache",
    # Base classes
    "CloudProvider",
    "AuthProvider",
    # Data classes
    "VolumeInfo",
    "SnapshotInfo",
    "NodeInfo",
    # Exceptions
    "ProviderError",
    "VolumeNotFoundError",
    "VolumeInUseError",
    "SnapshotNotFoundError",
    "QuotaExceededError",
    "AuthenticationError",
    "AuthorizationError",
]
