"""
Unit tests for disk reconciler.
"""
import random
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch, call

import pytest
from botocore.exceptions import ClientError


# ============================================================================
# Volume Discovery Tests
# ============================================================================

class TestVolumeDiscovery:
    """Tests for AWS volume discovery."""

    def test_get_all_gpudev_volumes_success(self, mock_ec2_client):
        """get_all_gpudev_volumes returns parsed volumes."""
        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-12345678",
                        "Size": 100,
                        "State": "available",
                        "AvailabilityZone": "us-east-1a",
                        "CreateTime": datetime.now(UTC),
                        "Attachments": [],
                        "Tags": [
                            {"Key": "gpu-dev-user", "Value": "testuser"},
                            {"Key": "disk-name", "Value": "test-disk"}
                        ]
                    }
                ]
            }
        ]

        from shared.disk_reconciler import get_all_gpudev_volumes
        volumes, error = get_all_gpudev_volumes(mock_ec2_client)

        assert error is None
        assert len(volumes) == 1
        assert volumes[0]["volume_id"] == "vol-12345678"
        assert volumes[0]["user_id"] == "testuser"
        assert volumes[0]["disk_name"] == "test-disk"

    def test_get_all_gpudev_volumes_empty(self, mock_ec2_client):
        """get_all_gpudev_volumes returns empty list when no volumes."""
        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": []}
        ]

        from shared.disk_reconciler import get_all_gpudev_volumes
        volumes, error = get_all_gpudev_volumes(mock_ec2_client)

        assert error is None
        assert volumes == []

    def test_get_all_gpudev_volumes_skips_quarantined(self, mock_ec2_client):
        """get_all_gpudev_volumes skips quarantined volumes."""
        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-12345678",
                        "Size": 100,
                        "State": "available",
                        "AvailabilityZone": "us-east-1a",
                        "CreateTime": datetime.now(UTC),
                        "Attachments": [],
                        "Tags": [
                            {"Key": "gpu-dev-user", "Value": "testuser"},
                            {"Key": "disk-name", "Value": "test-disk"},
                            {"Key": "gpu-dev-quarantined", "Value": "2024-01-01T00:00:00Z"}
                        ]
                    }
                ]
            }
        ]

        from shared.disk_reconciler import get_all_gpudev_volumes
        volumes, error = get_all_gpudev_volumes(mock_ec2_client)

        assert error is None
        assert volumes == []

    def test_get_all_gpudev_volumes_retry_on_throttling(self, mock_ec2_client):
        """get_all_gpudev_volumes retries on throttling."""
        throttle_error = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
            "DescribeVolumes"
        )
        mock_ec2_client.get_paginator.return_value.paginate.side_effect = [
            throttle_error,
            [{"Volumes": []}]
        ]

        from shared.disk_reconciler import get_all_gpudev_volumes
        with patch("time.sleep"):  # Skip actual sleep
            volumes, error = get_all_gpudev_volumes(mock_ec2_client, max_retries=2)

        assert error is None
        assert volumes == []

    def test_get_all_gpudev_volumes_error_on_max_retries(self, mock_ec2_client):
        """get_all_gpudev_volumes returns error after max retries."""
        throttle_error = ClientError(
            {"Error": {"Code": "Throttling", "Message": "Rate exceeded"}},
            "DescribeVolumes"
        )
        mock_ec2_client.get_paginator.return_value.paginate.side_effect = throttle_error

        from shared.disk_reconciler import get_all_gpudev_volumes
        with patch("time.sleep"):
            volumes, error = get_all_gpudev_volumes(mock_ec2_client, max_retries=2)

        assert error is not None
        assert "throttling" in error.lower()
        assert volumes == []


# ============================================================================
# Volume Parsing Tests
# ============================================================================

