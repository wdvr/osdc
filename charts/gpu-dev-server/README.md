# GPU Dev Server Helm Chart

Helm chart for deploying GPU Development Server infrastructure on Kubernetes.

## Overview

This chart deploys the following components:

- **PostgreSQL** with PGMQ extension (primary + replica)
- **API Service** - REST API for job submission with IAM auth
- **Reservation Processor** - Polls PGMQ and manages GPU pod lifecycle
- **Availability Updater** - CronJob that tracks GPU availability
- **Reservation Expiry** - CronJob that handles reservation cleanup
- **Registry Caches** - Pull-through caches for ghcr.io and Docker Hub

## Prerequisites

- Kubernetes 1.24+
- Helm 3.8+
- A storage class (gp3 for AWS, pd-ssd for GCP)
- For AWS: EKS cluster with IAM roles configured
- For GCP: GKE cluster with Workload Identity configured

## Installation

### AWS (EKS)

```bash
# Create a values file with your AWS-specific settings
cat > my-values.yaml <<EOF
cloudProvider:
  name: "aws"
  region: "us-east-2"
  aws:
    eksClusterName: "my-gpu-cluster"
    primaryAvailabilityZone: "us-east-2a"

postgres:
  auth:
    password: "your-secure-password"  # Or use existingSecret

apiService:
  image:
    repository: "your-registry/api-service"
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT:role/api-service-role"

reservationProcessor:
  image:
    repository: "your-registry/reservation-processor"
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT:role/reservation-processor-role"

availabilityUpdater:
  image:
    repository: "your-registry/availability-updater"
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT:role/availability-updater-role"

reservationExpiry:
  image:
    repository: "your-registry/reservation-expiry"
  serviceAccount:
    annotations:
      eks.amazonaws.com/role-arn: "arn:aws:iam::ACCOUNT:role/reservation-expiry-role"
EOF

# Install the chart
helm install gpu-dev ./charts/gpu-dev-server \
  -f charts/gpu-dev-server/values-aws.yaml \
  -f my-values.yaml
```

### GCP (GKE)

```bash
# Create a values file with your GCP-specific settings
cat > my-values.yaml <<EOF
cloudProvider:
  name: "gcp"
  region: "us-central1"
  gcp:
    gkeClusterName: "my-gpu-cluster"
    zone: "us-central1-a"
    projectId: "my-project"

postgres:
  auth:
    password: "your-secure-password"

apiService:
  image:
    repository: "gcr.io/my-project/api-service"
  serviceAccount:
    annotations:
      iam.gke.io/gcp-service-account: "api-service@my-project.iam.gserviceaccount.com"

# ... similar for other services
EOF

# Install the chart
helm install gpu-dev ./charts/gpu-dev-server \
  -f charts/gpu-dev-server/values-gcp.yaml \
  -f my-values.yaml
```

## Configuration

See [values.yaml](values.yaml) for the full list of configurable parameters.

### Key Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `global.prefix` | Prefix for resource naming | `gpu-dev` |
| `namespaces.controlplane` | Controlplane namespace | `gpu-controlplane` |
| `namespaces.workloads` | GPU workloads namespace | `gpu-dev` |
| `storage.class` | Storage class name | `gp3` |
| `postgres.enabled` | Enable PostgreSQL | `true` |
| `postgres.auth.password` | PostgreSQL password | `""` (generate) |
| `apiService.enabled` | Enable API Service | `true` |
| `apiService.replicas` | API Service replicas | `2` |
| `reservationProcessor.enabled` | Enable Reservation Processor | `true` |
| `availabilityUpdater.enabled` | Enable Availability Updater | `true` |
| `reservationExpiry.enabled` | Enable Reservation Expiry | `true` |

### Cloud Provider Configuration

#### AWS

```yaml
cloudProvider:
  name: "aws"
  region: "us-east-2"
  aws:
    eksClusterName: "my-cluster"
    primaryAvailabilityZone: "us-east-2a"
    efsSecurityGroupId: "sg-xxx"
    efsSubnetIds: "subnet-xxx,subnet-yyy"
```

#### GCP

```yaml
cloudProvider:
  name: "gcp"
  region: "us-central1"
  gcp:
    gkeClusterName: "my-cluster"
    zone: "us-central1-a"
    projectId: "my-project"
```

## Upgrading

```bash
helm upgrade gpu-dev ./charts/gpu-dev-server -f my-values.yaml
```

## Uninstalling

```bash
helm uninstall gpu-dev
```

**Note:** PersistentVolumeClaims are not deleted by default to prevent data loss.
To delete them manually:

```bash
kubectl delete pvc -n gpu-controlplane -l app.kubernetes.io/instance=gpu-dev
```

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                   gpu-controlplane                   │
│                                                     │
│  ┌──────────────┐  ┌──────────────┐               │
│  │ API Service  │  │  PostgreSQL  │               │
│  │  (FastAPI)   │◀─│   + PGMQ     │               │
│  └──────┬───────┘  └──────▲───────┘               │
│         │                  │                        │
│         │ Push jobs        │ Poll jobs             │
│         ▼                  │                        │
│  ┌──────────────────────┴─────────┐               │
│  │    Reservation Processor       │               │
│  └────────────────────────────────┘               │
│                                                     │
│  ┌────────────────┐  ┌────────────────┐           │
│  │  Availability  │  │  Reservation   │           │
│  │    Updater     │  │    Expiry      │           │
│  │   (CronJob)    │  │   (CronJob)    │           │
│  └────────────────┘  └────────────────┘           │
└─────────────────────────────────────────────────────┘
```

## Troubleshooting

### Check pod status

```bash
kubectl get pods -n gpu-controlplane
```

### View logs

```bash
# API Service
kubectl logs -f -n gpu-controlplane -l app=api-service

# Reservation Processor
kubectl logs -f -n gpu-controlplane -l app=reservation-processor

# CronJobs
kubectl logs -n gpu-controlplane -l app=availability-updater
kubectl logs -n gpu-controlplane -l app=reservation-expiry
```

### Database access

```bash
kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432
psql -h localhost -U gpudev -d gpudev
```

## License

See repository LICENSE file.
