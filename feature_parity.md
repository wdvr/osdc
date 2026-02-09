# GPU Dev Server Feature Parity Review

**Date:** 2025-02-09
**Comparison:** OpenTofu Infrastructure (`terraform-gpu-devservers/`) vs Helm Chart (`charts/gpu-dev-server/`)

## Executive Summary

The Helm chart implementation provides a solid foundation for cloud-agnostic deployment of the GPU Dev Server infrastructure. However, there are several significant gaps compared to the original OpenTofu implementation that need to be addressed before production readiness.

**Overall Status:**
- Core Services: ~85% parity
- RBAC/Security: ~90% parity
- Storage: ~70% parity
- Monitoring: 0% parity (completely missing)
- AWS-specific Features: ~40% parity
- Multi-cloud Support: Basic structure present, needs completion

---

## Feature Comparison Matrix

| Component | OpenTofu | Helm Chart | Parity | Priority |
|-----------|----------|------------|--------|----------|
| **Namespaces** | | | | |
| gpu-controlplane namespace | Yes | Yes | 100% | - |
| gpu-dev namespace | Yes | Yes | 100% | - |
| **PostgreSQL** | | | | |
| Primary StatefulSet | Yes | Yes | 90% | Medium |
| Replica StatefulSet | Yes | Yes | 85% | Medium |
| Primary Service (ClusterIP) | Yes | Yes | 100% | - |
| Replica Service (ClusterIP) | Yes | Yes | 100% | - |
| Headless Services | Yes | **No** | 0% | Medium |
| PostgreSQL ConfigMap (primary) | Yes | Yes | 90% | Low |
| PostgreSQL ConfigMap (replica) | Yes | Yes | 90% | Low |
| PostgreSQL Credentials Secret | Yes | Yes | 100% | - |
| Replication Credentials Secret | Yes | **No** | 0% | High |
| Init Script ConfigMap (PGMQ) | Yes | **Partial** | 60% | Medium |
| Service Account | Yes | **No** | 0% | Medium |
| Role/RoleBinding | Yes | **No** | 0% | Medium |
| **Database Migration** | | | | |
| Schema ConfigMap | Yes | **No** | 0% | Medium |
| Fixtures ConfigMap | Yes | **No** | 0% | Low |
| Migration Job | Yes | Yes | 80% | Low |
| **API Service** | | | | |
| Deployment | Yes | Yes | 95% | - |
| Internal Service (ClusterIP) | Yes | Yes | 100% | - |
| Public Service (LoadBalancer) | Yes | Yes | 100% | - |
| ConfigMap | Yes | Yes | 100% | - |
| ServiceAccount | Yes | Yes | 80% | Low |
| IAM Role (AWS IRSA) | Yes | **Placeholder** | 50% | High |
| **Reservation Processor** | | | | |
| Deployment | Yes | Yes | 95% | - |
| ConfigMap | Yes | Yes | 90% | Low |
| ServiceAccount | Yes | Yes | 80% | Low |
| ClusterRole | Yes | Yes | 100% | - |
| ClusterRoleBinding | Yes | Yes | 100% | - |
| IAM Role (AWS IRSA) | Yes | **Placeholder** | 50% | High |
| **Availability Updater** | | | | |
| CronJob | Yes | Yes | 90% | Low |
| ConfigMap | Yes | Yes | 80% | Low |
| ServiceAccount | Yes | Yes | 80% | Low |
| ClusterRole | Yes | Yes | 100% | - |
| ClusterRoleBinding | Yes | Yes | 100% | - |
| IAM Role (AWS IRSA) | Yes | **Placeholder** | 50% | High |
| Tolerations | Yes | **No** | 0% | Medium |
| **Reservation Expiry** | | | | |
| CronJob | Yes | Yes | 90% | Low |
| ConfigMap | Yes | Yes | 80% | Low |
| ServiceAccount | Yes | Yes | 80% | Low |
| ClusterRole | Yes | Yes | 100% | - |
| ClusterRoleBinding | Yes | Yes | 100% | - |
| IAM Role (AWS IRSA) | Yes | **Placeholder** | 50% | High |
| **Registry Caches** | | | | |
| GHCR Pull-Through Cache | Yes | Yes | 85% | Low |
| Docker Hub Pull-Through Cache | Yes | **No** | 0% | Medium |
| Native In-Cluster Registry | Yes | Yes | 70% | Medium |
| TLS for Native Registry | Yes | **Partial** | 40% | Medium |
| LoadBalancer Services (NLB) | Yes | **No** | 0% | High |
| **Storage** | | | | |
| gp3 StorageClass | Yes | Yes | 100% | - |
| GCP StorageClass | N/A | Yes | 100% | - |
| **Monitoring** | | | | |
| kube-prometheus-stack | Yes | **No** | 0% | High |
| Grafana | Yes | **No** | 0% | High |
| GPU Overview Dashboard | Yes | **No** | 0% | High |
| K8s Storage Dashboard | Yes | **No** | 0% | Medium |
| DCGM Exporter integration | Yes | **No** | 0% | High |
| Grafana Cloud remote write | Yes | **No** | 0% | Low |
| **NVIDIA GPU Operator** | | | | |
| GPU Operator Helm Release | Yes | **Dependency** | 60% | Medium |
| DCGM Exporter | Yes | **No** | 0% | High |
| MIG Manager | Yes | **No** | 0% | Low |
| Device Plugin | Yes | Yes (dep) | 80% | - |
| **AWS-Specific** | | | | |
| EFS Security Group | Yes | **No** | 0% | Medium |
| EFS Shared ccache | Yes | **No** | 0% | Medium |
| CloudFront (HTTPS) | Yes | **No** | 0% | High |
| SSH Proxy (ECS Fargate) | Yes | **No** | 0% | Low |
| ALB for SSH/Jupyter | Yes | **No** | 0% | Low |
| Route53 DNS | Yes | **No** | 0% | Low |
| ECR Repositories | Yes | **N/A** | - | - |
| **EKS/Cluster** | | | | |
| aws-auth ConfigMap | Yes | **No** | 0% | Critical |
| OIDC Provider (for IRSA) | Yes | **N/A** | - | - |
| EFA Device Plugin | Yes | **No** | 0% | Medium |
| Image Pre-puller DaemonSet | Yes | **No** | 0% | Low |
| Profiling Node Labeler | Yes | **No** | 0% | Low |
| **Security** | | | | |
| PostgreSQL RBAC | Yes | **No** | 0% | Medium |
| fsGroup Security Context | Yes | **Partial** | 50% | Medium |
| Network Policies | **No** | **No** | N/A | Future |

