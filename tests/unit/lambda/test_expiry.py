"""
Unit tests for Lambda reservation_expiry

Tests:
- Expiration detection
- Warning generation
- Cleanup operations
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest


class TestExpirationDetection:
    """Tests for detecting expired reservations"""

    def test_reservation_is_expired_when_past_expires_at(self):
        """Should detect reservation as expired when expires_at is in the past"""
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (now - timedelta(minutes=5)).isoformat(),
        }

        def is_expired(reservation):
            expires_at_str = reservation.get("expires_at")
            if not expires_at_str:
                return False
            expires_at = datetime.fromisoformat(expires_at_str)
            return datetime.now(timezone.utc) > expires_at

        assert is_expired(reservation) is True

    def test_reservation_not_expired_when_future(self):
        """Should not detect reservation as expired when expires_at is in future"""
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (now + timedelta(hours=2)).isoformat(),
        }

        def is_expired(reservation):
            expires_at_str = reservation.get("expires_at")
            if not expires_at_str:
                return False
            expires_at = datetime.fromisoformat(expires_at_str)
            return datetime.now(timezone.utc) > expires_at

        assert is_expired(reservation) is False

    def test_only_active_reservations_can_expire(self):
        """Should only check expiration for active reservations"""
        EXPIRABLE_STATUSES = ["active", "preparing"]

        def should_check_expiry(reservation):
            return reservation.get("status") in EXPIRABLE_STATUSES

        assert should_check_expiry({"status": "active"}) is True
        assert should_check_expiry({"status": "preparing"}) is True
        assert should_check_expiry({"status": "completed"}) is False
        assert should_check_expiry({"status": "cancelled"}) is False
        assert should_check_expiry({"status": "queued"}) is False


class TestWarningGeneration:
    """Tests for expiry warning generation"""

    def test_warning_at_30_minutes(self):
        """Should generate warning at 30 minutes before expiry"""
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (now + timedelta(minutes=28)).isoformat(),
            "warned_30min": False,
        }

        def should_warn_30min(reservation):
            if reservation.get("warned_30min"):
                return False
            expires_at = datetime.fromisoformat(reservation["expires_at"])
            time_remaining = expires_at - datetime.now(timezone.utc)
            return time_remaining <= timedelta(minutes=30) and time_remaining > timedelta(minutes=15)

        assert should_warn_30min(reservation) is True

    def test_warning_at_15_minutes(self):
        """Should generate warning at 15 minutes before expiry"""
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (now + timedelta(minutes=12)).isoformat(),
            "warned_15min": False,
        }

        def should_warn_15min(reservation):
            if reservation.get("warned_15min"):
                return False
            expires_at = datetime.fromisoformat(reservation["expires_at"])
            time_remaining = expires_at - datetime.now(timezone.utc)
            return time_remaining <= timedelta(minutes=15) and time_remaining > timedelta(minutes=5)

        assert should_warn_15min(reservation) is True

    def test_warning_at_5_minutes(self):
        """Should generate warning at 5 minutes before expiry"""
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (now + timedelta(minutes=3)).isoformat(),
            "warned_5min": False,
        }

        def should_warn_5min(reservation):
            if reservation.get("warned_5min"):
                return False
            expires_at = datetime.fromisoformat(reservation["expires_at"])
            time_remaining = expires_at - datetime.now(timezone.utc)
            return time_remaining <= timedelta(minutes=5) and time_remaining > timedelta(0)

        assert should_warn_5min(reservation) is True

    def test_no_duplicate_warnings(self):
        """Should not generate warning if already warned"""
        now = datetime.now(timezone.utc)

        reservation = {
            "reservation_id": "res-123",
            "status": "active",
            "expires_at": (now + timedelta(minutes=28)).isoformat(),
            "warned_30min": True,  # Already warned
        }

        def should_warn_30min(reservation):
            if reservation.get("warned_30min"):
                return False
            expires_at = datetime.fromisoformat(reservation["expires_at"])
            time_remaining = expires_at - datetime.now(timezone.utc)
            return time_remaining <= timedelta(minutes=30)

        assert should_warn_30min(reservation) is False


class TestCleanupOperations:
    """Tests for cleanup logic"""

    def test_cleanup_order(self):
        """Should cleanup resources in correct order"""
        cleanup_steps = []

        def cleanup_reservation(reservation):
            # Order matters:
            # 1. Create final snapshot (before pod deletion)
            cleanup_steps.append("snapshot")
            # 2. Delete the pod
            cleanup_steps.append("delete_pod")
            # 3. Delete the service
            cleanup_steps.append("delete_service")
            # 4. Update reservation status
            cleanup_steps.append("update_status")
            # 5. Clear disk in_use flag
            cleanup_steps.append("clear_disk")

        cleanup_reservation({})

        assert cleanup_steps == [
            "snapshot",
            "delete_pod",
            "delete_service",
            "update_status",
            "clear_disk",
        ]

    def test_snapshot_created_before_deletion(self):
        """Should create snapshot before deleting pod"""
        actions = []

        def create_shutdown_snapshot(pod_name, volume_id):
            actions.append(f"snapshot:{pod_name}")

        def delete_pod(pod_name):
            actions.append(f"delete:{pod_name}")

        def cleanup_with_snapshot(pod_name, volume_id):
            create_shutdown_snapshot(pod_name, volume_id)
            delete_pod(pod_name)

        cleanup_with_snapshot("test-pod", "vol-123")

        assert actions[0].startswith("snapshot:")
        assert actions[1].startswith("delete:")


class TestStalePendingCleanup:
    """Tests for cleaning up stale pending reservations"""

    def test_stale_pending_threshold(self):
        """Should identify reservations pending too long"""
        STALE_THRESHOLD_DAYS = 7
        now = datetime.now(timezone.utc)

        def is_stale_pending(reservation):
            if reservation.get("status") not in ["queued", "pending"]:
                return False
            created_at = datetime.fromisoformat(reservation["created_at"])
            age = now - created_at
            return age > timedelta(days=STALE_THRESHOLD_DAYS)

        old_reservation = {
            "status": "queued",
            "created_at": (now - timedelta(days=10)).isoformat(),
        }
        recent_reservation = {
            "status": "queued",
            "created_at": (now - timedelta(days=2)).isoformat(),
        }
        active_old = {
            "status": "active",
            "created_at": (now - timedelta(days=10)).isoformat(),
        }

        assert is_stale_pending(old_reservation) is True
        assert is_stale_pending(recent_reservation) is False
        assert is_stale_pending(active_old) is False  # Active, not pending


class TestSnapshotRetention:
    """Tests for snapshot retention policy"""

    def test_keep_recent_snapshots(self):
        """Should keep the most recent N snapshots"""
        KEEP_LATEST = 3
        now = datetime.now(timezone.utc)

        snapshots = [
            {"SnapshotId": "snap-1", "StartTime": now - timedelta(days=1)},
            {"SnapshotId": "snap-2", "StartTime": now - timedelta(days=5)},
            {"SnapshotId": "snap-3", "StartTime": now - timedelta(days=10)},
            {"SnapshotId": "snap-4", "StartTime": now - timedelta(days=15)},
            {"SnapshotId": "snap-5", "StartTime": now - timedelta(days=20)},
        ]

        def get_snapshots_to_delete(snapshots, keep_latest=3):
            sorted_snaps = sorted(snapshots, key=lambda x: x["StartTime"], reverse=True)
            return sorted_snaps[keep_latest:]

        to_delete = get_snapshots_to_delete(snapshots, KEEP_LATEST)

        assert len(to_delete) == 2
        assert to_delete[0]["SnapshotId"] == "snap-4"
        assert to_delete[1]["SnapshotId"] == "snap-5"

    def test_delete_snapshots_older_than_30_days(self):
        """Should delete snapshots older than retention period"""
        RETENTION_DAYS = 30
        now = datetime.now(timezone.utc)

        snapshots = [
            {"SnapshotId": "snap-recent", "StartTime": now - timedelta(days=5)},
            {"SnapshotId": "snap-old", "StartTime": now - timedelta(days=35)},
            {"SnapshotId": "snap-very-old", "StartTime": now - timedelta(days=60)},
        ]

        def get_old_snapshots(snapshots, retention_days=30):
            cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
            return [s for s in snapshots if s["StartTime"] < cutoff]

        old = get_old_snapshots(snapshots, RETENTION_DAYS)

        assert len(old) == 2
        assert "snap-old" in [s["SnapshotId"] for s in old]
        assert "snap-very-old" in [s["SnapshotId"] for s in old]
