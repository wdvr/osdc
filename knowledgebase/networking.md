# Networking

## VPC

- **CIDR**: `10.0.0.0/16`
- **Resource**: `aws_vpc.gpu_dev_vpc`
- **Terraform**: `/terraform-gpu-devservers/main.tf`

## Subnets

### Public Subnets (3)

| Subnet | CIDR | AZ | Purpose |
|--------|------|----|---------|
| Primary | `10.0.0.0/20` | AZ[0] | Main GPU nodes |
| Secondary | `10.0.16.0/20` | AZ[1] | Additional capacity |
| Tertiary | `10.0.32.0/20` | AZ[2] | Additional capacity (conditional) |

All public subnets have `map_public_ip_on_launch = true` and route to Internet Gateway.

### Private Subnets (3)

| Subnet | CIDR | AZ | Purpose |
|--------|------|----|---------|
| Private Primary | `10.0.48.0/20` | AZ[0] | Multi-EFA instances |
| Private Secondary | `10.0.64.0/20` | AZ[1] | Multi-EFA instances |
| Private Tertiary | `10.0.80.0/20` | AZ[2] | Multi-EFA instances |

Private subnets route to NAT Gateway for internet access. Used for multi-EFA GPU types (h100, h200, b200) that need multiple network interfaces.

## Internet Gateway

- **Resource**: `aws_internet_gateway.gpu_dev_igw`
- Routes `0.0.0.0/0` from public subnets

## NAT Gateway

- **Purpose**: Internet access for private subnet instances (multi-EFA)
- **Elastic IP**: Allocated for NAT
- **Location**: Primary public subnet

## Security Groups

### Control Plane SG

- Allows inbound from GPU dev SG on port 443

### GPU Dev SG (`gpu_dev_sg`)

Core security group for all GPU nodes:

| Direction | Port | Source/Dest | Purpose |
|-----------|------|-------------|---------|
| Ingress | 443 | Control Plane SG | K8s API |
| Ingress | 10250 | Control Plane SG | Kubelet |
| Ingress | 22 | 0.0.0.0/0 | SSH (direct) |
| Ingress | 30000-32767 | 0.0.0.0/0 | NodePort services |
| Ingress | All | Self | Intra-node communication |
| Egress | All | 0.0.0.0/0 | Internet access |

**EFA self-referencing rule**: The SG has a self-referencing ingress rule for all traffic, required for EFA cross-node communication.

### EFS SG

- Ingress: Port 2049 (NFS) from GPU Dev SG
- **Resource**: `/terraform-gpu-devservers/efs.tf`

### ALB SG

- Ingress: Ports 443, 80 from 0.0.0.0/0
- **Resource**: `/terraform-gpu-devservers/alb.tf`

### SSH Proxy SG

- Ingress: Ports 8080 (health), 8081 (WebSocket) from ALB SG
- **Resource**: `/terraform-gpu-devservers/ssh-proxy.tf`

## Placement Groups

- One cluster placement group per GPU type with `use_placement_group=true`
- Ensures instances are in same rack for lowest latency
- Critical for multi-node NCCL performance

## DNS (Route53)

### Hosted Zones

| Zone | Domain | Workspace |
|------|--------|-----------|
| Primary | `devservers.io` | prod |
| Subdomain | `test.devservers.io` | default (test) |

NS delegation records created in parent zone when using subdomains.

### ACM Certificate

- Wildcard: `*.{domain}` (e.g., `*.devservers.io`)
- DNS validation via Route53
- Used by ALB HTTPS listener

### DNS Records per Reservation

| Record | Type | Value | TTL |
|--------|------|-------|-----|
| `{name}.{domain}` | CNAME | ALB DNS name | 60s |
| `_port.{name}.{domain}` | TXT | `"{port}"` | 60s |

Created by `create_dns_record()` in `dns_utils.py`, cleaned up on cancellation/expiry.

## Application Load Balancer (ALB)

- **Resource**: `jupyter_alb` (external)
- **Terraform**: `/terraform-gpu-devservers/alb.tf`

### Listeners

| Port | Protocol | Action |
|------|----------|--------|
| 443 | HTTPS | Forward based on host header rules |
| 80 | HTTP | Redirect to HTTPS |

### Routing Rules

| Priority | Host | Target |
|----------|------|--------|
| 1 | `ssh.{domain}` | SSH proxy WebSocket target group (port 8081) |
| Auto | `{name}.{domain}` | Per-reservation Jupyter target group |
| Default | * | Fixed 404 response |

### Target Groups

- **Default**: Returns 404 (catch-all)
- **SSH proxy**: WebSocket (port 8081), registered to ECS tasks
- **Per-reservation**: HTTP (Jupyter NodePort), created dynamically by Lambda

## SSH Proxy (ECS Fargate)

- **Terraform**: `/terraform-gpu-devservers/ssh-proxy.tf`
- **Cluster**: `ssh-proxy`
- **Task Definition**: Fargate, 256 CPU (0.25 vCPU), 512 MB memory
- **Service**: 2 instances in public subnets
- **Ports**: 8080 (health), 8081 (WebSocket)
- **Image**: `ssh-proxy` ECR repository

### Proxy Server (`proxy.py`)

- **File**: `/terraform-gpu-devservers/ssh-proxy/proxy.py`
- **Protocol**: WebSocket server on port 8081
- **Connection flow**:
  1. Client connects to `wss://ssh.{domain}/tunnel/{hostname}`
  2. Server extracts subdomain from hostname
  3. Looks up `ssh_domain_mappings` DynamoDB table for `node_ip:node_port`
  4. Opens TCP connection to `node_ip:node_port`
  5. Bidirectional forwarding: WebSocket <-> TCP
- **Health check**: HTTP GET `/health` on port 8080
- **WebSocket config**: ping_interval=30s, ping_timeout=10s
- **Error codes**: 4000 (invalid path), 4004 (host not found), 4500 (internal error)

### Client Side (`ssh_proxy.py`)

- **Entry point**: `gpu-dev-ssh-proxy <target_host> <target_port>`
- Used as SSH ProxyCommand
- Connects via `wss://{ssh_proxy_host}/tunnel/{target_host}`
- Strips HTTP proxy environment variables to avoid corporate proxy issues
- 3 retries with exponential backoff

## EFA (Elastic Fabric Adapter)

- **Purpose**: High-speed inter-node networking for NCCL/GPU communication
- **Bandwidth**: Up to 3200 Gbps on p5.48xlarge
- **Limitation**: Same AZ only
- **K8s resource**: `vpc.amazonaws.com/efa` (exposed by EFA device plugin DaemonSet)
- **Allocated**: Only for full-node reservations (8 GPUs) on EFA-capable instances
- **Interface count**: Varies by instance type (4 for a100, 32 for h100/h200/b200)

See [multi-node.md](multi-node.md) for NCCL configuration details.

## Network Tuning

Applied via user-data on GPU nodes (`al2023-user-data.sh`):
```
net.core.rmem_default=262144000
net.core.rmem_max=262144000
net.core.wmem_default=262144000
net.core.wmem_max=262144000
```

Hugepages for EFA RDMA:
```
vm.nr_hugepages = 5128
```
Set before nodeadm so kubelet reports hugepages as allocatable.