class TestVolumeParsing:
    """Tests for AWS volume response parsing."""

    def test_parse_volume_from_aws_basic(self):
        """parse_volume_from_aws parses basic volume."""
        aws_volume = {
            "VolumeId": "vol-12345678",
            "Size": 100,
            "State": "available",
            "AvailabilityZone": "us-east-1a",
            "CreateTime": datetime.now(UTC),
            "Attachments": [],
            "Tags": [
                {"Key": "gpu-dev-user", "Value": "testuser"},
                {"Key": "disk-name", "Value": "test-disk"}
            ]
        }

        from shared.disk_reconciler import parse_volume_from_aws
        result = parse_volume_from_aws(aws_volume)

        assert result["volume_id"] == "vol-12345678"
        assert result["size_gb"] == 100
        assert result["user_id"] == "testuser"
        assert result["disk_name"] == "test-disk"
        assert result["is_attached"] is False

    def test_parse_volume_from_aws_attached(self):
        """parse_volume_from_aws parses attached volume."""
        aws_volume = {
            "VolumeId": "vol-12345678",
            "Size": 100,
            "State": "in-use",
            "AvailabilityZone": "us-east-1a",
            "CreateTime": datetime.now(UTC),
            "Attachments": [
                {"InstanceId": "i-12345678", "State": "attached"}
            ],
            "Tags": [
                {"Key": "gpu-dev-user", "Value": "testuser"},
                {"Key": "disk-name", "Value": "test-disk"}
            ]
        }

        from shared.disk_reconciler import parse_volume_from_aws
        result = parse_volume_from_aws(aws_volume)

        assert result["is_attached"] is True
        assert result["attached_instance"] == "i-12345678"

    def test_parse_volume_from_aws_skips_missing_tags(self):
        """parse_volume_from_aws skips volumes without required tags."""
        aws_volume = {
            "VolumeId": "vol-12345678",
            "Size": 100,
            "State": "available",
            "AvailabilityZone": "us-east-1a",
            "CreateTime": datetime.now(UTC),
            "Attachments": [],
            "Tags": [
                {"Key": "gpu-dev-user", "Value": "testuser"}
                # Missing disk-name
            ]
        }

        from shared.disk_reconciler import parse_volume_from_aws
        result = parse_volume_from_aws(aws_volume)

        assert result is None

    def test_parse_volume_from_aws_skips_transient_states(self):
        """parse_volume_from_aws skips volumes in transient states."""
        aws_volume = {
            "VolumeId": "vol-12345678",
            "Size": 100,
            "State": "creating",
            "AvailabilityZone": "us-east-1a",
            "CreateTime": datetime.now(UTC),
            "Attachments": [],
            "Tags": [
                {"Key": "gpu-dev-user", "Value": "testuser"},
                {"Key": "disk-name", "Value": "test-disk"}
            ]
        }

        from shared.disk_reconciler import parse_volume_from_aws
        result = parse_volume_from_aws(aws_volume)

        assert result is None


# ============================================================================
# Orphan Detection Tests
# ============================================================================

class TestOrphanDetection:
    """Tests for orphan volume/record detection."""

    def test_orphaned_aws_volume_imported(
        self, mock_ec2_client, mock_db_cursor, mock_connection_pool
    ):
        """Orphaned AWS volume is imported to database."""
        aws_volume = {
            "volume_id": "vol-12345678",
            "size_gb": 100,
            "state": "available",
            "availability_zone": "us-east-1a",
            "created_at": datetime.now(UTC),
            "is_attached": False,
            "attached_instance": None,
            "disk_name": "new-disk",
            "user_id": "testuser",
            "reservation_id": None,
            "tags": {}
        }

        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        with patch("shared.disk_reconciler.create_disk") as mock_create:
            mock_create.return_value = True
            from shared.disk_reconciler import import_volume_to_db
            result = import_volume_to_db(aws_volume, mock_ec2_client)

        assert result is True
        mock_create.assert_called_once()
        call_args = mock_create.call_args[0][0]
        assert call_args["disk_name"] == "new-disk"
        assert call_args["ebs_volume_id"] == "vol-12345678"


# ============================================================================
# Snapshot Management Tests
# ============================================================================

