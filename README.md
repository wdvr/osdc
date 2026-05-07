# ODC - Open Dev Cloud

**High-Performance GPU Development Platform**

ODC (formerly OSDC) provides on-demand GPU reservations for machine learning development and training, running on Kubernetes with persistent storage, multinode support, and seamless IDE integration.

---

## Quick Start

```bash
# Install the CLI
pip install gpu-dev

# Configure your GitHub username
gpu-dev config set github_user your-github-username

# Reserve GPUs
gpu-dev reserve

# Connect
gpu-dev connect
```

---

## Features

- **On-Demand GPU Access** - Reserve B200, H200, H100, A100, and more with a single command
- **Persistent Storage** - Your work survives across reservations with automatic snapshots
- **IDE Integration** - Seamless VS Code and Cursor remote development
- **Multinode Support** - Distributed training across multiple GPU nodes with EFA networking
- **Custom Environments** - Bring your own Docker images or Dockerfiles
- **Jupyter Lab** - Built-in notebook support
- **Claude Code Pre-installed** - AI pair programming out of the box

---

## Documentation

**Full documentation:** [odc-docs.nest.x2p.facebook.net](https://odc-docs.nest.x2p.facebook.net) *(internal)*

### Quick Links

- [User Guide](docs/USER_GUIDE.md) - Comprehensive guide to all features
- [CLI Reference](cli-tools/gpu-dev-cli/README.md) - Complete command reference
- [GitHub Repository](https://github.com/wdvr/osdc)

---

## Installation

```bash
# Install from GitHub
pip install git+https://github.com/wdvr/osdc.git

# Or install from local clone
git clone https://github.com/wdvr/osdc.git
cd osdc
pip install -e .
```

**Requirements:**
- Python 3.10+
- AWS credentials configured
- GitHub account with SSH keys

---

## Example Usage

```bash
# Reserve 4 H100 GPUs for 8 hours with Jupyter
gpu-dev reserve -t h100 -g 4 -h 8 --jupyter

# Connect via SSH
gpu-dev connect

# Check availability
gpu-dev avail

# List your reservations
gpu-dev list

# Extend a reservation
gpu-dev edit <id> --extend
```

---

## GPU Types

| Type | Memory/GPU | Best For |
|------|-----------|----------|
| B200 | 192 GB | Latest NVIDIA Blackwell architecture |
| H200 | 141 GB | Large models, high memory workloads |
| H100 | 80 GB | Production training |
| A100 | 40 GB | General ML training |
| T4 | 16 GB | Development and testing |

See the [full GPU reference](docs/USER_GUIDE.md#gpu-types-reference) for all available types.

---

## Architecture

ODC runs on Amazon EKS with:
- Auto-scaling GPU node groups
- EBS persistent storage with snapshots
- EFS shared storage
- EFA networking for multinode
- NVIDIA GPU Operator
- Custom reservation processor

See [Architecture Overview](docs/USER_GUIDE.md#architecture-overview) for details.

---

## Contributing

Contributions welcome! Please:
1. Fork the repository
2. Create a feature branch
3. Submit a pull request

---

## License

[Add license information]

---

## Support

- Report issues: [GitHub Issues](https://github.com/wdvr/osdc/issues)
- Internal documentation: [odc-docs.nest.x2p.facebook.net](https://odc-docs.nest.x2p.facebook.net)

---

**Project Status:** Active development | Production ready for internal use
