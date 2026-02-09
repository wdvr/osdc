# Multi-Cloud Architecture Progress

> Last Updated: 2025-02-09
> Status: Phase 2 Complete, Helm Chart Added

## Overview

This document tracks the progress of making the GPU Dev Server infrastructure cloud-agnostic, supporting AWS, GCP, and custom Kubernetes deployments.

---

## Architecture Summary

### Before (AWS-Only)
```
┌─────────────────────────────────────────────────────────┐
│                    OpenTofu (AWS)                       │
│  - EKS Cluster                                          │
│  - VPC/Networking                                       │
│  - IAM Roles (IRSA)                                     │
│  - All K8s Resources (hardcoded in .tf files)          │
│  - AWS-specific services (EBS, EFS, CloudFront)        │
└─────────────────────────────────────────────────────────┘
```

### After (Multi-Cloud)
```
┌─────────────────────────────────────────────────────────┐
│              Infrastructure Layer (OpenTofu)            │
│  AWS: EKS, VPC, IAM, EBS CSI, EFS                      │
│  GCP: GKE, VPC, Workload Identity, PD CSI, Filestore  │
│  Custom: Any K8s cluster with GPU support              │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Application Layer (Helm Chart)             │
│  - PostgreSQL + PGMQ (StatefulSets)                    │
│  - API Service (Deployment + LoadBalancer)             │
│  - Reservation Processor (Deployment)                   │
│  - Availability Updater (CronJob)                       │
│  - Reservation Expiry (CronJob)                         │
│  - Registry Caches (Deployments)                        │
│  - RBAC (ClusterRoles, ServiceAccounts)                │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│              Provider Abstraction (Python)              │
│  - CloudProvider interface                              │
│  - AWSProvider implementation                           │
│  - GCPProvider implementation (stub)                   │
│  - Storage operations (snapshots, volumes)             │
│  - Compute operations (instances, availability)        │
└─────────────────────────────────────────────────────────┘
```

---

## Completed Work

### Phase 1: Provider Abstraction (PR #28)

**Branch:** `feat/cloud-abstraction-tests`
**PR:** https://github.com/wdvr/osdc/pull/28
**Status:** Open, Ready for Review

#### Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `cli-tools/gpu-dev-cli/gpu_dev/providers/__init__.py` | 5 | Package exports |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/base.py` | 180 | Abstract `CloudProvider` interface |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/aws.py` | 250 | AWS implementation |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/gcp.py` | 50 | GCP stub implementation |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/factory.py` | 45 | Provider factory pattern |

#### CloudProvider Interface

```python
class CloudProvider(ABC):
    """Abstract base class for cloud provider implementations."""

    # Storage Operations
    @abstractmethod
    def create_snapshot(self, volume_id: str, tags: dict) -> str: ...
    @abstractmethod
    def delete_snapshot(self, snapshot_id: str) -> bool: ...
    @abstractmethod
    def get_snapshot(self, snapshot_id: str) -> Optional[dict]: ...
    @abstractmethod
    def list_snapshots(self, filters: dict) -> List[dict]: ...
    @abstractmethod
    def create_volume_from_snapshot(self, snapshot_id: str, ...) -> str: ...
    @abstractmethod
    def delete_volume(self, volume_id: str) -> bool: ...
    @abstractmethod
    def get_volume(self, volume_id: str) -> Optional[dict]: ...

    # Compute Operations
    @abstractmethod
    def get_instance(self, instance_id: str) -> Optional[dict]: ...
    @abstractmethod
    def list_instances(self, filters: dict) -> List[dict]: ...
    @abstractmethod
    def get_availability_zones(self) -> List[str]: ...

    # Identity Operations
    @abstractmethod
    def get_caller_identity(self) -> dict: ...
    @abstractmethod
    def verify_credentials(self, credentials: dict) -> dict: ...
```

#### Unit Tests Created

| Test File | Tests | Coverage |
|-----------|-------|----------|
| `tests/unit/cli/test_auth.py` | 8 | Authentication, SSH key validation |
| `tests/unit/cli/test_availability.py` | 13 | GPU availability, cluster status |
| `tests/unit/cli/test_cancel.py` | 9 | Reservation cancellation |
| `tests/unit/cli/test_config.py` | 10 | Configuration management |
| `tests/unit/cli/test_connect_show.py` | 5 | SSH config, connection info |
| `tests/unit/cli/test_disks.py` | 12 | Disk CRUD operations |
| `tests/unit/cli/test_edit.py` | 10 | Reservation editing |
| `tests/unit/cli/test_reserve.py` | 20 | Reservation creation |