---

## Missing Features - Detailed Analysis

### Critical Priority

#### 1. AWS Auth ConfigMap
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:12-37`

The `aws-auth` ConfigMap is essential for EKS node authentication. Without it, nodes cannot join the cluster.

```yaml
# Missing from Helm chart - CRITICAL for EKS
apiVersion: v1
kind: ConfigMap
metadata:
  name: aws-auth
  namespace: kube-system
data:
  mapRoles: |
    - rolearn: <EKS_NODE_ROLE_ARN>
      username: system:node:{{EC2PrivateDNSName}}
      groups:
        - system:bootstrappers
        - system:nodes
```

**Recommendation:** This should NOT be in the Helm chart but rather in the EKS/GKE cluster setup (separate Terraform/OpenTofu). Document this as a prerequisite.

### High Priority

#### 2. PostgreSQL Headless Services
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:802-830, 1073-1101`

Headless services are required for StatefulSet DNS resolution and proper PostgreSQL primary/replica communication.

**Missing Templates:**
- `charts/gpu-dev-server/templates/postgres/service-headless.yaml`

```yaml
# Primary Headless Service
apiVersion: v1
kind: Service
metadata:
  name: postgres-primary-headless
  namespace: {{ .Values.namespaces.controlplane }}
spec:
  type: ClusterIP
  clusterIP: None
  selector:
    app: postgres
    role: primary
  ports:
    - name: postgres
      port: 5432
      targetPort: 5432
```

