# Authentication

## Overview

Authentication has three layers:
1. **AWS IAM** -- controls who can interact with the platform
2. **GitHub SSH Keys** -- provides SSH access to pods
3. **K8s RBAC** -- controls what Lambda functions can do in the cluster

## AWS IAM Authentication

### User Authentication

Users authenticate via AWS credentials (SSO, IAM user, or profile):

1. CLI calls `sts:GetCallerIdentity` to verify credentials
2. CLI calls `sqs:GetQueueUrl` to verify access to the reservation queue
3. AWS username (from ARN) becomes the `user_id` for reservations

### AWS Profile Support

The CLI checks for a `gpu-dev` AWS profile first:
```python
# In config.py
available_profiles = boto3.Session().available_profiles
if "gpu-dev" in available_profiles:
    session = boto3.Session(profile_name="gpu-dev")
```

Falls back to default session if `gpu-dev` profile doesn't exist.

### Minimum IAM Policy

Defined in `/cli-tools/gpu-dev-cli/minimal-iam-policy.json`. Users need permissions for:
- `sqs:SendMessage`, `sqs:GetQueueUrl` -- send reservation requests
- `dynamodb:GetItem`, `dynamodb:Query`, `dynamodb:Scan` -- poll status and list reservations
- `sts:GetCallerIdentity` -- identity verification
- `ec2:DescribeSnapshots`, `ec2:CreateTags` -- disk management
- `s3:GetObject` -- disk contents viewing

## GitHub SSH Key Authentication

### Configuration

```bash
gpu-dev config set github_user <your-github-username>
```

Stored in `~/.config/gpu-dev/config.json` as `github_user` field.

### Validation Flow

In `auth.py`, `validate_ssh_key_matches_github_user()`:

1. Runs `ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=accept-new git@github.com`
2. Parses response: `Hi <username>! You've successfully authenticated`
3. Compares detected username with configured `github_user` (case-insensitive)
4. Supports password-protected SSH keys (stops spinner for password prompt)

### Key Injection into Pods

In Lambda's `create_pod()` function:

1. Init container runs:
   ```bash
   wget -q -O /home/dev/.ssh/authorized_keys https://github.com/{github_user}.keys
   ```
2. Fetches ALL public keys for the GitHub user
3. Writes to `/home/dev/.ssh/authorized_keys` in pod
4. When `add_user` action is used, additional keys are appended

### SSH Access Flow

```
User's SSH key -> gpu-dev-ssh-proxy (WebSocket) -> ALB -> ECS proxy -> NodePort -> Pod sshd
```

The pod runs an SSH server (installed in Docker image) that authenticates against the injected GitHub public keys.

## K8s RBAC (Lambda Authorization)

### aws-auth ConfigMap

Defined in `/terraform-gpu-devservers/kubernetes.tf`:

```yaml
mapRoles:
  - rolearn: <node-role-arn>
    username: system:node:{{EC2PrivateDNSName}}
    groups: [system:bootstrappers, system:nodes]
  - rolearn: <reservation-processor-role-arn>
    username: reservation-processor
  - rolearn: <expiry-role-arn>
    username: reservation-expiry
  - rolearn: <availability-updater-role-arn>
    username: availability-updater
```

### RBAC Bindings

**ServiceAccount**: `gpu-dev-sa` in namespace `gpu-dev`

**Role permissions** (in `gpu-dev` namespace):
- pods: all verbs
- services: all verbs
- configmaps: all verbs
- persistentvolumeclaims: all verbs
- persistentvolumes: all verbs
- events: all verbs

**RoleBinding** binds to:
- ServiceAccount `gpu-dev-sa`
- User `reservation-processor`
- User `reservation-expiry`
- User `availability-updater`

### Lambda IAM Roles

Each Lambda has its own IAM role with specific permissions:

**Reservation Processor** (`lambda.tf`):
- SQS: receive, delete, send messages
- DynamoDB: full access to reservations, disks, operations, availability, alb_target_groups, ssh_domain_mappings tables
- EKS: describe cluster
- EC2: describe/create/delete volumes, snapshots, tags; describe instances
- EFS: create/describe/delete file systems, mount targets
- ECR: get authorization token, batch get image
- Lambda: invoke self (for retries)
- ELBv2: create/delete target groups, rules; register/deregister targets
- Route53: change resource record sets, list resource record sets
- S3: put/get objects in disk contents bucket
- Bedrock: invoke model (for Claude Code in pods)
- STS: assume role

**Expiry Lambda** (`expiry.tf`):
- DynamoDB: reservations, disks tables
- EKS: describe cluster
- EC2: describe/create/delete snapshots, describe volumes
- S3: put/get objects
- Lambda: invoke self

**Availability Updater** (`availability.tf`):
- DynamoDB: availability table
- Autoscaling: describe auto scaling groups
- EKS: describe cluster

## Add User Feature

Users can add collaborators to their reservation:

```bash
gpu-dev edit --id <id>  # then select "Add user"
# or via SQS message with action "add_user"
```

Lambda's `add_user_to_pod()` function:
1. Fetches GitHub public keys for the new user
2. Appends to existing `/home/dev/.ssh/authorized_keys` in pod
3. Both the original user and added user can SSH with their GitHub keys

## Domain-Based Access

SSH proxy uses DynamoDB `ssh_domain_mappings` table to route:
- Key: `domain_name` (subdomain)
- Values: `node_ip`, `node_port`, `reservation_id`, `expires_at`

The proxy server (`ssh-proxy/proxy.py`) looks up the subdomain from the incoming hostname and establishes a TCP connection to the correct node and port.
