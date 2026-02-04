# Cloud-Agnostic Architecture Progress

## Overview

This document tracks the progress of making ODC (Open Developer Cloud) cloud-agnostic, supporting AWS, GCP, and custom deployments.

---

## Section 1: Current State (Dev Branch)

The dev branch has successfully migrated from AWS Lambda/DynamoDB to a Kubernetes-native architecture:

| Component | Old (main) | New (dev) | Status |
|-----------|------------|-----------|--------|
| Job Queue | AWS SQS | PostgreSQL PGMQ | ✅ Done |
| State Store | DynamoDB | PostgreSQL | ✅ Done |
| Reservation Processing | Lambda | K8s Pod (processor) | ✅ Done |
| Availability Updates | Lambda | K8s CronJob | ✅ Done |
| Expiry Handling | Lambda | K8s CronJob | ✅ Done |
| API Service | Lambda + API Gateway | FastAPI in K8s | ✅ Done |

### Architecture Diagram

```
┌─────────────┐     ┌──────────────────────────────────────────────────────┐
│   CLI       │────▶│              API Service (FastAPI)                    │
│  (gpu-dev)  │     │  - AWS IAM Auth (to be replaced with OIDC)           │
└─────────────┘     └──────────────────┬───────────────────────────────────┘
                                       │
                                       ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                         PostgreSQL + PGMQ                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │
│  │reservations │  │   disks     │  │ api_users   │  │ pgmq queues │     │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘     │
└──────────────────────────────────────┬───────────────────────────────────┘
                                       │
            ┌──────────────────────────┼──────────────────────────┐
            ▼                          ▼                          ▼
┌───────────────────┐    ┌───────────────────┐    ┌───────────────────┐
│ Reservation       │    │ Availability      │    │ Expiry            │
│ Processor Pod     │    │ Updater CronJob   │    │ Handler CronJob   │
│ - Polls PGMQ      │    │ - Updates GPU     │    │ - Sends warnings  │
│ - Creates pods    │    │   availability    │    │ - Expires old res │
│ - Manages disks   │    │                   │    │ - Creates snapshots│
└───────────────────┘    └───────────────────┘    └───────────────────┘
```

---

## Section 2: AWS-Specific Dependencies

### Block Storage (EBS) - HIGH PRIORITY

| File | AWS Dependency | Lines |
|------|----------------|-------|
| `shared/disk_reconciler.py` | EC2 volume listing, tagging, snapshot operations | ~800 |
| `shared/snapshot_utils.py` | `ec2_client.create_snapshot()`, `describe_snapshots()` | ~200 |
| `reservation_handler.py` | Direct EBS volume attachment, cross-AZ migration | ~400 |
| `expiry/main.py` | EC2 snapshot tagging | ~50 |
| `database/schema/003_disks.sql` | `ebs_volume_id` column | Schema |

**Current Flow:**
```python
# Pod spec uses direct EBS attachment (NOT PVC!)
client.V1Volume(
    name="dev-home",
    aws_elastic_block_store=client.V1AWSElasticBlockStoreVolumeSource(
        volume_id=ebs_volume_id,
        fs_type="ext4"
    )
)
```

**Target Flow (CSI-based):**
```python
# Use PersistentVolumeClaim instead
client.V1Volume(
    name="dev-home",
    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
        claim_name=f"gpu-dev-{user_id}-{disk_name}"
    )
)
```

### Snapshots - HIGH PRIORITY

| Current (AWS) | Target (K8s Native) |
|---------------|---------------------|
| `ec2_client.create_snapshot(VolumeId)` | `VolumeSnapshot` CR |
| `ec2_client.describe_snapshots()` | `kubectl get volumesnapshots` |
| `ec2_client.delete_snapshot()` | `kubectl delete volumesnapshot` |
| Wait via `get_waiter("snapshot_completed")` | Watch VolumeSnapshot status |

**Required Components:**
- Snapshot Controller (deploy as addon or standalone)
- VolumeSnapshotClass for each CSI driver
- Update all snapshot_utils.py to use K8s API

### File Storage (EFS) - MEDIUM PRIORITY

| File | AWS Dependency |
|------|----------------|
| `efs.tf` | EFS resources, mount targets |
| `reservation_handler.py` | `create_or_find_user_efs()`, EFS client API |

**Options:**
1. **EFS CSI Driver** - AWS-specific but uses K8s primitives
2. **Generic NFS CSI** - Cloud-agnostic, works with any NFS
3. **GCP Filestore CSI** - GCP equivalent

### DNS (Route53) - LOW PRIORITY

| File | AWS Dependency |
|------|----------------|
| `shared/dns_utils.py` | Route53 record management |
| `route53.tf` | Hosted zone configuration |

**Target:** Use `external-dns` controller with annotations

### Authentication (IAM/STS) - HIGH PRIORITY

| File | AWS Dependency |
|------|----------------|
| `api-service/app/main.py` | STS `get_caller_identity()` verification |
| `cli-tools/gpu-dev-cli/gpu_dev_cli/auth.py` | AWS credentials for API auth |

**Target:** OIDC-based authentication (see Section 4)