#### 3. PostgreSQL Replication Credentials Secret
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:158-182`

The replication user credentials are needed for streaming replication between primary and replica.

**Missing from:** `charts/gpu-dev-server/templates/postgres/secret.yaml`

```yaml
# Should add to secret.yaml
REPLICATION_USER: {{ "replicator" | b64enc }}
REPLICATION_PASSWORD: {{ randAlphaNum 32 | b64enc }}
```

#### 4. Full Monitoring Stack
**OpenTofu File:** `terraform-gpu-devservers/monitoring.tf` (989 lines)

The entire monitoring stack is missing from the Helm chart:
- kube-prometheus-stack (Prometheus + Grafana)
- DCGM Exporter integration
- GPU Overview Dashboard
- K8s & Storage Dashboard

**Recommendation:** Add monitoring as a subchart dependency or create separate templates:
- `charts/gpu-dev-server/templates/monitoring/` directory
- Add `kube-prometheus-stack` as Chart.yaml dependency
- Create dashboard ConfigMaps

#### 5. CloudFront HTTPS Endpoint
**OpenTofu File:** `terraform-gpu-devservers/cloudfront.tf`

CloudFront provides HTTPS with free AWS-managed certificates. The Helm chart only creates a LoadBalancer which doesn't have HTTPS.

**Recommendation:** For AWS, document CloudFront setup as a post-install step. For GCP, document GCP HTTPS Load Balancer setup.

#### 6. Docker Hub Registry Cache
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:1445-1678`

The Docker Hub pull-through cache is missing, which helps avoid Docker Hub rate limits.

**Missing Template:** `charts/gpu-dev-server/templates/registry/deployment-dockerhub.yaml`

#### 7. Registry LoadBalancer Services
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:1409-1443, 1644-1678, 1914-1947`

The OpenTofu uses internal NLBs for registry access from nodes. The Helm chart only creates ClusterIP services.

**Issue Location:** `charts/gpu-dev-server/templates/registry/deployment-*.yaml`

### Medium Priority

#### 8. PostgreSQL Init Script ConfigMap
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:286-329`

The init script creates the replication user and PGMQ extension. The Helm chart's migration job handles some of this but lacks the replication user setup.

**Gap in:** `charts/gpu-dev-server/templates/database-migration-job.yaml`

#### 9. Native Registry TLS Configuration
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:1688-1705, 1706-1743`

TLS is partially implemented but the actual certificate management and config are incomplete.

**Missing:**
- TLS Secret generation/management
- Registry config.yml with TLS settings

#### 10. EFS Configuration
**OpenTofu File:** `terraform-gpu-devservers/efs.tf`

Shared storage via EFS for ccache and user data is AWS-specific but important for multi-node setups.

**Recommendation:** Add AWS-specific EFS values and document GCP Filestore alternative.

#### 11. EFA Device Plugin
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:2000-2116`

The EFA (Elastic Fabric Adapter) device plugin is needed for high-performance networking on GPU instances.

**Missing Template:** `charts/gpu-dev-server/templates/daemonsets/efa-device-plugin.yaml`

### Low Priority

#### 12. SSH Proxy (ECS Fargate)
**OpenTofu File:** `terraform-gpu-devservers/ssh-proxy-service.tf`

WebSocket-based SSH proxy running on ECS Fargate - very AWS-specific.

**Recommendation:** Document as optional AWS-specific feature, not in Helm chart.

