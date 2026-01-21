# Docker Build and Deployment Guide

## 🚨 CRITICAL: Always Use OpenTofu for Docker Operations

This document explains the **correct and only supported way** to build and deploy Docker images for this infrastructure.

---

## ❌ WRONG - Don't Do This!

**Never manually build and push Docker images:**

```bash
# ❌ DON'T DO THIS:
cd api-service
docker build -t api-service:latest .
docker push $ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/api-service:latest

cd ../reservation-processor-service
docker build -t reservation-processor:latest .
docker push $ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/reservation-processor:latest

# ❌ DON'T DO THIS EITHER:
aws ecr get-login-password --region us-east-2 | docker login --username AWS --password-stdin ...
```

---

## ✅ CORRECT - Use OpenTofu

**Always use `tofu apply` with targets:**

```bash
cd /Users/jschmidt/meta/osdc/terraform-gpu-devservers

# Build and deploy API service
tofu apply -target=null_resource.api_service_image

# Build and deploy reservation processor
tofu apply -target=null_resource.reservation_processor_image

# Or deploy everything at once
tofu apply -auto-approve
```

---

## Why Manual Builds Are Forbidden

### 1. ❌ ECR Repository Might Not Exist

ECR repositories are created by OpenTofu. Manual builds will fail if the repository doesn't exist yet.

```bash
# Manual push fails:
docker push 308535385114.dkr.ecr.us-east-2.amazonaws.com/reservation-processor:latest
# Error: repository does not exist
```

### 2. ❌ Wrong Build Context

Dockerfiles expect to be built from the **parent directory** (terraform-gpu-devservers), not from the service directory:

```dockerfile
# In reservation-processor-service/Dockerfile:
COPY shared/ ./shared/                              # ← Needs parent directory
COPY reservation-processor-service/processor/ ...   # ← Needs parent directory
```

Building from the service directory will fail:

```bash
cd reservation-processor-service
docker build -t reservation-processor:latest .
# Error: COPY shared/ ./shared/
# Error: no such file or directory
```

### 3. ❌ Manual Authentication Required

You'd need to manually authenticate with ECR every time:

```bash
# Manual auth is tedious and error-prone:
aws ecr get-login-password --region us-east-2 | \
  docker login --username AWS --password-stdin \
  $(aws sts get-caller-identity --query Account --output text).dkr.ecr.us-east-2.amazonaws.com
```

### 4. ❌ Kubernetes Won't Update

Manually pushing an image doesn't trigger Kubernetes to pull the new version. You'd need to manually:
- Update the deployment
- Restart pods
- Wait for rollout

### 5. ❌ Not Idempotent

Manual builds are not repeatable or automation-friendly:
- Different results on different machines
- Can't be used in CI/CD
- Hard to debug failures
- State drift between Docker and Terraform

### 6. ❌ Bypasses Dependency Management

OpenTofu ensures resources are created in the correct order:
1. Create ECR repository
2. Authenticate with ECR
3. Build Docker image
4. Push to ECR
5. Update Kubernetes deployment
6. Wait for rollout

Manual builds skip steps 1, 2, 5, and 6.

---

## How OpenTofu Handles Docker Builds

### The Automated Process

When you run `tofu apply -target=null_resource.reservation_processor_image`, OpenTofu:

