"""
K8s-native persistent storage using PersistentVolumeClaims.

Cloud-agnostic alternative to EBS volumes — works on any K8s cluster
that has a CSI driver (local-path, gp3, pd-ssd, etc.).
"""

import logging
from typing import Optional

from kubernetes import client

logger = logging.getLogger(__name__)


def create_persistent_disk(
    core_v1: client.CoreV1Api,
    namespace: str,
    user_id: str,
    disk_name: str,
    size_gb: int = 100,
    storage_class: str = "",
) -> Optional[str]:
    """Create a PVC for a user's persistent disk.

    Returns the PVC name on success, None on failure.
    """
    pvc_name = f"disk-{user_id}-{disk_name}"[:63]

    body = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(
            name=pvc_name,
            namespace=namespace,
            labels={
                "gpu-dev-user": user_id,
                "disk-name": disk_name,
                "managed-by": "gpu-dev",
            },
        ),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=client.V1VolumeResourceRequirements(
                requests={"storage": f"{size_gb}Gi"}
            ),
            storage_class_name=storage_class or None,
        ),
    )

    try:
        core_v1.create_namespaced_persistent_volume_claim(
            namespace=namespace, body=body
        )
        logger.info(f"Created PVC {pvc_name} ({size_gb}Gi)")
        return pvc_name
    except client.ApiException as e:
        if e.status == 409:
            logger.info(f"PVC {pvc_name} already exists")
            return pvc_name
        logger.error(f"Failed to create PVC {pvc_name}: {e}")
        return None


def get_persistent_disk(
    core_v1: client.CoreV1Api,
    namespace: str,
    user_id: str,
    disk_name: str,
) -> Optional[str]:
    """Check if a PVC exists for a user's disk. Returns PVC name or None."""
    pvc_name = f"disk-{user_id}-{disk_name}"[:63]
    try:
        core_v1.read_namespaced_persistent_volume_claim(
            name=pvc_name, namespace=namespace
        )
        return pvc_name
    except client.ApiException as e:
        if e.status == 404:
            return None
        logger.error(f"Error checking PVC {pvc_name}: {e}")
        return None


def delete_persistent_disk(
    core_v1: client.CoreV1Api,
    namespace: str,
    pvc_name: str,
) -> bool:
    """Delete a PVC. Returns True on success."""
    try:
        core_v1.delete_namespaced_persistent_volume_claim(
            name=pvc_name, namespace=namespace
        )
        logger.info(f"Deleted PVC {pvc_name}")
        return True
    except client.ApiException as e:
        if e.status == 404:
            logger.info(f"PVC {pvc_name} already deleted")
            return True
        logger.error(f"Failed to delete PVC {pvc_name}: {e}")
        return False


def create_disk_snapshot(
    custom_v1,
    namespace: str,
    pvc_name: str,
    snapshot_class: str = "",
) -> Optional[str]:
    """Create a VolumeSnapshot from a PVC (requires snapshot CRDs).

    Returns snapshot name on success, None on failure.
    """
    snapshot_name = f"snap-{pvc_name}"[:63]

    body = {
        "apiVersion": "snapshot.storage.k8s.io/v1",
        "kind": "VolumeSnapshot",
        "metadata": {
            "name": snapshot_name,
            "namespace": namespace,
            "labels": {"managed-by": "gpu-dev"},
        },
        "spec": {
            "source": {"persistentVolumeClaimName": pvc_name},
        },
    }
    if snapshot_class:
        body["spec"]["volumeSnapshotClassName"] = snapshot_class

    try:
        custom_v1.create_namespaced_custom_object(
            group="snapshot.storage.k8s.io",
            version="v1",
            namespace=namespace,
            plural="volumesnapshots",
            body=body,
        )
        logger.info(f"Created VolumeSnapshot {snapshot_name}")
        return snapshot_name
    except Exception as e:
        logger.error(f"Failed to create VolumeSnapshot: {e}")
        return None


def restore_from_snapshot(
    core_v1: client.CoreV1Api,
    namespace: str,
    snapshot_name: str,
    user_id: str,
    disk_name: str,
    size_gb: int = 100,
    storage_class: str = "",
) -> Optional[str]:
    """Create a PVC restored from a VolumeSnapshot.

    Returns PVC name on success, None on failure.
    """
    pvc_name = f"disk-{user_id}-{disk_name}"[:63]

    body = client.V1PersistentVolumeClaim(
        metadata=client.V1ObjectMeta(
            name=pvc_name,
            namespace=namespace,
            labels={
                "gpu-dev-user": user_id,
                "disk-name": disk_name,
                "managed-by": "gpu-dev",
                "restored-from": snapshot_name,
            },
        ),
        spec=client.V1PersistentVolumeClaimSpec(
            access_modes=["ReadWriteOnce"],
            resources=client.V1VolumeResourceRequirements(
                requests={"storage": f"{size_gb}Gi"}
            ),
            storage_class_name=storage_class or None,
            data_source=client.V1TypedLocalObjectReference(
                api_group="snapshot.storage.k8s.io",
                kind="VolumeSnapshot",
                name=snapshot_name,
            ),
        ),
    )

    try:
        core_v1.create_namespaced_persistent_volume_claim(
            namespace=namespace, body=body
        )
        logger.info(f"Created PVC {pvc_name} from snapshot {snapshot_name}")
        return pvc_name
    except client.ApiException as e:
        if e.status == 409:
            logger.info(f"PVC {pvc_name} already exists")
            return pvc_name
        logger.error(f"Failed to restore PVC from snapshot: {e}")
        return None