#### 13. Image Pre-puller DaemonSet
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:2259-2341`

Pre-pulls GPU dev container images on all GPU nodes for faster startup.

**Recommendation:** Add as optional DaemonSet template.

#### 14. Profiling Node Labeler
**OpenTofu File:** `terraform-gpu-devservers/kubernetes.tf:2343-2472`

CronJob that labels nodes for NVIDIA Nsight profiling (disables DCGM).

**Recommendation:** Add as optional component, disabled by default.

---

## Implementation Gaps by File

### OpenTofu Files Not Represented in Helm

| OpenTofu File | Description | Action Required |
|---------------|-------------|-----------------|
| `monitoring.tf` | Full monitoring stack | Create monitoring templates or subchart |
| `cloudfront.tf` | HTTPS endpoint | Document as AWS post-install step |
| `ssh-proxy-service.tf` | SSH WebSocket proxy | Optional AWS feature (not in chart) |
| `alb.tf` | Application Load Balancer | Optional AWS feature (not in chart) |
| `route53.tf` | DNS management | Optional AWS feature (not in chart) |
| `docker-certs.tf` | Registry TLS certs | Implement cert management |
| `registry-public-access.tf` | Public registry access | Review if needed |
| `s3-disk-contents.tf` | S3 backup for disks | Optional AWS feature |
| `ecr.tf` | ECR repositories | N/A for Helm (pre-requisite) |

### Helm Templates Needing Enhancement

| Template | Missing Feature | Reference |
|----------|-----------------|-----------|
| `postgres/statefulset-primary.yaml` | fsGroup security context | `kubernetes.tf:646-650` |
| `postgres/statefulset-primary.yaml` | Init container with proper config | `kubernetes.tf:665-690` |
| `postgres/service.yaml` | Headless services | `kubernetes.tf:802-830` |
| `postgres/secret.yaml` | Replication credentials | `kubernetes.tf:164-182` |
| `postgres/configmap.yaml` | pg_partman configuration | `kubernetes.tf:227` |
| `api-service/serviceaccount.yaml` | IAM role placeholder validation | `api-service.tf:210-224` |
| `registry/deployment-native.yaml` | Full TLS config | `kubernetes.tf:1706-1912` |
| `availability-updater/cronjob.yaml` | Tolerations | `availability-updater-service.tf:510-517` |

---

## Recommendations

### Immediate Actions (Before Production Use)

1. **Add PostgreSQL headless services** - Required for StatefulSet proper operation
2. **Add replication credentials secret** - Required for PostgreSQL streaming replication
3. **Document IAM role requirements** - Clear documentation for IRSA/Workload Identity setup
4. **Add monitoring stack** - Either as templates or dependency

### Short-term Actions

1. **Add Docker Hub registry cache** - Important for avoiding rate limits
2. **Improve native registry TLS** - Complete TLS configuration
3. **Add fsGroup to all StatefulSets/Deployments** - Security best practice
4. **Add ConfigMap-based schema files** - For schema version control

### Long-term Actions

1. **Create monitoring subchart** - Prometheus + Grafana + dashboards
2. **Add optional EFA plugin** - For high-performance networking
3. **Add image pre-puller** - For faster pod startup
4. **Document cloud-specific post-install** - CloudFront, GCP HTTPS LB, etc.

---

## Configuration Parity Analysis

### Values Comparison

| Configuration | OpenTofu | Helm (values.yaml) | Match |
|---------------|----------|-------------------|-------|
| PostgreSQL storage | 100Gi | 100Gi | Yes |
| PostgreSQL memory limit | 4Gi | 4Gi | Yes |
| PostgreSQL CPU limit | 2 | 2000m | Yes |
| API replicas | 2 | 2 | Yes |
| CronJob schedule | Every 5 min | Every 5 min | Yes |
| Processor memory limit | 4Gi | 4Gi | Yes |
| Registry storage | 50Gi (ghcr), 100Gi (native) | 50Gi | Partial |
| PGMQ visibility timeout | 900s | 900s | Yes |
| Max reservation hours | 168 | 168 | Yes |

### Missing Values in Helm Chart

```yaml
# Should be added to values.yaml:

postgres:
  replication:
    enabled: true
    user: "replicator"
    # password: auto-generated

monitoring:
  enabled: false  # Default off, enable for production
  prometheus:
    retention: 15d
    storage: 50Gi
  grafana:
    adminPassword: ""
    nodePort: 30080

registry:
  dockerhub:
    enabled: false  # Currently completely missing
  native:
    storage: 100Gi  # OpenTofu uses 100Gi, Helm uses 50Gi

nvidia:
  dcgmExporter:
    enabled: true
  migManager:
    enabled: true
```

---

## Conclusion

The Helm chart provides a good foundation for multi-cloud deployment but requires several enhancements before being production-ready, particularly:

1. **PostgreSQL HA features** (headless services, replication credentials)
2. **Monitoring stack** (critical for operations)
3. **IAM integration documentation** (IRSA/Workload Identity)
4. **Complete registry implementation** (Docker Hub cache, TLS)

The chart structure is well-organized and follows Helm best practices. The use of cloud-specific values files (`values-aws.yaml`, `values-gcp.yaml`) is excellent for multi-cloud support. Focus should be on completing the missing features rather than restructuring.

**Estimated effort to achieve full parity:** 2-3 weeks of focused development work.
