"""
Abstract base classes for cloud provider interfaces.

This module defines the abstract interfaces that all cloud providers must implement.
The abstraction allows the GPU reservation system to work with multiple cloud platforms
(AWS, GCP, custom on-prem) without modifying core business logic.

Provider Categories:
- StorageProvider: Block storage (EBS, GCE Persistent Disk)
- SnapshotProvider: Storage snapshots for backup/restore
- LoadBalancerProvider: ALB/NLB routing for Jupyter/SSH access
- DNSProvider: DNS record management (Route53, Cloud DNS)
- AuthProvider: Identity verification (AWS IAM, GCP IAM, OIDC)
- RegistryProvider: Container registry integration
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class VolumeInfo:
    """Standardized volume information across providers."""
    volume_id: str
    size_gb: int
    state: str  # 'available', 'in-use', 'creating', 'deleting'
    availability_zone: str
    created_at: datetime
    is_attached: bool
    attached_instance: str | None
    disk_name: str | None
    user_id: str | None
    reservation_id: str | None
    tags: dict[str, str]


@dataclass
class SnapshotInfo:
    """Standardized snapshot information across providers."""
    snapshot_id: str
    volume_id: str
    state: str  # 'pending', 'completed', 'error'
    start_time: datetime
    size_gb: int
    description: str
    user_id: str | None
    disk_name: str | None
    tags: dict[str, str]


@dataclass
class TargetGroupInfo:
    """Load balancer target group information."""
    target_group_id: str
    name: str
    port: int
    protocol: str
    vpc_id: str | None


@dataclass
class ListenerRuleInfo:
    """Load balancer listener rule information."""
    rule_id: str
    target_group_id: str
    priority: int
    conditions: dict[str, Any]


class StorageProvider(ABC):
    """
    Abstract interface for block storage operations.

    Implementations:
    - AWS: EBS volumes
    - GCP: Persistent Disks
    - Custom: iSCSI, NFS-backed storage

    Example usage:
        provider = get_cloud_provider().storage
        volume = provider.create_volume(size_gb=100, zone='us-east-1a')
        provider.attach_volume(volume.volume_id, 'i-12345')
    """

    @abstractmethod
    def create_volume(
        self,
        size_gb: int,
        availability_zone: str,
        volume_type: str = 'gp3',
        tags: dict[str, str] | None = None,
        iops: int | None = None,
        throughput: int | None = None
    ) -> VolumeInfo:
        """
        Create a new block storage volume.

        Args:
            size_gb: Volume size in gigabytes
            availability_zone: Zone for volume placement
            volume_type: Storage class (gp3, io2, pd-ssd, etc.)
            tags: Key-value tags for the volume
            iops: Provisioned IOPS (if supported)
            throughput: Provisioned throughput MB/s (if supported)

        Returns:
            VolumeInfo with created volume details

        Raises:
            ProviderError: If volume creation fails
        """
        pass

    @abstractmethod
    def delete_volume(self, volume_id: str) -> bool:
        """
        Delete a block storage volume.

        Args:
            volume_id: Provider-specific volume identifier

        Returns:
            True if deleted successfully

        Raises:
            ProviderError: If volume is attached or deletion fails
        """
        pass

    @abstractmethod
    def get_volume(self, volume_id: str) -> VolumeInfo | None:
        """
        Get volume details by ID.

        Args:
            volume_id: Provider-specific volume identifier

        Returns:
            VolumeInfo if found, None otherwise
        """
        pass

    @abstractmethod
    def list_volumes(
        self,
        filters: dict[str, str] | None = None,
        tags: dict[str, str] | None = None
    ) -> list[VolumeInfo]:
        """
        List volumes matching filters.

        Args:
            filters: Provider-specific filters (state, zone, etc.)
            tags: Filter by tag key-value pairs

        Returns:
            List of matching VolumeInfo objects
        """
        pass

    @abstractmethod
    def attach_volume(
        self,
        volume_id: str,
        instance_id: str,
        device: str = '/dev/xvdf'
    ) -> bool:
        """
        Attach volume to a compute instance.

        Args:
            volume_id: Volume to attach
            instance_id: Target instance
            device: Device name/path

        Returns:
            True if attached successfully
        """
        pass

    @abstractmethod
    def detach_volume(self, volume_id: str, force: bool = False) -> bool:
        """
        Detach volume from instance.

        Args:
            volume_id: Volume to detach
            force: Force detach even if in use

        Returns:
            True if detached successfully
        """
        pass

    @abstractmethod
    def update_volume_tags(
        self,
        volume_id: str,
        tags: dict[str, str],
        remove_tags: list[str] | None = None
    ) -> bool:
        """
        Update volume tags.

        Args:
            volume_id: Volume to update
            tags: Tags to add/update
            remove_tags: Tag keys to remove

        Returns:
            True if updated successfully
        """
        pass


class SnapshotProvider(ABC):
    """
    Abstract interface for storage snapshot operations.

    Implementations:
    - AWS: EBS Snapshots
    - GCP: Disk Snapshots
    - Custom: ZFS snapshots, LVM snapshots
    """

    @abstractmethod
    def create_snapshot(
        self,
        volume_id: str,
        description: str = '',
        tags: dict[str, str] | None = None
    ) -> SnapshotInfo:
        """
        Create a snapshot of a volume.

        Args:
            volume_id: Source volume ID
            description: Human-readable description
            tags: Key-value tags

        Returns:
            SnapshotInfo with snapshot details
        """
        pass

    @abstractmethod
    def delete_snapshot(self, snapshot_id: str) -> bool:
        """
        Delete a snapshot.

        Args:
            snapshot_id: Snapshot to delete

        Returns:
            True if deleted successfully
        """
        pass

    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> SnapshotInfo | None:
        """
        Get snapshot details by ID.

        Args:
            snapshot_id: Snapshot identifier

        Returns:
            SnapshotInfo if found, None otherwise
        """
        pass

    @abstractmethod
    def list_snapshots(
        self,
        volume_id: str | None = None,
        user_id: str | None = None,
        status: str | None = None,
        tags: dict[str, str] | None = None
    ) -> list[SnapshotInfo]:
        """
        List snapshots matching criteria.

        Args:
            volume_id: Filter by source volume
            user_id: Filter by user tag
            status: Filter by status (pending, completed)
            tags: Filter by tag key-value pairs

        Returns:
            List of matching SnapshotInfo objects
        """
        pass

    @abstractmethod
    def restore_volume_from_snapshot(
        self,
        snapshot_id: str,
        availability_zone: str,
        volume_type: str = 'gp3',
        tags: dict[str, str] | None = None
    ) -> VolumeInfo:
        """
        Create a new volume from a snapshot.

        Args:
            snapshot_id: Source snapshot
            availability_zone: Zone for new volume
            volume_type: Storage class for new volume
            tags: Tags for new volume

        Returns:
            VolumeInfo with new volume details
        """
        pass

    @abstractmethod
    def get_snapshot_count(self, volume_id: str) -> int:
        """
        Get count of completed snapshots for a volume.

        Args:
            volume_id: Volume ID

        Returns:
            Number of completed snapshots
        """
        pass

    @abstractmethod
    def has_pending_snapshot(self, volume_id: str) -> bool:
        """
        Check if volume has any pending snapshots.

        Args:
            volume_id: Volume ID

        Returns:
            True if any snapshot is pending
        """
        pass


class LoadBalancerProvider(ABC):
    """
    Abstract interface for load balancer operations.

    Implementations:
    - AWS: ALB/NLB
    - GCP: Cloud Load Balancer
    - Custom: HAProxy, nginx, Traefik
    """

    @abstractmethod
    def create_target_group(
        self,
        name: str,
        port: int,
        protocol: str = 'HTTP',
        health_check_path: str = '/',
        tags: dict[str, str] | None = None
    ) -> TargetGroupInfo:
        """
        Create a target group for routing.

        Args:
            name: Target group name
            port: Target port
            protocol: HTTP, HTTPS, TCP
            health_check_path: Path for health checks
            tags: Key-value tags

        Returns:
            TargetGroupInfo with target group details
        """
        pass

    @abstractmethod
    def delete_target_group(self, target_group_id: str) -> bool:
        """
        Delete a target group.

        Args:
            target_group_id: Target group to delete

        Returns:
            True if deleted successfully
        """
        pass

    @abstractmethod
    def register_target(
        self,
        target_group_id: str,
        instance_id: str,
        port: int
    ) -> bool:
        """
        Register an instance with a target group.

        Args:
            target_group_id: Target group
            instance_id: Instance to register
            port: Port on the instance

        Returns:
            True if registered successfully
        """
        pass

    @abstractmethod
    def deregister_target(
        self,
        target_group_id: str,
        instance_id: str
    ) -> bool:
        """
        Remove instance from target group.

        Args:
            target_group_id: Target group
            instance_id: Instance to remove

        Returns:
            True if deregistered successfully
        """
        pass

    @abstractmethod
    def create_listener_rule(
        self,
        listener_id: str,
        target_group_id: str,
        hostname: str,
        priority: int | None = None
    ) -> ListenerRuleInfo:
        """
        Create routing rule based on hostname.

        Args:
            listener_id: Listener to add rule to
            target_group_id: Target group for matching requests
            hostname: Hostname to match (e.g., 'dev.example.com')
            priority: Rule priority (auto-generated if None)

        Returns:
            ListenerRuleInfo with rule details
        """
        pass

    @abstractmethod
    def delete_listener_rule(self, rule_id: str) -> bool:
        """
        Delete a listener rule.

        Args:
            rule_id: Rule to delete

        Returns:
            True if deleted successfully
        """
        pass


class DNSProvider(ABC):
    """
    Abstract interface for DNS record management.

    Implementations:
    - AWS: Route53
    - GCP: Cloud DNS
    - Custom: PowerDNS, BIND, Cloudflare
    """

    @abstractmethod
    def create_record(
        self,
        name: str,
        record_type: str,
        value: str,
        ttl: int = 60
    ) -> bool:
        """
        Create a DNS record.

        Args:
            name: Full domain name (e.g., 'dev.example.com')
            record_type: A, AAAA, CNAME, TXT, etc.
            value: Record value (IP, hostname, text)
            ttl: Time to live in seconds

        Returns:
            True if created successfully
        """
        pass

    @abstractmethod
    def delete_record(
        self,
        name: str,
        record_type: str,
        value: str
    ) -> bool:
        """
        Delete a DNS record.

        Args:
            name: Full domain name
            record_type: Record type to delete
            value: Record value (needed for exact match)

        Returns:
            True if deleted successfully
        """
        pass

    @abstractmethod
    def list_records(
        self,
        domain: str,
        record_type: str | None = None
    ) -> list[dict[str, Any]]:
        """
        List DNS records for a domain.

        Args:
            domain: Domain to list records for
            record_type: Filter by record type

        Returns:
            List of record dictionaries
        """
        pass

    @abstractmethod
    def get_hosted_zone_id(self, domain: str) -> str | None:
        """
        Get hosted zone ID for a domain.

        Args:
            domain: Domain name

        Returns:
            Zone ID if found, None otherwise
        """
        pass


class AuthProvider(ABC):
    """
    Abstract interface for identity verification.

    Used for authenticating API requests and verifying user identity
    against corporate identity providers.

    Implementations:
    - AWS: IAM STS, Cognito
    - GCP: IAM, Identity Platform
    - Custom: OIDC, LDAP, SAML
    """

    @abstractmethod
    def verify_identity(self, token: str) -> dict[str, Any] | None:
        """
        Verify an authentication token.

        Args:
            token: Bearer token, API key, or other credential

        Returns:
            Dictionary with user info if valid:
            {
                'user_id': str,
                'email': str,
                'groups': list[str],
                'expires_at': datetime
            }
            None if invalid
        """
        pass

    @abstractmethod
    def get_caller_identity(self, credentials: dict[str, Any]) -> dict[str, Any] | None:
        """
        Get identity of caller from credentials.

        Args:
            credentials: Provider-specific credentials dict

        Returns:
            Dictionary with identity info:
            {
                'account_id': str,
                'user_arn': str,
                'user_id': str
            }
            None if invalid
        """
        pass

    @abstractmethod
    def is_authorized(
        self,
        user_id: str,
        action: str,
        resource: str | None = None
    ) -> bool:
        """
        Check if user is authorized for an action.

        Args:
            user_id: User identifier
            action: Action to check (e.g., 'reserve:create')
            resource: Optional resource identifier

        Returns:
            True if authorized
        """
        pass


class RegistryProvider(ABC):
    """
    Abstract interface for container registry operations.

    Used for pulling images and caching them locally.

    Implementations:
    - AWS: ECR
    - GCP: Artifact Registry, GCR
    - Custom: Harbor, Docker Registry
    """

    @abstractmethod
    def get_auth_token(self) -> tuple[str, str]:
        """
        Get authentication credentials for registry.

        Returns:
            Tuple of (username, password/token)
        """
        pass

    @abstractmethod
    def get_registry_url(self) -> str:
        """
        Get the registry base URL.

        Returns:
            Registry URL (e.g., '123456789.dkr.ecr.us-east-1.amazonaws.com')
        """
        pass

    @abstractmethod
    def image_exists(self, repository: str, tag: str) -> bool:
        """
        Check if an image exists in the registry.

        Args:
            repository: Repository name
            tag: Image tag

        Returns:
            True if image exists
        """
        pass


class CloudProvider:
    """
    Composite cloud provider aggregating all provider interfaces.

    This is the main entry point for accessing cloud-specific functionality.
    Each cloud implementation provides concrete implementations of all
    sub-providers.

    Example:
        provider = get_cloud_provider()  # Returns AWSCloudProvider or GCPCloudProvider

        # Storage operations
        volume = provider.storage.create_volume(size_gb=100, zone='us-east-1a')

        # Snapshot operations
        snapshot = provider.snapshots.create_snapshot(volume.volume_id)

        # DNS operations
        provider.dns.create_record('dev.example.com', 'CNAME', 'lb.example.com')
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (aws, gcp, custom)."""
        pass

    @property
    @abstractmethod
    def storage(self) -> StorageProvider:
        """Storage operations (volumes)."""
        pass

    @property
    @abstractmethod
    def snapshots(self) -> SnapshotProvider:
        """Snapshot operations."""
        pass

    @property
    @abstractmethod
    def load_balancer(self) -> LoadBalancerProvider:
        """Load balancer operations."""
        pass

    @property
    @abstractmethod
    def dns(self) -> DNSProvider:
        """DNS operations."""
        pass

    @property
    @abstractmethod
    def auth(self) -> AuthProvider:
        """Authentication/authorization operations."""
        pass

    @property
    @abstractmethod
    def registry(self) -> RegistryProvider:
        """Container registry operations."""
        pass


class ProviderError(Exception):
    """Base exception for provider errors."""

    def __init__(self, message: str, provider: str, operation: str, details: dict | None = None):
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
