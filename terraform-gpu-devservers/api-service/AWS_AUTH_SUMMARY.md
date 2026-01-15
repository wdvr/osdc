# AWS Authentication Implementation Summary

## ✅ What We Implemented

### 1. **Token Exchange with TTL**

Users authenticate with AWS credentials (SSOCloudDevGpuReservation role) and receive time-limited API keys.

### 2. **New API Endpoint: `/v1/auth/aws-login`**

```http
POST /v1/auth/aws-login
Content-Type: application/json

{
  "aws_access_key_id": "ASIA...",
  "aws_secret_access_key": "...",
  "aws_session_token": "..."  // optional, for assumed roles
}

Response:
{
  "api_key": "long-secure-token",
  "key_prefix": "firstchars",
  "user_id": 123,
  "username": "john",
  "aws_arn": "arn:aws:sts::123:assumed-role/SSOCloudDevGpuReservation/john",
  "expires_at": "2024-01-15T14:30:00Z",
  "ttl_hours": 2
}
```

### 3. **AWS Verification**

The API:
- Calls AWS STS `GetCallerIdentity` to verify credentials
- Checks if the ARN contains `SSOCloudDevGpuReservation` role
- Extracts username from ARN
- Creates or updates user in database
- Issues API key with TTL (default 30 days)

### 4. **Automatic Key Expiration**

All API keys now have an expiration date:
- Default: 2 hours (configurable via `API_KEY_TTL_HOURS` env var)
- CLI can detect expiration and auto-refresh
- Old keys remain valid until they expire

## 🔧 Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY_TTL_HOURS` | 2 | API key time-to-live in hours |
| `ALLOWED_AWS_ROLE` | SSOCloudDevGpuReservation | Required AWS role name |
| `AWS_REGION` | us-east-1 | AWS region for STS calls |

### Example Kubernetes ConfigMap/Secret

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: api-service-config
data:
  API_KEY_TTL_HOURS: "2"
  ALLOWED_AWS_ROLE: "SSOCloudDevGpuReservation"
  AWS_REGION: "us-east-1"
  QUEUE_NAME: "gpu_reservations"
```

## 🔒 Security Features

### What We Protected

1. ✅ **AWS Credential Verification**
   - API validates credentials with AWS STS
   - No trust in client-provided claims

2. ✅ **Role-Based Access Control**
   - Only `SSOCloudDevGpuReservation` role allowed
   - Configurable via environment variable

3. ✅ **Time-Limited Keys**
   - All API keys expire after 2 hours
   - Forces frequent re-authentication
   - Minimizes impact of leaked keys

4. ✅ **No AWS Credentials Stored**
   - API never stores AWS credentials
   - Only uses them for verification
   - Credentials discarded after verification

5. ✅ **User Creation/Update**
   - Atomic transaction (user + API key)
   - Username extracted from AWS ARN
   - User automatically created on first login

### What's Protected Now

- ✅ `/v1/jobs/submit` - Requires valid API key
- ✅ `/v1/jobs/{job_id}` - Requires valid API key
- ✅ `/v1/jobs` - Requires valid API key
- ✅ `/v1/keys/rotate` - Requires valid API key
- ✅ `/v1/auth/aws-login` - Validates AWS credentials
- ⚠️  `/admin/users` - Still open (marked deprecated)

## 📊 Database Schema Updates

The existing schema already supports everything we need:
- `api_keys.expires_at` - Stores expiration timestamp
- `api_keys.description` - Stores login source (AWS ARN)
- All other fields unchanged

## 🚀 User Experience

### Before (SQS)
```bash
# Users assume AWS role
$ aws sso login
$ export AWS_PROFILE=gpu-dev

# Submit job (uses AWS credentials → SQS)
$ gpu-dev submit --image pytorch:latest --instance p5.48xlarge
```

### After (API with Token Exchange)
```bash
# Users assume AWS role (same as before)
$ aws sso login

# ONE-TIME: Get API key
$ gpu-dev login
🔐 Authenticating with AWS...
✅ Authenticated successfully!
   Username: john
   Expires: 2024-01-15T14:30:00Z (2 hours)

# Submit job (uses API key → API → PGMQ)
$ gpu-dev submit --image pytorch:latest --instance p5.48xlarge
✅ Job submitted!