class TestSnapshotManagement:
    """Tests for snapshot information gathering."""

    def test_get_snapshot_info_no_snapshots(self, mock_ec2_client):
        """get_snapshot_info returns defaults when no snapshots."""
        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        from shared.disk_reconciler import get_snapshot_info
        result = get_snapshot_info(mock_ec2_client, "vol-12345678", "testuser")

        assert result["count"] == 0
        assert result["is_backing_up"] is False
        assert result["last_snapshot_at"] is None

    def test_get_snapshot_info_with_completed_snapshots(self, mock_ec2_client):
        """get_snapshot_info returns info for completed snapshots."""
        snapshot_time = datetime.now(UTC)
        mock_ec2_client.describe_snapshots.side_effect = [
            {"Snapshots": []},  # pending check
            {"Snapshots": [
                {"SnapshotId": "snap-1", "StartTime": snapshot_time - timedelta(days=1)},
                {"SnapshotId": "snap-2", "StartTime": snapshot_time}
            ]}  # completed check
        ]

        from shared.disk_reconciler import get_snapshot_info
        result = get_snapshot_info(mock_ec2_client, "vol-12345678", "testuser")

        assert result["count"] == 2
        assert result["is_backing_up"] is False
        assert result["last_snapshot_at"] == snapshot_time

    def test_get_snapshot_info_with_pending_snapshot(self, mock_ec2_client):
        """get_snapshot_info detects in-progress backup."""
        mock_ec2_client.describe_snapshots.side_effect = [
            {"Snapshots": [{"SnapshotId": "snap-pending", "Status": "pending"}]},
            {"Snapshots": []}
        ]

        from shared.disk_reconciler import get_snapshot_info
        result = get_snapshot_info(mock_ec2_client, "vol-12345678", "testuser")

        assert result["is_backing_up"] is True


# ============================================================================
# Conflict Resolution Tests
# ============================================================================

class TestConflictResolution:
    """Tests for duplicate volume conflict resolution."""

    def test_resolve_conflict_one_attached(self, mock_ec2_client):
        """Attached volume is chosen when one is attached."""
        volumes = [
            {
                "volume_id": "vol-1",
                "is_attached": True,
                "size_gb": 100,
                "created_at": datetime.now(UTC) - timedelta(days=1)
            },
            {
                "volume_id": "vol-2",
                "is_attached": False,
                "size_gb": 100,
                "created_at": datetime.now(UTC)
            }
        ]

        mock_ec2_client.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-2", "Attachments": []}]
        }
        mock_ec2_client.create_tags.return_value = {}

        from shared.disk_reconciler import resolve_volume_conflict_with_quarantine
        current, quarantined = resolve_volume_conflict_with_quarantine(
            mock_ec2_client,
            "testuser",
            "test-disk",
            volumes,
            None
        )

        assert current["volume_id"] == "vol-1"
        assert "vol-2" in quarantined

    def test_resolve_conflict_multiple_attached_fails(self, mock_ec2_client):
        """Multiple attached volumes returns None (error state)."""
        volumes = [
            {
                "volume_id": "vol-1",
                "is_attached": True,
                "size_gb": 100,
                "created_at": datetime.now(UTC)
            },
            {
                "volume_id": "vol-2",
                "is_attached": True,
                "size_gb": 100,
                "created_at": datetime.now(UTC)
            }
        ]

        from shared.disk_reconciler import resolve_volume_conflict_with_quarantine
        current, quarantined = resolve_volume_conflict_with_quarantine(
            mock_ec2_client,
            "testuser",
            "test-disk",
            volumes,
            None
        )

        assert current is None
        assert quarantined == []

    def test_resolve_conflict_uses_db_preference(self, mock_ec2_client):
        """DB-referenced volume is preferred when all detached."""
        volumes = [
            {
                "volume_id": "vol-1",
                "is_attached": False,
                "size_gb": 100,
                "created_at": datetime.now(UTC)
            },
            {
                "volume_id": "vol-2",
                "is_attached": False,
                "size_gb": 100,
                "created_at": datetime.now(UTC) - timedelta(days=1)
            }
        ]

        db_record = {"ebs_volume_id": "vol-2"}

        mock_ec2_client.describe_volumes.return_value = {
            "Volumes": [{"VolumeId": "vol-1", "Attachments": []}]
        }
        mock_ec2_client.create_tags.return_value = {}

        from shared.disk_reconciler import resolve_volume_conflict_with_quarantine
        current, quarantined = resolve_volume_conflict_with_quarantine(
            mock_ec2_client,
            "testuser",
            "test-disk",
            volumes,
            db_record
        )

        assert current["volume_id"] == "vol-2"
        assert "vol-1" in quarantined


# ============================================================================
# Cross-AZ Migration Tests
# ============================================================================