**Total: 87 unit tests**

---

### Phase 2: Storage Abstraction (PR #29)

**Branch:** `feat/phase2-storage-abstraction`
**PR:** https://github.com/wdvr/osdc/pull/29
**Status:** Open, Ready for Review

#### Changes

- Refactored `snapshot_utils.py` to use `CloudProvider` interface
- Removed direct boto3 calls from utility functions
- Added provider injection for testability

---

### Phase 3: Helm Chart (PR #30)

**Branch:** `feat/helm-chart`
**PR:** https://github.com/wdvr/osdc/pull/30
**Status:** Open, Ready for Review

#### Chart Structure

```
charts/gpu-dev-server/
├── Chart.yaml                    # Chart metadata, version 0.1.0
├── values.yaml                   # Default cloud-agnostic values
├── values-aws.yaml               # AWS/EKS with IRSA annotations
├── values-gcp.yaml               # GCP/GKE with Workload Identity
├── README.md                     # Usage documentation
└── templates/
    ├── _helpers.tpl              # Template helper functions
    ├── namespaces.yaml           # gpu-controlplane, gpu-dev
    ├── storage-class.yaml        # AWS gp3, GCP pd-ssd
    ├── database-migration-job.yaml  # Helm hook for schema
    │
    ├── postgres/
    │   ├── statefulset-primary.yaml
    │   ├── statefulset-replica.yaml
    │   ├── service.yaml
    │   ├── configmap.yaml
    │   └── secret.yaml
    │
    ├── api-service/
    │   ├── deployment.yaml
    │   ├── service.yaml          # ClusterIP + LoadBalancer
    │   ├── configmap.yaml
    │   └── serviceaccount.yaml
    │
    ├── reservation-processor/
    │   ├── deployment.yaml
    │   ├── configmap.yaml
    │   ├── serviceaccount.yaml
    │   └── rbac.yaml             # ClusterRole + Binding
    │
    ├── availability-updater/
    │   ├── cronjob.yaml
    │   ├── configmap.yaml
    │   ├── serviceaccount.yaml
    │   └── rbac.yaml
    │
    ├── reservation-expiry/
    │   ├── cronjob.yaml
    │   ├── configmap.yaml
    │   ├── serviceaccount.yaml
    │   └── rbac.yaml
    │
    └── registry/
        ├── deployment-native.yaml
        ├── deployment-ghcr.yaml
        └── secret.yaml
```

#### Key Features

1. **Cloud-Agnostic Base Values**
   - All K8s resources templated
   - No hardcoded cloud-specific values
   - Configurable via values files

2. **AWS Support (values-aws.yaml)**
   - IRSA service account annotations
   - gp3 storage class
   - EKS cluster name configuration

3. **GCP Support (values-gcp.yaml)**
   - Workload Identity annotations
   - pd-ssd storage class
   - GKE cluster name configuration

4. **Database Migration**
   - Runs as Helm post-install/post-upgrade hook
   - Creates PGMQ extension
   - Creates all required tables
   - Idempotent (safe to run multiple times)

5. **Configurable Components**
   - Enable/disable individual services
   - Configure replicas, resources, tolerations
   - External secrets support

#### Installation Examples

```bash
# AWS
helm install gpu-dev ./charts/gpu-dev-server \
  -f charts/gpu-dev-server/values-aws.yaml \
  --set postgres.auth.password=secure123 \
  --set cloudProvider.aws.eksClusterName=my-cluster \
  --set apiService.serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=arn:aws:iam::123:role/api-role

# GCP
helm install gpu-dev ./charts/gpu-dev-server \
  -f charts/gpu-dev-server/values-gcp.yaml \
  --set postgres.auth.password=secure123 \
  --set cloudProvider.gcp.gkeClusterName=my-cluster \
  --set cloudProvider.gcp.projectId=my-project
```

---

## What Stays in OpenTofu

The Helm chart manages **K8s resources only**. Infrastructure still requires OpenTofu:

| Component | AWS | GCP | Notes |
|-----------|-----|-----|-------|
| Kubernetes Cluster | EKS | GKE | Cluster creation, node groups |
| Networking | VPC, Subnets, SGs | VPC, Subnets, Firewall | Network topology |
| IAM/Identity | IAM Roles, IRSA | Service Accounts, WI | Cloud identity |
| Block Storage | EBS CSI Driver | PD CSI Driver | Storage drivers |
| Shared Storage | EFS | Filestore | Shared filesystems |
| Load Balancer | ALB/NLB/CLB | Cloud LB | External access |
| DNS/CDN | Route53, CloudFront | Cloud DNS, Cloud CDN | Domain management |
| Container Registry | ECR | Artifact Registry | Image storage |

