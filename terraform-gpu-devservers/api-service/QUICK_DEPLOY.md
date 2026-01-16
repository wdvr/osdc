# Quick Deploy - API Service

## ⚡ TL;DR

```bash
# From terraform-gpu-devservers directory:
terraform apply

# Get URL:
terraform output api_service_url

# Test:
curl http://$(terraform output -raw api_service_url | sed 's|http://||')/health
```

## 📋 5-Minute Deployment

### 1. Deploy (2-5 min)

```bash
cd terraform-gpu-devservers
terraform apply
# Type 'yes' when prompted
```

### 2. Wait for LoadBalancer (1-3 min)

```bash
kubectl get svc -n gpu-controlplane api-service-public -w
# Wait for EXTERNAL-IP to appear (not <pending>)
# Press Ctrl+C when you see the hostname
```

### 3. Get URL

```bash
URL=$(terraform output -raw api_service_url)
echo $URL
```

### 4. Test

```bash
# Health check
curl $URL/health | jq .

# API info
curl $URL/ | jq .

# View docs in browser
echo "$URL/docs"
```

### 5. Test Authentication

```bash
# Get AWS creds
export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id)
export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key)
export AWS_SESSION_TOKEN=$(aws configure get aws_session_token)

# Login
curl -X POST $URL/v1/auth/aws-login \
  -H "Content-Type: application/json" \
  -d "{
    \"aws_access_key_id\": \"$AWS_ACCESS_KEY_ID\",
    \"aws_secret_access_key\": \"$AWS_SECRET_ACCESS_KEY\",
    \"aws_session_token\": \"$AWS_SESSION_TOKEN\"
  }" | jq .

# Save the API key from response!
```

## ✅ Success Criteria

- [ ] Terraform apply succeeds
- [ ] 2 API service pods running
- [ ] LoadBalancer has external hostname
- [ ] Health check returns "healthy"
- [ ] Root endpoint returns API info
- [ ] AWS authentication returns API key
- [ ] Swagger docs accessible at /docs

## 🚨 If Something Goes Wrong

```bash
# Check pods
kubectl get pods -n gpu-controlplane -l app=api-service

# Check logs
kubectl logs -n gpu-controlplane -l app=api-service

# Check service
kubectl describe svc -n gpu-controlplane api-service-public

# Check LoadBalancer Controller
kubectl logs -n kube-system deployment/aws-load-balancer-controller --tail=50
```

## 🎯 What You Get

✅ **Public API endpoint** - Accessible from anywhere  
✅ **AWS DNS name** - `xxx-yyy.us-east-1.elb.amazonaws.com`  
✅ **Load balanced** - 2 replicas for HA  
✅ **Auto-scaling** - Kubernetes manages pods  
✅ **Health checks** - Automatic monitoring  
✅ **AWS IAM auth** - Integrated with your existing roles  

## 📞 Quick Commands

```bash
# URL
terraform output api_service_url

# Pod status
kubectl get pods -n gpu-controlplane -l app=api-service

# Logs
kubectl logs -n gpu-controlplane -l app=api-service --tail=50 -f

# Restart
kubectl rollout restart deployment -n gpu-controlplane api-service

# Scale
kubectl scale deployment -n gpu-controlplane api-service --replicas=5

# Delete
kubectl delete deployment -n gpu-controlplane api-service
```

---

**Total deployment time: ~5-8 minutes** ⏱️

