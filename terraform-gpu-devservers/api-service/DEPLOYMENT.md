# API Service Deployment Guide

## 🚀 Overview

This guide walks through deploying the GPU Dev API Service to your EKS cluster with public access via AWS Network Load Balancer.

## 📋 What Gets Deployed

```
AWS Resources:
├── ECR Repository (gpu-dev-api-service)
├── IAM Role (IRSA for AWS STS access)
├── Network Load Balancer (internet-facing)
└── Target Groups (automatic)

Kubernetes Resources:
├── ServiceAccount (with IRSA annotation)
├── ConfigMap (api-service-config)
├── Deployment (2 replicas)
├── Service (ClusterIP - internal)
└── Service (LoadBalancer - public)
```

## 🔧 Prerequisites

Before deploying:

1. ✅ **Postgres with PGMQ** - Already deployed (from previous steps)
2. ✅ **EKS Cluster** - Already configured
3. ✅ **AWS Load Balancer Controller** - Check if installed
4. ✅ **Docker** - For building image
5. ✅ **AWS CLI** - Configured with proper credentials

### Check AWS Load Balancer Controller

```bash
# Check if AWS Load Balancer Controller is installed
kubectl get deployment -n kube-system aws-load-balancer-controller

# If not installed, install it:
# https://docs.aws.amazon.com/eks/latest/userguide/aws-load-balancer-controller.html
```

## 📦 Step 1: Build and Push Docker Image

The Terraform configuration will automatically build and push the image, but you can do it manually:

```bash
cd terraform-gpu-devservers/api-service

# Get ECR repository URL
ECR_REPO=$(terraform output -raw api_service_ecr_url 2>/dev/null || \
  aws ecr describe-repositories --repository-names gpu-dev-api-service \
  --query 'repositories[0].repositoryUri' --output text)

# Login to ECR
aws ecr get-login-password --region us-east-1 | \
  docker login --username AWS --password-stdin $ECR_REPO

# Build image
docker build --platform linux/amd64 -t $ECR_REPO:latest .

# Push image
docker push $ECR_REPO:latest

echo "✅ Image pushed to $ECR_REPO:latest"
```

## 🚀 Step 2: Deploy to Kubernetes

```bash
cd terraform-gpu-devservers

# Plan the deployment
terraform plan

# Apply (this will build image and deploy to K8s)
terraform apply

# The build might take 2-5 minutes for first deployment
```

### What Terraform Does

1. Creates ECR repository
2. Builds Docker image from `api-service/`
3. Pushes image to ECR
4. Creates IAM role with STS permissions
5. Creates Kubernetes ServiceAccount with IRSA
6. Creates ConfigMap with configuration
7. Deploys API service (2 replicas)
8. Creates LoadBalancer service
9. Provisions AWS NLB automatically

## 🌐 Step 3: Get Public URL

```bash
# Wait for LoadBalancer to be provisioned (1-3 minutes)
kubectl get svc -n gpu-controlplane api-service-public -w

# Get the public URL
terraform output api_service_url

# Or manually:
kubectl get svc -n gpu-controlplane api-service-public \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'

# Example output:
# a1b2c3d4e5f6g7h8-123456789.us-east-1.elb.amazonaws.com
```

## ✅ Step 4: Verify Deployment

### Check Pods

```bash
# Check if pods are running
kubectl get pods -n gpu-controlplane -l app=api-service

# Should show:
# NAME                           READY   STATUS    RESTARTS   AGE
# api-service-xxxxxxxxxx-xxxxx   1/1     Running   0          2m
# api-service-xxxxxxxxxx-xxxxx   1/1     Running   0          2m
```

### Check Logs

```bash
# View logs
kubectl logs -n gpu-controlplane -l app=api-service --tail=50

# Should see:
# INFO:app.main:Starting up API service...
# INFO:app.main:Database connection pool created
# INFO:app.main:Database schema initialized
# INFO:app.main:PGMQ queue 'gpu_reservations' created
# INFO:app.main:API service started successfully
```

### Test Health Check

```bash
# Get LoadBalancer URL
LB_URL=$(terraform output -raw api_service_url | sed 's|http://||')

# Test health endpoint
curl http://$LB_URL/health | jq .

# Should return:
# {
#   "status": "healthy",
#   "database": "healthy",
#   "queue": "healthy",
#   "timestamp": "2026-01-15T..."
# }
```

### Test API Info

```bash
# Test root endpoint
curl http://$LB_URL/ | jq .

# Should return:
# {
#   "service": "GPU Dev API",
#   "version": "1.0.0",
#   "docs": "/docs",
#   "health": "/health",
#   "auth": {
#     "aws_login": "/v1/auth/aws-login",
#     "description": "Use AWS credentials to obtain an API key"
#   }
# }
```