class TestCrossAZMigration:
    """Tests for cross-AZ volume migration logic."""

    def test_sync_volume_detects_size_change(
        self, mock_ec2_client, mock_db_cursor
    ):
        """sync_volume_to_db detects volume size change."""
        aws_vol = {
            "volume_id": "vol-12345678",
            "size_gb": 200,  # Changed from 100
            "is_attached": False,
            "created_at": datetime.now(UTC)
        }
        db_disk = {
            "disk_id": 1,
            "disk_name": "test-disk",
            "user_id": "testuser",
            "ebs_volume_id": "vol-12345678",
            "size_gb": 100,
            "in_use": False,
            "snapshot_count": 0,
            "is_backing_up": False,
            "last_snapshot_at": None
        }

        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        with patch("shared.disk_reconciler.update_disk") as mock_update:
            mock_update.return_value = True
            from shared.disk_reconciler import sync_volume_to_db
            result = sync_volume_to_db(aws_vol, db_disk, mock_ec2_client)

        assert result == "updated"
        mock_update.assert_called_once()
        call_args = mock_update.call_args[0][2]
        assert call_args["size_gb"] == 200

    def test_sync_volume_detects_attachment_change(
        self, mock_ec2_client, mock_db_cursor
    ):
        """sync_volume_to_db detects attachment status change."""
        aws_vol = {
            "volume_id": "vol-12345678",
            "size_gb": 100,
            "is_attached": True,  # Changed from False
            "created_at": datetime.now(UTC)
        }
        db_disk = {
            "disk_id": 1,
            "disk_name": "test-disk",
            "user_id": "testuser",
            "ebs_volume_id": "vol-12345678",
            "size_gb": 100,
            "in_use": False,  # DB shows not in use
            "snapshot_count": 0,
            "is_backing_up": False,
            "last_snapshot_at": None
        }

        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        with patch("shared.disk_reconciler.update_disk") as mock_update:
            mock_update.return_value = True
            from shared.disk_reconciler import sync_volume_to_db
            result = sync_volume_to_db(aws_vol, db_disk, mock_ec2_client)

        assert result == "updated"
        call_args = mock_update.call_args[0][2]
        assert call_args["in_use"] is True

    def test_sync_volume_no_changes(self, mock_ec2_client, mock_db_cursor):
        """sync_volume_to_db returns synced when no changes."""
        aws_vol = {
            "volume_id": "vol-12345678",
            "size_gb": 100,
            "is_attached": False,
            "created_at": datetime.now(UTC)
        }
        db_disk = {
            "disk_id": 1,
            "disk_name": "test-disk",
            "user_id": "testuser",
            "ebs_volume_id": "vol-12345678",
            "size_gb": 100,
            "in_use": False,
            "snapshot_count": 0,
            "is_backing_up": False,
            "last_snapshot_at": None
        }

        mock_ec2_client.describe_snapshots.return_value = {"Snapshots": []}

        with patch("shared.disk_reconciler.update_disk") as mock_update:
            from shared.disk_reconciler import sync_volume_to_db
            result = sync_volume_to_db(aws_vol, db_disk, mock_ec2_client)

        assert result == "synced"
        mock_update.assert_not_called()


# ============================================================================
# Quarantine Cleanup Tests
# ============================================================================

