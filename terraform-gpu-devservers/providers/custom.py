"""
Custom Cloud Provider Template

This is a template for implementing custom cloud providers.
Copy this file and implement all abstract methods.

To use a custom provider:
1. Copy this file and rename (e.g., mycloud.py)
2. Implement all methods marked with NotImplementedError
3. Update providers/__init__.py to include your provider
4. Set environment variable: CLOUD_PROVIDER=mycloud

Required Methods to Implement:
- Volume operations: create, delete, attach, detach, get, list
- Snapshot operations: create, delete, get, list, wait_for
- Object storage: upload, download

Optional Methods (have default no-op implementations):
- DNS: create_dns_record, delete_dns_record
- Compute: get_nodes_by_gpu_type, get_node_availability
"""

import logging
from typing import Dict, List, Optional, Any

from .base import (
    CloudProvider,
    VolumeInfo,
    SnapshotInfo,
    NodeInfo,
)

logger = logging.getLogger(__name__)


class CustomProvider(CloudProvider):
    """
    Template for custom cloud provider implementation.

    Replace all NotImplementedError blocks with your cloud's API calls.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        Initialize your custom provider.

        Args:
            config: Provider-specific configuration dict.
                    Can include API keys, endpoints, regions, etc.

        Example:
            config = {
                "api_endpoint": "https://api.mycloud.com",
                "api_key": os.environ.get("MYCLOUD_API_KEY"),
                "region": "us-west",
            }
        """
        self.config = config or {}
        # Initialize your cloud SDK clients here
        # self.client = MyCloudSDK(...)

    def name(self) -> str:
        return "custom"

    # =========================================================================
    # BLOCK STORAGE - REQUIRED
    # =========================================================================

    def create_volume(
        self,
        size_gb: int,
        availability_zone: str,
        volume_type: str = "ssd",
        tags: Optional[Dict[str, str]] = None,
        snapshot_id: Optional[str] = None,
    ) -> VolumeInfo:
        """
        Create a block storage volume.

        Args:
            size_gb: Volume size in gigabytes
            availability_zone: Zone/region for the volume
            volume_type: Type of storage (ssd, hdd, etc.)
            tags: Key-value tags for the volume
            snapshot_id: Optional snapshot to create volume from

        Returns:
            VolumeInfo with the created volume details

        Implementation Notes:
            - Map volume_type to your cloud's storage types
            - Apply tags/labels as supported by your cloud
            - If snapshot_id provided, create volume from snapshot
        """
        raise NotImplementedError(
            "Implement volume creation for your cloud provider. "
            "Return VolumeInfo(volume_id, size_gb, availability_zone, status, tags)"
        )

    def delete_volume(self, volume_id: str) -> bool:
        """
        Delete a block storage volume.

        Args:
            volume_id: ID of the volume to delete

        Returns:
            True if deleted successfully, False otherwise
        """
        raise NotImplementedError("Implement volume deletion for your cloud provider")

    def attach_volume(
        self, volume_id: str, instance_id: str, device_path: str
    ) -> bool:
        """
        Attach volume to a compute instance.

        Args:
            volume_id: ID of the volume
            instance_id: ID of the compute instance
            device_path: Device path on the instance (e.g., /dev/xvdf)

        Returns:
            True if attached successfully

        Note:
            Some clouds may ignore device_path and auto-assign
        """
        raise NotImplementedError("Implement volume attachment for your cloud provider")

    def detach_volume(self, volume_id: str) -> bool:
        """
        Detach volume from its instance.

        Args:
            volume_id: ID of the volume to detach

        Returns:
            True if detached successfully
        """
        raise NotImplementedError("Implement volume detachment for your cloud provider")

    def get_volume(self, volume_id: str) -> Optional[VolumeInfo]:
        """
        Get information about a specific volume.

        Args:
            volume_id: ID of the volume

        Returns:
            VolumeInfo if found, None otherwise
        """
        raise NotImplementedError("Implement volume get for your cloud provider")

    def list_volumes(
        self, filters: Optional[Dict[str, str]] = None
    ) -> List[VolumeInfo]:
        """
        List volumes matching filters.

        Args:
            filters: Key-value filters to match (typically by tags)

        Returns:
            List of VolumeInfo matching the filters
        """
        raise NotImplementedError("Implement volume listing for your cloud provider")

    # =========================================================================
    # SNAPSHOTS - REQUIRED
    # =========================================================================

    def create_snapshot(
        self,
        volume_id: str,
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> SnapshotInfo:
        """
        Create a snapshot of a volume.

        Args:
            volume_id: ID of the volume to snapshot
            description: Optional description
            tags: Key-value tags for the snapshot

        Returns:
            SnapshotInfo with snapshot details
        """
        raise NotImplementedError("Implement snapshot creation for your cloud provider")

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete a snapshot."""
        raise NotImplementedError("Implement snapshot deletion for your cloud provider")

    def get_snapshot(self, snapshot_id: str) -> Optional[SnapshotInfo]:
        """Get snapshot information."""
        raise NotImplementedError("Implement snapshot get for your cloud provider")

    def list_snapshots(
        self, filters: Optional[Dict[str, str]] = None
    ) -> List[SnapshotInfo]:
        """List snapshots matching filters."""
        raise NotImplementedError("Implement snapshot listing for your cloud provider")

    def wait_for_snapshot(
        self, snapshot_id: str, timeout_seconds: int = 600
    ) -> bool:
        """
        Wait for snapshot to complete.

        Args:
            snapshot_id: ID of the snapshot
            timeout_seconds: Maximum time to wait

        Returns:
            True if completed, False if timeout or error
        """
        raise NotImplementedError(
            "Implement snapshot wait for your cloud provider. "
            "Poll get_snapshot() until status indicates completion."
        )

    # =========================================================================
    # COMPUTE - OPTIONAL (K8s handles most of this)
    # =========================================================================

    def get_nodes_by_gpu_type(self, gpu_type: str) -> List[NodeInfo]:
        """
        Get compute nodes by GPU type.

        Default implementation returns empty list.
        Override if your cloud has a native way to query this.
        Typically, Kubernetes API is used instead.
        """
        return []

    def get_node_availability(self) -> Dict[str, Dict[str, int]]:
        """
        Get GPU availability per type.

        Default returns empty dict.
        Handled by availability updater service via K8s API.
        """
        return {}

    # =========================================================================
    # OBJECT STORAGE - REQUIRED
    # =========================================================================

    def upload_to_object_storage(
        self,
        bucket: str,
        key: str,
        content: bytes,
        metadata: Optional[Dict[str, str]] = None,
    ) -> str:
        """
        Upload content to object storage (S3-compatible).

        Args:
            bucket: Bucket/container name
            key: Object key/path
            content: Binary content to upload
            metadata: Optional metadata

        Returns:
            URL or path to the uploaded object
        """
        raise NotImplementedError(
            "Implement object storage upload for your cloud provider"
        )

    def download_from_object_storage(
        self, bucket: str, key: str
    ) -> Optional[bytes]:
        """
        Download content from object storage.

        Args:
            bucket: Bucket/container name
            key: Object key/path

        Returns:
            Binary content if found, None otherwise
        """
        raise NotImplementedError(
            "Implement object storage download for your cloud provider"
        )

    # =========================================================================
    # DNS - OPTIONAL (can use external-dns instead)
    # =========================================================================

    def create_dns_record(
        self,
        subdomain: str,
        target: str,
        record_type: str = "A",
    ) -> bool:
        """
        Create DNS record. Optional - default no-op.

        Override if your cloud has integrated DNS management.
        """
        logger.info(f"DNS record creation not implemented: {subdomain} -> {target}")
        return True

    def delete_dns_record(
        self,
        subdomain: str,
        record_type: str = "A",
    ) -> bool:
        """
        Delete DNS record. Optional - default no-op.
        """
        logger.info(f"DNS record deletion not implemented: {subdomain}")
        return True