### Test AWS Authentication

```bash
# Get your AWS credentials
export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id)
export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key)
export AWS_SESSION_TOKEN=$(aws configure get aws_session_token)

# Test authentication
curl -X POST http://$LB_URL/v1/auth/aws-login \
  -H "Content-Type: application/json" \
  -d "{
    \"aws_access_key_id\": \"$AWS_ACCESS_KEY_ID\",
    \"aws_secret_access_key\": \"$AWS_SECRET_ACCESS_KEY\",
    \"aws_session_token\": \"$AWS_SESSION_TOKEN\"
  }" | jq .

# Should return API key with 2-hour expiration
```

### Browse API Documentation

```bash
# Open Swagger UI in browser
echo "http://$LB_URL/docs"

# Or ReDoc
echo "http://$LB_URL/redoc"
```

## 🔒 Step 5: Add HTTPS (Optional but Recommended)

### Option A: Use AWS Certificate Manager (ACM)

1. **Request certificate in ACM:**
```bash
# Create or import certificate
aws acm request-certificate \
  --domain-name api.gpudev.example.com \
  --validation-method DNS
```

2. **Update `api-service.tf`:**

Uncomment the SSL annotations:
```hcl
annotations = {
  # ... existing annotations ...
  "service.beta.kubernetes.io/aws-load-balancer-ssl-cert" = "arn:aws:acm:us-east-1:123456789:certificate/xxx"
  "service.beta.kubernetes.io/aws-load-balancer-ssl-ports" = "443"
}
```

Uncomment the HTTPS port:
```hcl
port {
  name        = "https"
  port        = 443
  target_port = 8000
  protocol    = "TCP"
}
```

3. **Apply changes:**
```bash
terraform apply
```

4. **Create Route53 record:**
```bash
# Get LoadBalancer DNS
LB_DNS=$(kubectl get svc -n gpu-controlplane api-service-public \
  -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

# Create CNAME record
aws route53 change-resource-record-sets \
  --hosted-zone-id ZXXXXXXXXXXXXX \
  --change-batch "{
    \"Changes\": [{
      \"Action\": \"UPSERT\",
      \"ResourceRecordSet\": {
        \"Name\": \"api.gpudev.example.com\",
        \"Type\": \"CNAME\",
        \"TTL\": 300,
        \"ResourceRecords\": [{\"Value\": \"$LB_DNS\"}]
      }
    }]
  }"
```

### Option B: Use AWS-provided DNS

Just use the LoadBalancer DNS name directly:
```bash
# Get URL
terraform output api_service_url

# Use as-is (no custom domain needed)
https://a1b2c3d4e5f6g7h8-123456789.us-east-1.elb.amazonaws.com
```

## 🔄 Step 6: Update CLI Configuration

Update CLI to use the new API:

```bash
# Set in CLI configuration or environment
export GPU_DEV_API_URL="http://$LB_URL"

# Or for HTTPS:
export GPU_DEV_API_URL="https://api.gpudev.example.com"
```

## 📊 Monitoring

### Check API Service Status

```bash
# Pods
kubectl get pods -n gpu-controlplane -l app=api-service

# Service
kubectl get svc -n gpu-controlplane api-service-public

# Events
kubectl get events -n gpu-controlplane --field-selector involvedObject.name=api-service

# Logs from all pods
kubectl logs -n gpu-controlplane -l app=api-service --all-containers=true --tail=100
```

### Monitor Health

```bash
# Continuous health monitoring
watch -n 5 'curl -s http://$LB_URL/health | jq .'

# Check from within cluster
kubectl run -it --rm debug -n gpu-controlplane --image=curlimages/curl --restart=Never -- \
  curl http://api-service.gpu-controlplane.svc.cluster.local/health
```

## 🐛 Troubleshooting

### Pods Not Starting

```bash
# Check pod status
kubectl describe pod -n gpu-controlplane -l app=api-service

# Check logs
kubectl logs -n gpu-controlplane -l app=api-service

# Common issues:
# - Image pull error: Check ECR permissions
# - Database connection: Check postgres service is running
# - Config error: Check ConfigMap values
```

### LoadBalancer Not Provisioning

```bash
# Check service events
kubectl describe svc -n gpu-controlplane api-service-public

# Check AWS Load Balancer Controller logs
kubectl logs -n kube-system deployment/aws-load-balancer-controller

# Common issues:
# - Controller not installed: Install AWS LB Controller
# - Insufficient permissions: Check IAM role for controller
# - Subnet tags missing: Ensure subnets have proper tags
```