### Container Registry (ECR) - LOW PRIORITY

| Current | Options |
|---------|---------|
| ECR with pull-through cache | GHCR (works everywhere) |
| | In-cluster registry with pull-through |
| | GCP Artifact Registry |

---

## Section 3: Provider Interface Design

### Directory Structure

```
terraform-gpu-devservers/
├── providers/
│   ├── __init__.py          # Provider factory
│   ├── base.py              # Abstract interfaces
│   ├── aws.py               # AWS implementation
│   ├── gcp.py               # GCP implementation
│   └── custom.py            # Template for custom providers
```

### Base Interface

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

@dataclass
class VolumeInfo:
    volume_id: str
    size_gb: int
    availability_zone: str
    status: str
    tags: Dict[str, str]

@dataclass
class SnapshotInfo:
    snapshot_id: str
    volume_id: str
    status: str
    size_gb: int
    created_at: str
    tags: Dict[str, str]

class CloudProvider(ABC):
    @abstractmethod
    def name(self) -> str:
        """Provider name (aws, gcp, custom)"""
        pass

    # === Block Storage ===
    @abstractmethod
    def create_volume(self, size_gb: int, availability_zone: str,
                      volume_type: str = "ssd", tags: Dict[str, str] = None,
                      snapshot_id: Optional[str] = None) -> VolumeInfo:
        pass

    @abstractmethod
    def delete_volume(self, volume_id: str) -> bool:
        pass

    @abstractmethod
    def attach_volume(self, volume_id: str, instance_id: str,
                      device_path: str) -> bool:
        pass

    @abstractmethod
    def detach_volume(self, volume_id: str) -> bool:
        pass

    # === Snapshots ===
    @abstractmethod
    def create_snapshot(self, volume_id: str, description: str = "",
                        tags: Dict[str, str] = None) -> SnapshotInfo:
        pass

    @abstractmethod
    def delete_snapshot(self, snapshot_id: str) -> bool:
        pass

    @abstractmethod
    def list_snapshots(self, filters: Dict[str, str] = None) -> List[SnapshotInfo]:
        pass

    @abstractmethod
    def wait_for_snapshot(self, snapshot_id: str,
                          timeout_seconds: int = 600) -> bool:
        pass

    # === Object Storage ===
    @abstractmethod
    def upload_to_object_storage(self, bucket: str, key: str,
                                  content: bytes) -> str:
        pass

    @abstractmethod
    def download_from_object_storage(self, bucket: str,
                                      key: str) -> Optional[bytes]:
        pass