1. ✅ **Creates ECR repository** (if doesn't exist)
   - Repository: `reservation-processor`
   - Region: `us-east-2`
   - Lifecycle policy: Keep last 10 images

2. ✅ **Authenticates with ECR**
   - Gets login password from AWS
   - Logs Docker into ECR automatically

3. ✅ **Builds Docker image**
   - From correct directory: `terraform-gpu-devservers/`
   - Using correct Dockerfile: `reservation-processor-service/Dockerfile`
   - With correct build context (has access to `shared/`)

4. ✅ **Tags image properly**
   - Format: `$ACCOUNT_ID.dkr.ecr.us-east-2.amazonaws.com/reservation-processor:latest`
   - Uses actual AWS account ID
   - Uses correct region

5. ✅ **Pushes to ECR**
   - Already authenticated
   - Pushes to correct repository
   - Verifies push succeeded

6. ✅ **Updates Kubernetes deployment**
   - Sets `imagePullPolicy: Always`
   - Triggers rollout automatically
   - Waits for pods to be ready

7. ✅ **Idempotent**
   - Safe to run multiple times
   - Same result every time
   - Works in automation/CI/CD

---

## Development Workflow

### When You Change Code

**Scenario 1: Changed API Service Code**

```bash
# 1. Edit the code
vim api-service/app/main.py

# 2. Rebuild and deploy
cd terraform-gpu-devservers
tofu apply -target=null_resource.api_service_image

# 3. Verify deployment
kubectl rollout status -n gpu-controlplane deployment/api-service
kubectl logs -n gpu-controlplane -l app=api-service --tail=50 -f
```

**Scenario 2: Changed Reservation Processor Code**

```bash
# 1. Edit the code
vim reservation-processor-service/processor/reservation_handler.py

# 2. Rebuild and deploy
cd terraform-gpu-devservers
tofu apply -target=null_resource.reservation_processor_image

# 3. Verify deployment
kubectl rollout status -n gpu-controlplane deployment/reservation-processor
kubectl logs -n gpu-controlplane -l app=reservation-processor --tail=100 -f
```

**Scenario 3: Changed Shared Utilities**

```bash
# 1. Edit the code
vim shared/k8s_client.py

# 2. Rebuild ALL services that use shared utilities
cd terraform-gpu-devservers
tofu apply \
  -target=null_resource.api_service_image \
  -target=null_resource.reservation_processor_image

# 3. Verify both deployments
kubectl rollout status -n gpu-controlplane deployment/api-service
kubectl rollout status -n gpu-controlplane deployment/reservation-processor
```

**Scenario 4: Changed Infrastructure + Code**

```bash
# Just apply everything:
cd terraform-gpu-devservers
tofu apply -auto-approve
```

---

## Available Targets

### Service Images

```bash
# API Service
tofu apply -target=null_resource.api_service_image

# Reservation Processor
tofu apply -target=null_resource.reservation_processor_image
```

### Related Resources

```bash
# ECR repositories only
tofu apply -target=aws_ecr_repository.api_service
tofu apply -target=aws_ecr_repository.reservation_processor

# Kubernetes deployments only
tofu apply -target=kubernetes_deployment.api_service
tofu apply -target=kubernetes_deployment.reservation_processor

# Everything for one service
tofu apply \
  -target=aws_ecr_repository.api_service \
  -target=null_resource.api_service_image \
  -target=kubernetes_deployment.api_service
```

---

## Troubleshooting

### "Repository does not exist" Error

**Problem**: You tried to manually push an image before running `tofu apply`.

**Solution**:
```bash
# Create the repository first:
cd terraform-gpu-devservers
tofu apply -target=aws_ecr_repository.reservation_processor

# Then use proper build process:
tofu apply -target=null_resource.reservation_processor_image
```

### "no such file or directory: shared/" Error

**Problem**: You tried to build from the service directory instead of parent directory.

**Solution**: Always use OpenTofu, which uses the correct build context:
```bash
cd terraform-gpu-devservers
tofu apply -target=null_resource.reservation_processor_image
```

### Image Not Updating in Kubernetes

**Problem**: Manually pushed image but pods still running old version.

**Solution**: Use OpenTofu to trigger rollout:
```bash
cd terraform-gpu-devservers
tofu apply -target=null_resource.reservation_processor_image

# Force restart if needed:
kubectl rollout restart -n gpu-controlplane deployment/reservation-processor
```

### "authentication required" Error

**Problem**: Docker not authenticated with ECR.

**Solution**: Use OpenTofu which handles auth automatically:
```bash
cd terraform-gpu-devservers
tofu apply -target=null_resource.reservation_processor_image
```

---

## CI/CD Integration

For automated deployments (GitHub Actions, Jenkins, etc.):

```yaml
# .github/workflows/deploy.yml
name: Deploy Services

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      
      - name: Install OpenTofu
        run: |
          wget https://github.com/opentofu/opentofu/releases/download/v1.8.0/tofu_1.8.0_linux_amd64.zip
          unzip tofu_1.8.0_linux_amd64.zip
          sudo mv tofu /usr/local/bin/
      
      - name: Configure AWS credentials
        uses: aws-actions/configure-aws-credentials@v2
        with:
          aws-access-key-id: ${{ secrets.AWS_ACCESS_KEY_ID }}
          aws-secret-access-key: ${{ secrets.AWS_SECRET_ACCESS_KEY }}
          aws-region: us-east-2
      
      - name: Deploy changed services
        run: |
          cd terraform-gpu-devservers
          tofu init
          
          # Detect which services changed and deploy them
          if git diff --name-only HEAD~1 | grep -q "api-service/"; then
            tofu apply -target=null_resource.api_service_image -auto-approve
          fi
          
          if git diff --name-only HEAD~1 | grep -q "reservation-processor-service/"; then
            tofu apply -target=null_resource.reservation_processor_image -auto-approve
          fi
          
          if git diff --name-only HEAD~1 | grep -q "shared/"; then
            # Shared code changed, rebuild everything
            tofu apply -auto-approve
          fi
```

---

## Quick Reference

### ✅ ALWAYS Use These Commands

```bash
cd terraform-gpu-devservers

# Deploy everything
tofu apply -auto-approve

# Deploy specific service
tofu apply -target=null_resource.api_service_image
tofu apply -target=null_resource.reservation_processor_image

# Check deployment status
kubectl rollout status -n gpu-controlplane deployment/api-service
kubectl rollout status -n gpu-controlplane deployment/reservation-processor
```

### ❌ NEVER Use These Commands

```bash
# ❌ FORBIDDEN:
docker build ...
docker push ...
aws ecr get-login-password ...
docker login ...

# These will fail, cause errors, or create inconsistent state
```

---

## Summary

| Requirement | Manual Build | OpenTofu |
|-------------|-------------|----------|
| ECR repo must exist first | ❌ You must create manually | ✅ Created automatically |
| Correct build context | ❌ Easy to get wrong | ✅ Always correct |
| ECR authentication | ❌ Manual every time | ✅ Automatic |
| Kubernetes update | ❌ Manual restart needed | ✅ Automatic rollout |
| Idempotent | ❌ No | ✅ Yes |
| CI/CD friendly | ❌ No | ✅ Yes |
| Error prone | ❌ Yes | ✅ No |
| Recommended | ❌ **NEVER** | ✅ **ALWAYS** |

---

**Remember: When in doubt, use `tofu apply`!** 🚀

For more information, see:
- `README.md` - Main project documentation
- `CLAUDE.md` - AI assistant guidelines  
- `URGENT_CLEANUP.md` - Deployment troubleshooting
- `reservation-processor-service/README.md` - Service-specific docs

