"""
Shared utilities for GPU development server services
"""

# Database connection pool utilities
from .db_pool import (
    get_db_cursor,
    get_db_transaction,
    get_db_connection,
    init_connection_pool,
    close_connection_pool,
    get_pool_stats,
    ConnectionPoolExhaustedError,
    ConnectionHealthCheckError
)

# Kubernetes client utilities
from .k8s_client import get_bearer_token, setup_kubernetes_client
from .k8s_resource_tracker import K8sGPUTracker

# ALB/NLB utilities
from .alb_utils import (
    is_alb_enabled,
    create_jupyter_target_group,
    create_listener_rule,
    store_alb_mapping,
    delete_alb_mapping
)

# DNS utilities
from .dns_utils import (
    generate_unique_name,
    create_dns_record,
    delete_dns_record,
    store_domain_mapping,
    delete_domain_mapping,
    get_existing_dns_names
)

# Snapshot utilities
from .snapshot_utils import (
    safe_create_snapshot,
    update_disk_snapshot_completed
)

# Reservation database utilities
from .reservation_db import (
    create_reservation,
    get_reservation,
    update_reservation,
    delete_reservation,
    list_reservations_by_user,
    list_reservations_by_status,
    append_status_history,
    list_multinode_reservations,
    count_active_reservations_by_gpu_type,
    list_expired_reservations,
    update_reservation_status
)

# Disk database utilities
from .disk_db import (
    create_disk,
    get_disk,
    get_disk_by_id,
    update_disk,
    delete_disk,
    list_disks_by_user,
    mark_disk_in_use,
    mark_disk_deleted,
    get_disks_in_use,
    get_disks_pending_deletion,
    update_disk_operation
)

__all__ = [
    # Database pool
    "get_db_cursor",
    "get_db_transaction",
    "get_db_connection",
    "init_connection_pool",
    "close_connection_pool",
    "get_pool_stats",
    "ConnectionPoolExhaustedError",
    "ConnectionHealthCheckError",
    # Kubernetes
    "setup_kubernetes_client",
    "get_bearer_token",
    "K8sGPUTracker",
    # ALB
    "is_alb_enabled",
    "create_jupyter_target_group",
    "create_listener_rule",
    "store_alb_mapping",
    "delete_alb_mapping",
    # DNS
    "generate_unique_name",
    "create_dns_record",
    "delete_dns_record",
    "store_domain_mapping",
    "delete_domain_mapping",
    "get_existing_dns_names",
    # Snapshots
    "safe_create_snapshot",
    "update_disk_snapshot_completed",
    # Reservations
    "create_reservation",
    "get_reservation",
    "update_reservation",
    "delete_reservation",
    "list_reservations_by_user",
    "list_reservations_by_status",
    "append_status_history",
    "list_multinode_reservations",
    "count_active_reservations_by_gpu_type",
    "list_expired_reservations",
    "update_reservation_status",
    # Disks
    "create_disk",
    "get_disk",
    "get_disk_by_id",
    "update_disk",
    "delete_disk",
    "list_disks_by_user",
    "mark_disk_in_use",
    "mark_disk_deleted",
    "get_disks_in_use",
    "get_disks_pending_deletion",
    "update_disk_operation",
]