class AuthProvider(ABC):
    @abstractmethod
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify auth token, return user info or None"""
        pass

    @abstractmethod
    def create_api_key(self, user_id: str, scopes: List[str],
                       ttl_hours: int = 24) -> str:
        pass
```

### Provider Factory

```python
import os
from typing import Optional
from .base import CloudProvider

_provider_instance: Optional[CloudProvider] = None

def get_cloud_provider() -> CloudProvider:
    global _provider_instance
    if _provider_instance is not None:
        return _provider_instance

    provider_name = os.environ.get("CLOUD_PROVIDER", "aws").lower()

    if provider_name == "aws":
        from .aws import AWSProvider
        _provider_instance = AWSProvider(
            region=os.environ.get("AWS_REGION", "us-east-2")
        )
    elif provider_name == "gcp":
        from .gcp import GCPProvider
        _provider_instance = GCPProvider(
            project=os.environ.get("GCP_PROJECT"),
            zone=os.environ.get("GCP_ZONE", "us-central1-a")
        )
    elif provider_name == "custom":
        from .custom import CustomProvider
        _provider_instance = CustomProvider()
    else:
        raise ValueError(f"Unknown cloud provider: {provider_name}")

    return _provider_instance
```

---

## Section 4: OIDC Authentication Design

### Current Flow (AWS IAM)

```
CLI -> AWS Credentials -> API Service -> STS Verify -> Issue API Key
```

### Target Flow (OIDC)

```
┌──────────┐     ┌──────────┐     ┌─────────────┐     ┌──────────┐
│  User    │────▶│  OIDC    │────▶│ API Service │────▶│ Resource │
│  (CLI)   │     │ Provider │     │ (validates) │     │ Creation │
└──────────┘     └──────────┘     └─────────────┘     └──────────┘
                                         │
                                         ▼
                                  ┌─────────────┐
                                  │ audit_log   │
                                  │ (traceable) │
                                  └─────────────┘
```

### Database Changes

```sql
-- Add OIDC fields to api_users
ALTER TABLE api_users ADD COLUMN oidc_subject VARCHAR(255);
ALTER TABLE api_users ADD COLUMN oidc_issuer VARCHAR(512);
ALTER TABLE api_users ADD COLUMN oidc_claims JSONB;

-- Audit log for traceability (including Bedrock/Claude usage)
CREATE TABLE audit_log (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    user_id INTEGER REFERENCES api_users(id),
    username VARCHAR(255),
    action VARCHAR(100),  -- reserve, cancel, extend, etc.
    resource_type VARCHAR(50),
    resource_id VARCHAR(255),
    request_metadata JSONB,
    bedrock_request_id VARCHAR(255),
    bedrock_tokens_used INTEGER
);
```

### OIDC Provider Options

| Provider | Pros | Cons |
|----------|------|------|
| **GitHub** | Devs have accounts, matches SSH key auth | Limited enterprise features |
| **Google** | Universal, easy setup | May not fit enterprise policy |
| **Okta/Auth0** | Enterprise features, MFA | Cost, complexity |
| **Dex** | Self-hosted, multi-provider | Operational overhead |

**Recommendation:** Start with GitHub OIDC (matches existing SSH key auth pattern), add enterprise options later.

---

## Section 5: Migration Phases

### Phase 1: Abstraction Layer (Days 1-2) - LOW RISK

- [ ] Create `providers/` directory structure
- [ ] Implement `CloudProvider` base class
- [ ] Implement `AWSProvider` wrapping existing boto3 calls
- [ ] Add `get_cloud_provider()` factory
- [ ] **No production changes yet**

### Phase 2: Refactor Storage Code (Days 3-7) - MEDIUM RISK

- [ ] Refactor `shared/snapshot_utils.py` to use provider interface
- [ ] Refactor `reservation_handler.py` volume operations
- [ ] Refactor `shared/disk_reconciler.py`
- [ ] Update `expiry/main.py` snapshot operations
- [ ] Add comprehensive tests

### Phase 3: K8s-Native Storage (Week 2) - MEDIUM RISK

- [ ] Deploy Snapshot Controller
- [ ] Create VolumeSnapshotClass resources
- [ ] Add `K8sStorageProvider` implementation
- [ ] Support PVC-based volume attachment
- [ ] Make storage backend configurable

### Phase 4: GCP Provider (Weeks 3-4) - MEDIUM-HIGH RISK

- [ ] Implement `GCPProvider` class
- [ ] Create Terraform modules for GKE
- [ ] GCE Persistent Disk operations
- [ ] GCP Filestore for shared storage
- [ ] End-to-end testing on GCP

### Phase 5: OIDC Authentication (Week 5) - HIGH RISK

- [ ] Add OIDC token verification to API service
- [ ] Create user mapping (OIDC subject -> internal user)
- [ ] Add audit logging with full traceability
- [ ] Update CLI for OIDC login flow
- [ ] Dual-auth period (AWS IAM + OIDC)

### Phase 6: DNS and Load Balancing (Week 6) - LOW-MEDIUM RISK

- [ ] Deploy external-dns controller
- [ ] Replace Route53 calls with K8s annotations
- [ ] Make DNS optional/configurable
- [ ] Document DNS-free deployment option

---

## Section 6: Files to Modify

### Storage Abstraction

| File | Changes |
|------|---------|
| `shared/disk_reconciler.py` | Replace EC2 API with provider interface |
| `shared/snapshot_utils.py` | Replace EC2 snapshot calls with provider/K8s API |
| `shared/disk_db.py` | Add `pvc_name`, `storage_class` columns |
| `reservation_handler.py` | Use PVC-based volumes |
| `expiry/main.py` | CSI-based snapshot cleanup |
| `database/schema/003_disks.sql` | Add PVC columns |
| `eks.tf` | Add snapshot controller addon |
| `monitoring.tf` | Add VolumeSnapshotClass resources |

### Authentication

| File | Changes |
|------|---------|
| `api-service/app/main.py` | Add OIDC verification endpoint |
| `cli-tools/gpu-dev-cli/gpu_dev_cli/auth.py` | OIDC login flow |
| `database/schema/` | Add audit_log table, OIDC fields |

### DNS

| File | Changes |
|------|---------|
| `shared/dns_utils.py` | Make optional, add external-dns option |
| `route53.tf` | Make conditional |

---

## Section 7: Open Questions

### Authentication
1. Which OIDC provider(s) to support initially?
2. How to handle AWS IAM → OIDC transition?
3. What user attributes needed from OIDC claims?
4. How to trace Bedrock/Claude token usage to users?

### Storage
5. Acceptable latency for snapshot operations?
6. Should we support both direct EBS and PVC modes?

### Registry
7. Single registry (GHCR) or multi-region?
8. How to handle custom images built per reservation?

### General
9. Is external-dns required or optional?
10. Should provider interface be a separate package?
11. Priority: AWS improvements vs. multi-cloud?

---

## Appendix: Key File References

### Storage
- `terraform-gpu-devservers/shared/snapshot_utils.py`
- `terraform-gpu-devservers/shared/disk_reconciler.py`
- `terraform-gpu-devservers/shared/disk_db.py`
- `terraform-gpu-devservers/reservation-processor-service/processor/reservation_handler.py`

### Authentication
- `terraform-gpu-devservers/api-service/app/main.py`
- `cli-tools/gpu-dev-cli/gpu_dev_cli/auth.py`

### Database
- `terraform-gpu-devservers/database/schema/002_reservations.sql`
- `terraform-gpu-devservers/database/schema/003_disks.sql`

### Infrastructure
- `terraform-gpu-devservers/eks.tf`
- `terraform-gpu-devservers/efs.tf`
- `terraform-gpu-devservers/monitoring.tf`
