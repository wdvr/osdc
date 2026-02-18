# Current Limitations & Cloud Dependencies

## Cloud-Agnostic Status

### Fully Cloud-Agnostic (Helm Chart)

The Helm chart (`charts/gpu-dev-server/`) is cloud-agnostic and can be installed on any Kubernetes cluster:

- PostgreSQL + PGMQ (stateful, no cloud dependency)
- API Service (FastAPI, no AWS SDK calls)
- Reservation Processor deployment
- CronJobs (availability updater, reservation expiry)
- RBAC, namespaces, storage class
- Registry caches (standard Docker registry)
- Image prepuller (DaemonSet)

### AWS Dependencies (Python Services)

The reservation processor's Python code still has AWS-specific logic:

| Feature | AWS Dependency | Non-AWS Behavior |
|---------|---------------|-----------------|
| EBS persistent disks | `ec2_client.create_volume()` | Skipped (EmptyDir) |
| EBS snapshots | `ec2_client.create_snapshot()` | Skipped |
| EFS shared mounts | NFS via EFS DNS | Skipped (EmptyDir) |
| Route53 DNS for pods | `route53_client` | Skipped |
| ECR image cache check | `ecr_client.describe_images()` | Skipped |
| BuildKit ECR auth | `aws ecr get-login-password` | No auth needed |
| S3 disk content backup | `s3_client` | Skipped |
| DynamoDB domain mappings | SSH proxy | Not available |

The `CLOUD_PROVIDER` env var controls behavior:
- `aws` (default): Full AWS integration
- `local`: Skip all AWS API calls, use EmptyDir for storage

### AWS-Only Infrastructure (OpenTofu)

These components are inherently AWS-specific:

- EKS cluster management
- IAM roles for service accounts (IRSA)
- ALB with ACM certificates
- Auto Scaling Groups for nodes
- Route53 public DNS
- SSH proxy on ECS Fargate

## Known Issues

1. **GPU base image must be built manually** - requires Docker Desktop and `tofu apply -target=null_resource.docker_build_and_push`
2. **SSH proxy runs on ECS** - not yet migrated to K8s; depends on DynamoDB for domain mappings
3. **No GCP/bare-metal support** - `providers/` abstraction layer exists but is incomplete
4. **Max reservation: 48 hours** - initial 24h + one 24h extension

## Roadmap for Full Cloud-Agnosticism

### Short Term
- [x] Helm chart is cloud-agnostic
- [x] `CLOUD_PROVIDER` env var gates AWS calls
- [x] `BUILDKIT_REGISTRY_URL` for non-ECR registries
- [ ] Validate full local flow end-to-end

### Medium Term
- [ ] Move SSH proxy from ECS to K8s Deployment
- [ ] Replace DynamoDB domain lookups with PostgreSQL
- [ ] Abstract storage via `providers/` layer (`create_volume()`, `upload_to_object_storage()`)
- [ ] Make EFS mounts configurable via generic NFS server in values.yaml

### Long Term
- [ ] GCP provider implementation (GKE, Persistent Disks, Cloud DNS)
- [ ] Bare-metal provider (local storage, MetalLB, CoreDNS)
- [ ] Multi-cloud Helm chart with provider-specific value files
