"""
Abstract base classes for cloud provider interfaces.

This module defines the abstract interfaces that all cloud providers must implement.
The abstraction allows the GPU reservation system to work with multiple cloud platforms
(AWS, GCP, custom on-prem) without modifying core business logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class VolumeInfo:
    """Standardized volume information across providers."""
    volume_id: str
    size_gb: int
    availability_zone: str
    status: str  # 'available', 'in-use', 'creating', 'deleting'
    tags: dict[str, str]


@dataclass
class SnapshotInfo:
    """Standardized snapshot information across providers."""
    snapshot_id: str
    volume_id: str
    status: str  # 'pending', 'completed', 'error'
    size_gb: int
    created_at: str  # ISO format timestamp
    tags: dict[str, str]


@dataclass
class NodeInfo:
    """Standardized node/instance information across providers."""
    node_id: str
    name: str
    instance_type: str
    availability_zone: str
    gpu_type: str | None
    gpu_count: int
    status: str  # 'running', 'stopped', 'terminated'
    labels: dict[str, str]


class CloudProvider(ABC):
    """
    Abstract base class for cloud provider implementations.

    This is the main interface for cloud-specific functionality.
    Each cloud implementation provides concrete implementations of
    storage, snapshot, compute, and object storage operations.

    Example:
        provider = get_cloud_provider()  # Returns AWSProvider or GCPProvider

        # Storage operations
        volume = provider.create_volume(size_gb=100, availability_zone='us-east-2a')

        # Snapshot operations
        snapshot = provider.create_snapshot(volume.volume_id)

        # Object storage
        uri = provider.upload_to_object_storage('bucket', 'key', b'content')
    """

    @abstractmethod
    def name(self) -> str:
        """Provider name (aws, gcp, custom)."""
        pass

    # === Block Storage ===

    @abstractmethod
    def create_volume(
        self,
        size_gb: int,
        availability_zone: str,
        volume_type: str = "ssd",
        tags: dict[str, str] | None = None,
        snapshot_id: str | None = None,
    ) -> VolumeInfo:
        """
        Create a block storage volume.

        Args:
            size_gb: Volume size in gigabytes
            availability_zone: Zone for volume placement
            volume_type: Storage class (ssd, hdd, io)
            tags: Key-value tags for the volume
            snapshot_id: Create volume from snapshot

        Returns:
            VolumeInfo with created volume details
        """
        pass

    @abstractmethod
    def delete_volume(self, volume_id: str) -> bool:
        """Delete a block storage volume."""
        pass

    @abstractmethod
    def attach_volume(
        self, volume_id: str, instance_id: str, device_path: str
    ) -> bool:
        """Attach volume to instance."""
        pass

    @abstractmethod
    def detach_volume(self, volume_id: str) -> bool:
        """Detach volume from instance."""
        pass

    @abstractmethod
    def get_volume(self, volume_id: str) -> VolumeInfo | None:
        """Get volume information."""
        pass

    @abstractmethod
    def list_volumes(
        self, filters: dict[str, str] | None = None
    ) -> list[VolumeInfo]:
        """List volumes matching filters (by tags)."""
        pass

    # === Snapshots ===

    @abstractmethod
    def create_snapshot(
        self,
        volume_id: str,
        description: str = "",
        tags: dict[str, str] | None = None,
    ) -> SnapshotInfo:
        """Create a snapshot of a volume."""
        pass

    @abstractmethod
    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        pass

    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> SnapshotInfo | None:
        """Get snapshot information."""
        pass

    @abstractmethod
    def list_snapshots(
        self,
        filters: dict[str, str] | None = None,
        volume_id: str | None = None,
        status: list[str] | None = None,
        use_pagination: bool = True,
    ) -> list[SnapshotInfo]:
        """
        List snapshots matching filters.

        Args:
            filters: Tag-based filters as key-value pairs
            volume_id: Filter by specific volume ID
            status: Filter by status (e.g., ["pending", "completed"])
            use_pagination: Whether to use pagination for large result sets
        """
        pass

    @abstractmethod
    def wait_for_snapshot(
        self, snapshot_id: str, timeout_seconds: int = 600
    ) -> bool:
        """Wait for snapshot to complete."""
        pass

    # === Compute ===

    @abstractmethod
    def get_nodes_by_gpu_type(self, gpu_type: str) -> list[NodeInfo]:
        """Get nodes/instances by GPU type."""
        pass

    @abstractmethod
    def get_node_availability(self) -> dict[str, dict[str, int]]:
        """Get GPU availability by type."""
        pass

    # === Object Storage ===

    @abstractmethod
    def upload_to_object_storage(
        self,
        bucket: str,
        key: str,
        content: bytes,
        metadata: dict[str, str] | None = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload content to object storage. Returns URI."""
        pass

    @abstractmethod
    def download_from_object_storage(
        self, bucket: str, key: str
    ) -> bytes | None:
        """Download content from object storage."""
        pass


class AuthProvider(ABC):
    """
    Abstract interface for identity verification.

    Used for authenticating API requests and verifying user identity.
    """

    @abstractmethod
    def verify_token(self, token: str) -> dict[str, Any] | None:
        """
        Verify an authentication token.

        Returns user info dict if valid, None if invalid.
        """
        pass

    @abstractmethod
    def get_user_info(self, token: str) -> dict[str, Any] | None:
        """Get user information from token."""
        pass

    @abstractmethod
    def create_api_key(
        self, user_id: str, scopes: list[str], ttl_hours: int = 24
    ) -> str:
        """Create an API key for a user."""
        pass


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        operation: str = "unknown",
        details: dict | None = None
    ):
        self.provider = provider
        self.operation = operation
        self.details = details or {}
        super().__init__(f"[{provider}] {operation}: {message}")


class VolumeNotFoundError(ProviderError):
    """Volume does not exist."""
    pass


class VolumeInUseError(ProviderError):
    """Volume is attached and cannot be modified."""
    pass


class SnapshotNotFoundError(ProviderError):
    """Snapshot does not exist."""
    pass


class QuotaExceededError(ProviderError):
    """Resource quota exceeded."""
    pass


class AuthenticationError(ProviderError):
    """Authentication failed."""
    pass


class AuthorizationError(ProviderError):
    """User not authorized for operation."""
    pass
