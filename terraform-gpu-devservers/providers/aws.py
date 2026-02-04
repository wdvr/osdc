"""
AWS Cloud Provider Implementation

Wraps existing boto3 code to provide cloud-agnostic interface.
"""

import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

from .base import (
    CloudProvider,
    AuthProvider,
    VolumeInfo,
    SnapshotInfo,
    NodeInfo,
)

logger = logging.getLogger(__name__)


class AWSProvider(CloudProvider):
    """AWS implementation of CloudProvider interface."""

    def __init__(self, region: str = "us-east-2"):
        self.region = region
        self._ec2 = None
        self._s3 = None
        self._autoscaling = None
        self._efs = None

    @property
    def ec2(self):
        if self._ec2 is None:
            self._ec2 = boto3.client("ec2", region_name=self.region)
        return self._ec2

    @property
    def s3(self):
        if self._s3 is None:
            self._s3 = boto3.client("s3", region_name=self.region)
        return self._s3

    @property
    def efs(self):
        if self._efs is None:
            self._efs = boto3.client("efs", region_name=self.region)
        return self._efs

    def name(self) -> str:
        return "aws"

    # === Block Storage (EBS) ===

    def create_volume(
        self,
        size_gb: int,
        availability_zone: str,
        volume_type: str = "ssd",
        tags: Optional[Dict[str, str]] = None,
        snapshot_id: Optional[str] = None,
    ) -> VolumeInfo:
        """Create an EBS volume."""
        aws_volume_type = {"ssd": "gp3", "hdd": "sc1", "io": "io2"}.get(volume_type, "gp3")

        params = {
            "AvailabilityZone": availability_zone,
            "Size": size_gb,
            "VolumeType": aws_volume_type,
            "Encrypted": True,
        }

        if snapshot_id:
            params["SnapshotId"] = snapshot_id

        if aws_volume_type == "gp3":
            params["Iops"] = 3000
            params["Throughput"] = 125

        response = self.ec2.create_volume(**params)
        volume_id = response["VolumeId"]

        if tags:
            self.ec2.create_tags(
                Resources=[volume_id],
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )

        return VolumeInfo(
            volume_id=volume_id,
            size_gb=response["Size"],
            availability_zone=response["AvailabilityZone"],
            status=response["State"],
            tags=tags or {},
        )

    def delete_volume(self, volume_id: str) -> bool:
        """Delete an EBS volume."""
        try:
            self.ec2.delete_volume(VolumeId=volume_id)
            return True
        except ClientError as e:
            logger.error(f"Failed to delete volume {volume_id}: {e}")
            return False

    def attach_volume(
        self, volume_id: str, instance_id: str, device_path: str
    ) -> bool:
        """Attach EBS volume to EC2 instance."""
        try:
            self.ec2.attach_volume(
                VolumeId=volume_id,
                InstanceId=instance_id,
                Device=device_path,
            )
            return True
        except ClientError as e:
            logger.error(f"Failed to attach volume {volume_id}: {e}")
            return False

    def detach_volume(self, volume_id: str) -> bool:
        """Detach EBS volume from instance."""
        try:
            self.ec2.detach_volume(VolumeId=volume_id)
            return True
        except ClientError as e:
            logger.error(f"Failed to detach volume {volume_id}: {e}")
            return False

    def get_volume(self, volume_id: str) -> Optional[VolumeInfo]:
        """Get EBS volume information."""
        try:
            response = self.ec2.describe_volumes(VolumeIds=[volume_id])
            if response["Volumes"]:
                vol = response["Volumes"][0]
                tags = {t["Key"]: t["Value"] for t in vol.get("Tags", [])}
                return VolumeInfo(
                    volume_id=vol["VolumeId"],
                    size_gb=vol["Size"],
                    availability_zone=vol["AvailabilityZone"],
                    status=vol["State"],
                    tags=tags,
                )
        except ClientError:
            pass
        return None

    def list_volumes(
        self, filters: Optional[Dict[str, str]] = None
    ) -> List[VolumeInfo]:
        """List EBS volumes matching filters."""
        aws_filters = []
        if filters:
            for key, value in filters.items():
                aws_filters.append({"Name": f"tag:{key}", "Values": [value]})

        response = self.ec2.describe_volumes(Filters=aws_filters)

        volumes = []
        for vol in response.get("Volumes", []):
            tags = {t["Key"]: t["Value"] for t in vol.get("Tags", [])}
            volumes.append(
                VolumeInfo(
                    volume_id=vol["VolumeId"],
                    size_gb=vol["Size"],
                    availability_zone=vol["AvailabilityZone"],
                    status=vol["State"],
                    tags=tags,
                )
            )
        return volumes

    # === Snapshots ===

    def create_snapshot(
        self,
        volume_id: str,
        description: str = "",
        tags: Optional[Dict[str, str]] = None,
    ) -> SnapshotInfo:
        """Create EBS snapshot."""
        response = self.ec2.create_snapshot(
            VolumeId=volume_id,
            Description=description,
        )

        snapshot_id = response["SnapshotId"]

        if tags:
            self.ec2.create_tags(
                Resources=[snapshot_id],
                Tags=[{"Key": k, "Value": v} for k, v in tags.items()],
            )

        return SnapshotInfo(
            snapshot_id=snapshot_id,
            volume_id=volume_id,
            status=response["State"],
            size_gb=response["VolumeSize"],
            created_at=response["StartTime"].isoformat(),
            tags=tags or {},
        )

    def delete_snapshot(self, snapshot_id: str) -> bool:
        """Delete EBS snapshot."""
        try:
            self.ec2.delete_snapshot(SnapshotId=snapshot_id)
            return True
        except ClientError as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            return False

    def get_snapshot(self, snapshot_id: str) -> Optional[SnapshotInfo]:
        """Get EBS snapshot information."""
        try:
            response = self.ec2.describe_snapshots(SnapshotIds=[snapshot_id])
            if response["Snapshots"]:
                snap = response["Snapshots"][0]
                tags = {t["Key"]: t["Value"] for t in snap.get("Tags", [])}
                return SnapshotInfo(
                    snapshot_id=snap["SnapshotId"],
                    volume_id=snap["VolumeId"],
                    status=snap["State"],
                    size_gb=snap["VolumeSize"],
                    created_at=snap["StartTime"].isoformat(),
                    tags=tags,
                )
        except ClientError:
            pass
        return None

    def list_snapshots(
        self,
        filters: Optional[Dict[str, str]] = None,
        volume_id: Optional[str] = None,
        status: Optional[List[str]] = None,
        use_pagination: bool = True,
    ) -> List[SnapshotInfo]:
        """
        List EBS snapshots matching filters.

        Args:
            filters: Tag-based filters as key-value pairs (e.g., {"gpu-dev-user": "john"})
            volume_id: Filter by specific volume ID
            status: Filter by status (e.g., ["pending", "completed"])
            use_pagination: Whether to use pagination for large result sets
        """
        aws_filters = []

        # Tag-based filters
        if filters:
            for key, value in filters.items():
                aws_filters.append({"Name": f"tag:{key}", "Values": [value]})

        # Volume ID filter
        if volume_id:
            aws_filters.append({"Name": "volume-id", "Values": [volume_id]})

        # Status filter
        if status:
            aws_filters.append({"Name": "status", "Values": status})

        snapshots = []

        if use_pagination:
            paginator = self.ec2.get_paginator('describe_snapshots')
            page_iterator = paginator.paginate(
                OwnerIds=["self"],
                Filters=aws_filters if aws_filters else [],
                PaginationConfig={'PageSize': 100}
            )

            for page in page_iterator:
                for snap in page.get("Snapshots", []):
                    tags = {t["Key"]: t["Value"] for t in snap.get("Tags", [])}
                    snapshots.append(
                        SnapshotInfo(
                            snapshot_id=snap["SnapshotId"],
                            volume_id=snap["VolumeId"],
                            status=snap["State"],
                            size_gb=snap["VolumeSize"],
                            created_at=snap["StartTime"].isoformat(),
                            tags=tags,
                        )
                    )
        else:
            params = {"OwnerIds": ["self"]}
            if aws_filters:
                params["Filters"] = aws_filters
            response = self.ec2.describe_snapshots(**params)

            for snap in response.get("Snapshots", []):
                tags = {t["Key"]: t["Value"] for t in snap.get("Tags", [])}
                snapshots.append(
                    SnapshotInfo(
                        snapshot_id=snap["SnapshotId"],
                        volume_id=snap["VolumeId"],
                        status=snap["State"],
                        size_gb=snap["VolumeSize"],
                        created_at=snap["StartTime"].isoformat(),
                        tags=tags,
                    )
                )
        return snapshots

    def wait_for_snapshot(
        self, snapshot_id: str, timeout_seconds: int = 600
    ) -> bool:
        """Wait for EBS snapshot to complete."""
        try:
            waiter = self.ec2.get_waiter("snapshot_completed")
            waiter.wait(
                SnapshotIds=[snapshot_id],
                WaiterConfig={"Delay": 15, "MaxAttempts": timeout_seconds // 15},
            )
            return True
        except Exception as e:
            logger.error(f"Snapshot {snapshot_id} did not complete: {e}")
            return False

    # === Compute ===

    def get_nodes_by_gpu_type(self, gpu_type: str) -> List[NodeInfo]:
        """Get EC2 instances by GPU type label."""
        # This would typically query K8s nodes, not EC2 directly
        # For now, return empty - K8s client handles this
        return []

    def get_node_availability(self) -> Dict[str, Dict[str, int]]:
        """Get GPU availability - delegates to K8s."""
        # This is handled by the availability updater
        return {}

    # === Object Storage (S3) ===

    def upload_to_object_storage(
        self,
        bucket: str,
        key: str,
        content: bytes,
        metadata: Optional[Dict[str, str]] = None,
        content_type: str = "application/octet-stream",
    ) -> str:
        """Upload content to S3."""
        self.s3.put_object(
            Bucket=bucket,
            Key=key,
            Body=content,
            ContentType=content_type,
            **({"Metadata": metadata} if metadata else {}),
        )

        return f"s3://{bucket}/{key}"

    def download_from_object_storage(
        self, bucket: str, key: str
    ) -> Optional[bytes]:
        """Download content from S3."""
        try:
            response = self.s3.get_object(Bucket=bucket, Key=key)
            return response["Body"].read()
        except ClientError:
            return None


class AWSIAMAuthProvider(AuthProvider):
    """AWS IAM/STS based authentication (legacy)."""

    def __init__(self, region: str = "us-east-2"):
        self.region = region
        self._sts = None

    @property
    def sts(self):
        if self._sts is None:
            self._sts = boto3.client("sts", region_name=self.region)
        return self._sts

    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify AWS credentials (token contains access key info)."""
        # This is handled differently - credentials are verified via STS
        return None

    def get_user_info(self, token: str) -> Optional[Dict[str, Any]]:
        """Get user info from AWS identity."""
        try:
            response = self.sts.get_caller_identity()
            return {
                "user_id": response["UserId"],
                "account": response["Account"],
                "arn": response["Arn"],
            }
        except Exception:
            return None

    def create_api_key(
        self, user_id: str, scopes: List[str], ttl_hours: int = 24
    ) -> str:
        """Create API key - handled by API service."""
        raise NotImplementedError("Use API service for API key creation")
