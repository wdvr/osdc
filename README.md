# GPU Developer Servers Infrastructure

## 🚀 Project Overview

The GPU Developer Servers Infrastructure (OSDC) is a comprehensive Kubernetes-based platform that provides on-demand GPU development environments for machine learning and deep learning workloads. Built on AWS EKS with OpenTofu (Terraform fork) for infrastructure management, it offers developers seamless access to various GPU types through a simple CLI interface.

### Key Features

- **🎮 Multi-GPU Support**: Access to NVIDIA B200, H200, H100, A100, A10G, L4, and T4 GPUs
- **⚡ On-Demand Provisioning**: Reserve GPUs instantly with configurable duration (5 minutes to 48 hours)
- **🔐 Secure Access**: GitHub SSH key authentication and AWS IAM-based API authentication
- **💾 Persistent Storage**: Named EBS disks and shared EFS storage across sessions
- **🐳 Custom Environments**: Support for custom Docker images and Dockerfiles
- **📊 Monitoring**: Integrated Grafana dashboards with NVIDIA DCGM metrics
- **🔬 Profiling Support**: Dedicated nodes for NVIDIA Nsight profiling tools
- **🌐 Multi-Node**: Support for distributed training across multiple GPU nodes

## 📁 Project Structure

```
osdc/
├── CLAUDE.md                        # AI agent context and development notes
├── DOCUMENTATION_ACTION_PLAN.md     # Documentation review checklist
├── cli-tools/                       # CLI tool implementation
│   └── gpu-dev-cli/                # Python CLI for GPU reservations
│       ├── gpu_dev_cli/            # CLI source code
│       └── README.md               # CLI usage documentation
└── terraform-gpu-devservers/        # Infrastructure as Code
    ├── *.tf                        # OpenTofu configuration files
    ├── README.md                   # Infrastructure documentation
    ├── api-service/                # REST API service
    │   ├── app/                   # FastAPI application
    │   └── README.md              # API documentation
    ├── reservation-processor-service/  # Job processing service
    │   └── README.md              # Processor documentation
    ├── availability-updater-service/   # GPU availability tracker
    ├── reservation-expiry-service/     # Reservation expiry handler
    ├── database/                   # Database schemas and migrations
    ├── migrations/                 # Database migration scripts
    ├── shared/                     # Shared utilities
    └── templates/                  # Node bootstrap scripts
```

## 🏗️ Architecture

The system follows a microservices architecture with clear separation of concerns:

```
User → CLI → API Service → PostgreSQL/PGMQ → Job Processor → Kubernetes → GPU Pods
```

### Core Components

1. **GPU Dev CLI** (`gpu-dev`): Command-line interface for developers
2. **API Service**: FastAPI-based REST API with AWS IAM authentication
3. **PostgreSQL + PGMQ**: Database for state management and message queuing
4. **Job Processor Pod**: Kubernetes controller that manages GPU pod lifecycle
5. **EKS Cluster**: Kubernetes cluster with GPU-enabled node groups
6. **GPU Pods**: User development environments with SSH access

## 🚀 Quick Start

### For End Users

```bash
# Install the CLI
pip install git+https://github.com/wdvr/osdc.git

# Initial setup
gpu-dev setup

# Authenticate
gpu-dev login

# Reserve GPUs
gpu-dev reserve --gpu-type h100 --gpus 4 --hours 8

# Connect to your reservation
gpu-dev connect

# List your reservations
gpu-dev list

# Check GPU availability
gpu-dev avail
```

### For Infrastructure Operators

```bash
# Clone the repository
git clone https://github.com/wdvr/osdc.git
cd osdc/terraform-gpu-devservers

# Initialize OpenTofu (NOT Terraform!)
tofu init

# Deploy infrastructure
tofu apply

# Get API endpoint
tofu output api_service_url
```

## ⚠️ Critical Requirements

### OpenTofu Only - Never Use Terraform

This infrastructure **EXCLUSIVELY** uses OpenTofu. Using Terraform will corrupt the state file and cause irreversible damage.

```bash
# ✅ CORRECT
tofu init
tofu plan
tofu apply

# ❌ FORBIDDEN - Will destroy infrastructure
terraform init  # NEVER use this
terraform plan  # NEVER use this
terraform apply # NEVER use this
```

## 📚 Documentation

- **[CLI Documentation](cli-tools/gpu-dev-cli/README.md)**: Complete guide for using the GPU Dev CLI
- **[Infrastructure Documentation](terraform-gpu-devservers/README.md)**: OpenTofu infrastructure setup and management
- **[API Documentation](terraform-gpu-devservers/api-service/README.md)**: REST API endpoints and authentication
- **[CLAUDE.md](CLAUDE.md)**: AI agent context, development notes, and troubleshooting

## 🔧 Development

### Prerequisites

- Python 3.11+
- OpenTofu 1.8+ (install via `brew install opentofu`)
- AWS CLI configured with appropriate credentials
- kubectl for Kubernetes management
- Docker for building service images

### Setting Up Development Environment

```bash
# Install development dependencies
cd cli-tools/gpu-dev-cli
poetry install --with dev

# Run tests
poetry run pytest

# Format code
poetry run black .
poetry run isort .
```

### Deploying Changes

```bash
# Update API service
cd terraform-gpu-devservers
tofu apply -target=null_resource.api_service_image

# Update job processor
tofu apply -target=null_resource.reservation_processor_image

# Full deployment
tofu apply -auto-approve
```

## 🎯 Current Status

### ✅ Production Ready
- EKS cluster with multi-GPU support
- PostgreSQL + PGMQ for state and queue management
- API Service with CloudFront HTTPS
- Job Processor Pod for reservation management
- CLI tool with full API integration
- SSH access with GitHub key authentication
- Persistent disk management
- GPU monitoring with Grafana

### 🚧 In Development
- FQDN for development servers
- Enhanced debugging and observability
- Multi-node reservation improvements
- Advanced quota management

## 🤝 Contributing

See [CLAUDE.md](CLAUDE.md) for development guidelines and agent notes. Key principles:

- Use OpenTofu exclusively (never Terraform)
- Follow existing code patterns
- Keep documentation updated
- Test changes thoroughly
- Use compact, efficient code

## 📞 Support

- **Issues**: Report bugs via GitHub issues
- **Documentation**: Check component-specific READMEs
- **Debugging**: Use `gpu-dev show <id>` for detailed reservation info
- **Logs**: Access via `kubectl logs` for infrastructure debugging

## 📄 License

[License information to be added]

---

*For detailed technical documentation and troubleshooting, refer to the component-specific README files and [CLAUDE.md](CLAUDE.md) for comprehensive development notes.*