### Health Check Failing

```bash
# Test health from pod
kubectl exec -it -n gpu-controlplane deployment/api-service -- \
  curl localhost:8000/health

# Check if postgres is reachable
kubectl exec -it -n gpu-controlplane deployment/api-service -- \
  curl postgres-primary:5432 -v

# Check ConfigMap
kubectl get cm -n gpu-controlplane api-service-config -o yaml
```

### Authentication Not Working

```bash
# Check if IAM role is properly annotated
kubectl get sa -n gpu-controlplane api-service-sa -o yaml | grep role-arn

# Check IAM role permissions
aws iam get-role-policy \
  --role-name gpu-dev-api-service-role \
  --policy-name sts-get-caller-identity

# Test from pod
kubectl exec -it -n gpu-controlplane deployment/api-service -- \
  python -c "import boto3; print(boto3.client('sts').get_caller_identity())"
```

## 🔄 Updating the Service

### Update Code

```bash
# Make changes to api-service/app/main.py

# Terraform will detect changes and rebuild
terraform apply

# Or force rebuild
terraform taint null_resource.api_service_build
terraform apply
```

### Scale Replicas

```bash
# Edit api-service.tf
# Change: replicas = 2
# To: replicas = 5

terraform apply

# Or use kubectl
kubectl scale deployment -n gpu-controlplane api-service --replicas=5
```

### Update Configuration

```bash
# Edit ConfigMap values in api-service.tf
# Then apply:
terraform apply

# Restart pods to pick up new config
kubectl rollout restart deployment -n gpu-controlplane api-service
```

## 🗑️ Cleanup

### Remove API Service

```bash
# Remove Kubernetes resources
terraform destroy -target=kubernetes_deployment.api_service
terraform destroy -target=kubernetes_service.api_service_public
terraform destroy -target=kubernetes_service.api_service

# Remove ECR repository (optional)
terraform destroy -target=aws_ecr_repository.api_service
```

### Or use kubectl

```bash
kubectl delete deployment -n gpu-controlplane api-service
kubectl delete svc -n gpu-controlplane api-service api-service-public
```

## 📈 Performance Tuning

### Adjust Resources

```hcl
# In api-service.tf, modify resources:
resources {
  requests = {
    cpu    = "500m"    # Increase for more performance
    memory = "1Gi"     # Increase if seeing OOM
  }
  limits = {
    cpu    = "2000m"
    memory = "2Gi"
  }
}
```

### Adjust Replicas

```hcl
# In api-service.tf:
replicas = 5  # Scale up for higher load
```

### Enable Horizontal Pod Autoscaling (HPA)

```bash
kubectl autoscale deployment api-service \
  -n gpu-controlplane \
  --cpu-percent=70 \
  --min=2 \
  --max=10
```

## 🔐 Production Checklist

Before going to production:

- [ ] HTTPS enabled with ACM certificate
- [ ] Custom domain configured (or using AWS DNS)
- [ ] Rate limiting added to API code
- [ ] Request logging enabled
- [ ] Metrics/monitoring configured
- [ ] Alerts set up for errors
- [ ] Tested with real AWS credentials
- [ ] Load tested (100+ concurrent requests)
- [ ] CLI updated to use API URL
- [ ] Documentation updated for users
- [ ] Backup/DR plan in place

## 📝 Configuration Reference

### Environment Variables (via ConfigMap)

| Variable | Value | Purpose |
|----------|-------|---------|
| `QUEUE_NAME` | `gpu_reservations` | PGMQ queue name |
| `API_KEY_TTL_HOURS` | `2` | API key expiration |
| `ALLOWED_AWS_ROLE` | `SSOCloudDevGpuReservation` | Required AWS role |
| `AWS_REGION` | `us-east-1` | AWS region |

### Database Connection

Configured via environment variable interpolation:
```
postgresql://gpudev:${POSTGRES_PASSWORD}@postgres-primary.gpu-controlplane.svc.cluster.local:5432/gpudev
```

Password comes from existing `postgres-credentials` secret.

## 🎯 Next Steps

1. ✅ Deploy API service: `terraform apply`
2. ✅ Get public URL: `terraform output api_service_url`
3. ✅ Test endpoints: `curl http://$URL/health`
4. ⚠️ Add HTTPS with ACM (recommended)
5. ⚠️ Configure custom domain (optional)
6. ⚠️ Update CLI to use API URL
7. ⚠️ Add rate limiting before public launch
8. ⚠️ Set up monitoring/alerts

---

**Ready to deploy?** Run `terraform apply` from the `terraform-gpu-devservers` directory!