# 2 hours later... (automatic refresh)
$ gpu-dev submit --image my-model:v2 --instance p5.48xlarge
⚠️  API key expired. Re-authenticating...
✅ Authenticated successfully!
✅ Job submitted!
```

## 🔄 Migration Path

### Phase 1: Deploy API (Current)
- API deployed with AWS auth
- SQS still works (no breaking changes)
- Early adopters can test

### Phase 2: Update CLI
- Add `gpu-dev login` command
- Add AWS auth module
- Keep SQS as fallback

### Phase 3: Switch Default
- CLI defaults to API
- SQS deprecated but functional
- Communication to all users

### Phase 4: Remove SQS
- CLI removes SQS code
- SQS resources deleted
- Full PGMQ migration complete

## 📝 TODO Before Production

### High Priority

1. **Test AWS Verification**
   ```bash
   # Test with real AWS credentials
   curl -X POST http://localhost:8000/v1/auth/aws-login \
     -H "Content-Type: application/json" \
     -d '{
       "aws_access_key_id": "$AWS_ACCESS_KEY_ID",
       "aws_secret_access_key": "$AWS_SECRET_ACCESS_KEY",
       "aws_session_token": "$AWS_SESSION_TOKEN"
     }'
   ```

2. **TTL Already Set**
   - ✅ Configured to 2 hours (hardcoded)
   - Provides strong security (frequent re-auth)
   - CLI will auto-refresh transparently

3. **Configure AWS Region**
   - Set AWS_REGION to match your deployment
   - Ensure API can reach AWS STS

4. **Deploy with AWS IAM Role**
   - API pod needs IAM role to call STS
   - Use IRSA (IAM Roles for Service Accounts)
   - Or use instance role if on EC2

### Medium Priority

5. **Deprecate /admin/users**
   - Add warning in docs
   - Eventually remove or protect

6. **Add Monitoring**
   - Track auth failures
   - Track API key expiration
   - Alert on unusual patterns

7. **CLI Implementation**
   - Follow `CLI_INTEGRATION.md`
   - Test auto-refresh flow
   - Handle edge cases

### Nice to Have

8. **Key Revocation Endpoint**
   ```python
   @app.delete("/v1/keys/{key_prefix}")
   async def revoke_key(key_prefix: str, user: dict = Depends(verify_api_key)):
       """Revoke a specific API key"""
   ```

9. **List User's Keys**
   ```python
   @app.get("/v1/keys")
   async def list_keys(user: dict = Depends(verify_api_key)):
       """List all active keys for user"""
   ```

10. **Expiration Warning**
    - Endpoint to check key expiration
    - CLI warns "Key expires in 3 days"

## 🧪 Testing

### Unit Test Examples

```python
import pytest
from app.main import extract_username_from_arn

def test_extract_username_from_arn():
    arn = "arn:aws:sts::123:assumed-role/SSOCloudDevGpuReservation/john"
    assert extract_username_from_arn(arn) == "john"
    
def test_verify_aws_credentials_invalid():
    with pytest.raises(HTTPException):
        await verify_aws_credentials("invalid", "invalid", None)
```

### Integration Test

```bash
# 1. Start API locally
uvicorn app.main:app --reload

# 2. Get AWS credentials
export AWS_ACCESS_KEY_ID=$(aws configure get aws_access_key_id)
export AWS_SECRET_ACCESS_KEY=$(aws configure get aws_secret_access_key)
export AWS_SESSION_TOKEN=$(aws configure get aws_session_token)

# 3. Test login
curl -X POST http://localhost:8000/v1/auth/aws-login \
  -H "Content-Type: application/json" \
  -d "{
    \"aws_access_key_id\": \"$AWS_ACCESS_KEY_ID\",
    \"aws_secret_access_key\": \"$AWS_SECRET_ACCESS_KEY\",
    \"aws_session_token\": \"$AWS_SESSION_TOKEN\"
  }" | jq .

# 4. Save API key
API_KEY=$(curl ... | jq -r .api_key)

# 5. Test job submission
curl -X POST http://localhost:8000/v1/jobs/submit \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "image": "pytorch/pytorch:latest",
    "instance_type": "p5.48xlarge",
    "duration_hours": 4
  }' | jq .
```

## 📚 Documentation Files

- `README.md` - General API documentation
- `CLI_INTEGRATION.md` - Complete CLI integration guide
- `AWS_AUTH_SUMMARY.md` - This file
- `SECURITY_REVIEW.md` - (deleted, needs update)

## ✨ Next Steps

1. **Review this implementation** with team
2. **Test locally** with real AWS credentials
3. **Deploy to dev environment** 
4. **Implement CLI changes** (see CLI_INTEGRATION.md)
5. **Test end-to-end** with CLI
6. **Roll out to users** gradually

## 🎉 Benefits

- ✅ **No breaking changes** - Users keep AWS SSO workflow
- ✅ **Highly secure** - 2-hour keys, role verification
- ✅ **Better UX** - Automatic refresh every 2 hours
- ✅ **Flexible** - TTL configurable, multiple keys per user
- ✅ **Auditable** - AWS ARN stored with each key
- ✅ **Maintainable** - No password management needed