class TestQuarantineCleanup:
    """Tests for quarantined volume cleanup."""

    def test_cleanup_old_quarantined_volumes_deletes_old(self, mock_ec2_client):
        """Old quarantined volumes are deleted."""
        old_timestamp = (datetime.now(UTC) - timedelta(days=35)).isoformat()
        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-old",
                        "Size": 100,
                        "Attachments": [],
                        "Tags": [
                            {"Key": "gpu-dev-quarantined", "Value": old_timestamp},
                            {"Key": "gpu-dev-user", "Value": "testuser"},
                            {"Key": "disk-name", "Value": "old-disk"}
                        ]
                    }
                ]
            }
        ]
        mock_ec2_client.create_snapshot.return_value = {"SnapshotId": "snap-123"}
        mock_ec2_client.delete_volume.return_value = {}

        from shared.disk_reconciler import cleanup_old_quarantined_volumes
        stats = cleanup_old_quarantined_volumes(mock_ec2_client, max_age_days=30)

        assert stats["deleted"] == 1
        mock_ec2_client.create_snapshot.assert_called_once()
        mock_ec2_client.delete_volume.assert_called_once()

    def test_cleanup_old_quarantined_volumes_skips_recent(self, mock_ec2_client):
        """Recent quarantined volumes are not deleted."""
        recent_timestamp = (datetime.now(UTC) - timedelta(days=5)).isoformat()
        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-recent",
                        "Size": 100,
                        "Attachments": [],
                        "Tags": [
                            {"Key": "gpu-dev-quarantined", "Value": recent_timestamp},
                            {"Key": "gpu-dev-user", "Value": "testuser"},
                            {"Key": "disk-name", "Value": "recent-disk"}
                        ]
                    }
                ]
            }
        ]

        from shared.disk_reconciler import cleanup_old_quarantined_volumes
        stats = cleanup_old_quarantined_volumes(mock_ec2_client, max_age_days=30)

        assert stats["deleted"] == 0
        assert stats["skipped_too_recent"] == 1
        mock_ec2_client.delete_volume.assert_not_called()

    def test_cleanup_old_quarantined_volumes_skips_attached(self, mock_ec2_client):
        """Attached quarantined volumes are not deleted (safety)."""
        old_timestamp = (datetime.now(UTC) - timedelta(days=35)).isoformat()
        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {
                "Volumes": [
                    {
                        "VolumeId": "vol-attached",
                        "Size": 100,
                        "Attachments": [{"State": "attached"}],
                        "Tags": [
                            {"Key": "gpu-dev-quarantined", "Value": old_timestamp},
                            {"Key": "gpu-dev-user", "Value": "testuser"},
                            {"Key": "disk-name", "Value": "attached-disk"}
                        ]
                    }
                ]
            }
        ]

        from shared.disk_reconciler import cleanup_old_quarantined_volumes
        stats = cleanup_old_quarantined_volumes(mock_ec2_client, max_age_days=30)

        assert stats["deleted"] == 0
        assert stats["errors"] == 1
        mock_ec2_client.delete_volume.assert_not_called()


# ============================================================================
# Reconciliation Lock Tests
# ============================================================================

class TestReconciliationLock:
    """Tests for reconciliation advisory lock."""

    def test_reconcile_acquires_lock(
        self, mock_ec2_client, mock_db_cursor, mock_connection_pool
    ):
        """reconcile_all_disks acquires advisory lock."""
        mock_db_cursor.fetchone.return_value = {"locked": True}
        mock_db_cursor.fetchall.return_value = []

        mock_ec2_client.get_paginator.return_value.paginate.return_value = [
            {"Volumes": []}
        ]

        with patch("shared.disk_reconciler.get_db_cursor") as mock_get_cursor:
            mock_get_cursor.return_value.__enter__.return_value = mock_db_cursor
            with patch("shared.disk_reconciler.get_db_transaction"):
                from shared.disk_reconciler import reconcile_all_disks
                stats = reconcile_all_disks(mock_ec2_client)

        # Check lock was acquired
        assert mock_db_cursor.execute.call_count >= 2
        lock_call = mock_db_cursor.execute.call_args_list[0]
        assert "pg_try_advisory_lock" in lock_call[0][0]

    def test_reconcile_skips_if_lock_held(
        self, mock_ec2_client, mock_db_cursor, mock_connection_pool
    ):
        """reconcile_all_disks skips if lock is already held."""
        mock_db_cursor.fetchone.return_value = {"locked": False}

        with patch("shared.disk_reconciler.get_db_cursor") as mock_get_cursor:
            mock_get_cursor.return_value.__enter__.return_value = mock_db_cursor
            from shared.disk_reconciler import reconcile_all_disks
            stats = reconcile_all_disks(mock_ec2_client)

        assert stats["skipped_concurrent_run"] is True
        mock_ec2_client.get_paginator.assert_not_called()


# ============================================================================
# Timezone Handling Tests
# ============================================================================

class TestTimezoneHandling:
    """Tests for timezone-aware datetime handling."""

    def test_ensure_utc_with_naive_datetime(self):
        """ensure_utc handles naive datetime."""
        from shared.disk_reconciler import ensure_utc
        naive = datetime(2024, 1, 1, 12, 0, 0)
        result = ensure_utc(naive)

        assert result.tzinfo is not None
        assert result.tzinfo == UTC

    def test_ensure_utc_with_aware_datetime(self):
        """ensure_utc handles aware datetime."""
        from shared.disk_reconciler import ensure_utc
        aware = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        result = ensure_utc(aware)

        assert result.tzinfo is not None
        assert result == aware

    def test_ensure_utc_with_none(self):
        """ensure_utc handles None."""
        from shared.disk_reconciler import ensure_utc
        assert ensure_utc(None) is None