---

## PR Dependency Chain

```
main
  │
  └── dev (base for feature work)
        │
        ├── PR #28: Cloud abstraction + tests
        │     └── Adds: providers/, 87 tests
        │
        ├── PR #29: Storage abstraction (depends on #28 conceptually)
        │     └── Refactors: snapshot_utils.py
        │
        └── PR #30: Helm chart (independent)
              └── Adds: charts/gpu-dev-server/
```

**Merge Order:**
1. PR #28 → dev
2. PR #29 → dev
3. PR #30 → dev
4. dev → main (via PR #22)

---

## Testing Instructions

### Unit Tests
```bash
# All tests
pytest tests/unit/ -v

# Specific test file
pytest tests/unit/cli/test_reserve.py -v

# With coverage
pytest tests/unit/ --cov=gpu_dev --cov-report=html
```

### Helm Chart Validation
```bash
# Template rendering
helm template gpu-dev ./charts/gpu-dev-server \
  --set postgres.auth.password=test

# Dry-run install
helm install gpu-dev ./charts/gpu-dev-server \
  --dry-run --debug \
  -f charts/gpu-dev-server/values-aws.yaml

# Lint
helm lint ./charts/gpu-dev-server
```

### Local Testing (k3d/kind)
```bash
# Create local cluster
k3d cluster create gpu-test

# Install chart
helm install gpu-dev ./charts/gpu-dev-server \
  --set postgres.auth.password=test123 \
  --set nvidia.devicePlugin.enabled=false

# Verify
kubectl get pods -n gpu-controlplane
```

---

## Remaining Work

### High Priority
- [ ] Implement full GCPProvider (currently stub)
- [ ] Add integration tests for provider abstraction
- [ ] Test Helm chart on real EKS cluster
- [ ] Test Helm chart on real GKE cluster
- [ ] Update CLI to use provider factory

### Medium Priority
- [ ] Add Azure provider support
- [ ] Create Terraform module for GCP infrastructure
- [ ] Add Helm chart CI/CD pipeline
- [ ] Create migration guide from OpenTofu-only to Helm

### Low Priority
- [ ] Add Prometheus/Grafana Helm subchart
- [ ] Create Helm chart tests (helm unittest)
- [ ] Add chart to Helm repository
- [ ] Create one-click deployment scripts

---

## File Inventory

### New Files (This Work)

| Path | Lines | Type |
|------|-------|------|
| `cli-tools/gpu-dev-cli/gpu_dev/providers/__init__.py` | 5 | Python |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/base.py` | 180 | Python |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/aws.py` | 250 | Python |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/gcp.py` | 50 | Python |
| `cli-tools/gpu-dev-cli/gpu_dev/providers/factory.py` | 45 | Python |
| `tests/unit/cli/test_auth.py` | 150 | Python |
| `tests/unit/cli/test_availability.py` | 200 | Python |
| `tests/unit/cli/test_cancel.py` | 180 | Python |
| `tests/unit/cli/test_config.py` | 170 | Python |
| `tests/unit/cli/test_connect_show.py` | 120 | Python |
| `tests/unit/cli/test_disks.py` | 220 | Python |
| `tests/unit/cli/test_edit.py` | 180 | Python |
| `tests/unit/cli/test_reserve.py` | 350 | Python |
| `charts/gpu-dev-server/Chart.yaml` | 20 | YAML |
| `charts/gpu-dev-server/values.yaml` | 200 | YAML |
| `charts/gpu-dev-server/values-aws.yaml` | 40 | YAML |
| `charts/gpu-dev-server/values-gcp.yaml` | 45 | YAML |
| `charts/gpu-dev-server/README.md` | 180 | Markdown |
| `charts/gpu-dev-server/templates/*.yaml` | 1500 | YAML |

**Total: ~4,000+ lines of new code**

---

## Changelog

### 2025-02-09
- Created Helm chart (PR #30)
- Added AWS and GCP values files
- Created database migration Helm hook
- Added chart README with usage examples

### 2025-02-08
- Created CloudProvider abstraction (PR #28)
- Implemented AWSProvider
- Created GCPProvider stub
- Added 87 unit tests
- Refactored snapshot_utils.py (PR #29)
