# API Endpoint Security Review

## 📋 All Exposed Endpoints

### ✅ Public Endpoints (No Authentication Required)

#### 1. `GET /`
**Purpose:** API information and documentation links  
**Security:** ✅ Safe - Read-only, no sensitive data  
**Risk:** None  
**Action:** Keep as-is

#### 2. `GET /health`
**Purpose:** Health check for monitoring  
**Security:** ✅ Safe - Returns service status only  
**Risk:** Low - Reveals service is running and queue name  
**Action:** Keep as-is (needed for load balancers/monitoring)

#### 3. `POST /v1/auth/aws-login`
**Purpose:** Exchange AWS credentials for API key  
**Security:** ✅ Protected
- Validates credentials with AWS STS
- Checks for required role (`SSOCloudDevGpuReservation`)
- Rate limiting recommended (not yet implemented)

**Risk:** Medium without rate limiting
- Could be used for credential stuffing
- AWS will throttle STS calls

**Action:** ✅ Keep - This is the main authentication endpoint  
**TODO:** Add rate limiting before production

---

### 🔐 Authenticated Endpoints (Require Valid API Key)

#### 4. `POST /v1/jobs/submit`
**Purpose:** Submit GPU job to queue  
**Security:** ✅ Protected
- Requires valid API key (2-hour expiration)
- User info extracted from token
- Input validation via Pydantic

**Risk:** Low
- Users can only submit jobs for themselves
- No privilege escalation possible

**Action:** ✅ Keep as-is

#### 5. `GET /v1/jobs/{job_id}`
**Purpose:** Get job status  
**Security:** ⚠️ Needs improvement
- Requires valid API key ✅
- **Missing:** No check if job belongs to requesting user
- Any authenticated user can query any job ID

**Risk:** Medium - Information disclosure
- Users can see other users' job status
- Job IDs are UUIDs (hard to guess but not impossible)

**Action:** ⚠️ TODO - Add user ownership check:
```python
# Verify job belongs to user
job = await get_job_from_db(job_id)
if job['user_id'] != user['user_id']:
    raise HTTPException(403, "Not your job")
```

#### 6. `GET /v1/jobs`
**Purpose:** List user's jobs  
**Security:** ✅ Will be protected (when implemented)
- Currently returns empty list (not implemented)
- Should filter by user_id when implemented

**Risk:** None (not implemented)

**Action:** ✅ Implement with user filtering

#### 7. `POST /v1/keys/rotate`
**Purpose:** Generate new API key for user  
**Security:** ✅ Protected
- Requires valid API key
- Creates key for authenticated user only
- Old keys remain valid until expiration

**Risk:** Low
- Users can create multiple keys (intentional)
- Could be abused to create many keys

**Action:** ✅ Keep as-is  
**Optional:** Add limit on active keys per user

---

## 🗑️ Removed Endpoints

#### ❌ `POST /admin/users` - REMOVED ✅
**Was:** Create user without AWS authentication  
**Risk:** Critical - Anyone could create accounts  
**Action:** ✅ Removed in this update

---

## 🔒 Security Summary

### Current State

| Endpoint | Auth Required | User Isolation | Risk Level | Status |
|----------|---------------|----------------|------------|--------|
| `GET /` | No | N/A | None | ✅ Safe |
| `GET /health` | No | N/A | Low | ✅ Safe |
| `POST /v1/auth/aws-login` | AWS Creds | N/A | Medium* | ✅ Safe |
| `POST /v1/jobs/submit` | API Key | Yes | Low | ✅ Safe |
| `GET /v1/jobs/{job_id}` | API Key | **No** | Medium | ⚠️ Fix needed |
| `GET /v1/jobs` | API Key | TBD | Low | ⚠️ Not implemented |
| `POST /v1/keys/rotate` | API Key | Yes | Low | ✅ Safe |

\* Medium risk without rate limiting

### Security Strengths ✅

1. **AWS-Based Authentication**
   - No password management
   - Role verification required
   - Credentials validated by AWS

2. **Time-Limited Keys**
   - 2-hour expiration
   - Automatic refresh by CLI
   - Reduces leaked key impact

3. **Input Validation**
   - Pydantic models validate all inputs
   - Type checking enforced
   - SQL injection prevented (parameterized queries)

4. **No Admin Backdoors**
   - `/admin/users` removed
   - All users must authenticate via AWS
   - No way to bypass authentication

5. **Connection Security**
   - Database connection pooling
   - Prepared statements (asyncpg)
   - No raw SQL concatenation

### Security Gaps ⚠️

1. **No Rate Limiting**
   - `/v1/auth/aws-login` could be abused
   - Job submission could be spammed
   - **Recommendation:** Add slowapi or similar

2. **Job Ownership Not Verified**
   - `/v1/jobs/{job_id}` doesn't check ownership
   - Users can query other users' jobs
   - **Recommendation:** Add ownership check

3. **No Request Logging**
   - Hard to detect abuse
   - No audit trail
   - **Recommendation:** Add structured logging

4. **No Key Limits**
   - Users can create unlimited keys
   - Could fill database
   - **Recommendation:** Limit to 10 active keys per user

5. **No CORS Configuration**
   - Not an issue if CLI-only
   - Needed if web UI added
   - **Recommendation:** Configure if needed

---

## 🎯 Recommended Actions

### High Priority (Before Production)

1. **Add Job Ownership Check** ⚠️
   ```python
   @app.get("/v1/jobs/{job_id}")
   async def get_job_status(job_id: str, user: dict = Depends(verify_api_key)):
       # TODO: Implement job tracking table
       # job = await conn.fetchrow("SELECT * FROM jobs WHERE job_id = $1", job_id)
       # if job['user_id'] != user['user_id']:
       #     raise HTTPException(403, "Access denied")
       pass
   ```

2. **Add Rate Limiting**
   ```python
   from slowapi import Limiter, _rate_limit_exceeded_handler
   from slowapi.util import get_remote_address
   
   limiter = Limiter(key_func=get_remote_address)
   app.state.limiter = limiter
   
   @app.post("/v1/auth/aws-login")
   @limiter.limit("5/minute")  # 5 logins per minute per IP
   async def aws_login(...):
       ...
   ```

3. **Add Request Logging**
   ```python
   import logging
   
   @app.middleware("http")
   async def log_requests(request: Request, call_next):
       logger.info(f"{request.method} {request.url.path}", extra={
           "ip": request.client.host,
           "user_agent": request.headers.get("user-agent")
       })
       response = await call_next(request)
       return response
   ```

### Medium Priority

4. **Implement Job Tracking**
   - Create `jobs` table to track submissions
   - Store job_id, user_id, status, timestamps
   - Enable proper job status queries

5. **Limit Active Keys Per User**
   ```python
   # Before creating new key
   active_keys = await conn.fetchval("""
       SELECT COUNT(*) FROM api_keys 
       WHERE user_id = $1 AND is_active = true 
       AND (expires_at IS NULL OR expires_at > NOW())
   """, user_id)
   
   if active_keys >= 10:
       raise HTTPException(429, "Too many active keys")
   ```

6. **Add Metrics/Monitoring**
   - Track auth failures
   - Track job submissions per user
   - Alert on anomalies

### Low Priority (Nice to Have)

7. **Add API Key Revocation Endpoint**
   ```python
   @app.delete("/v1/keys/{key_prefix}")
   async def revoke_key(key_prefix: str, user: dict = Depends(verify_api_key)):
       """Revoke a specific API key"""
       await conn.execute("""
           UPDATE api_keys SET is_active = false
           WHERE user_id = $1 AND key_prefix = $2
       """, user['user_id'], key_prefix)
   ```

8. **Add Key Listing Endpoint**
   ```python
   @app.get("/v1/keys")
   async def list_keys(user: dict = Depends(verify_api_key)):
       """List all active keys for user"""
       keys = await conn.fetch("""
           SELECT key_prefix, created_at, expires_at, last_used_at, description
           FROM api_keys
           WHERE user_id = $1 AND is_active = true
           ORDER BY created_at DESC
       """, user['user_id'])
       return {"keys": [dict(k) for k in keys]}
   ```

---

## 🧪 Security Testing Checklist

Before deploying to production:

- [ ] Test AWS authentication with invalid credentials
- [ ] Test AWS authentication with wrong role
- [ ] Test API key expiration (wait 2 hours or mock time)
- [ ] Test job submission with expired key
- [ ] Test job submission with invalid key
- [ ] Attempt to access another user's job (should fail after fix)
- [ ] Test rate limiting (once implemented)
- [ ] Verify all SQL queries use parameterization
- [ ] Run security scanner (bandit, safety)
- [ ] Review all error messages (no sensitive data leaked)
- [ ] Test HTTPS enforcement at ALB level
- [ ] Verify database credentials are from secrets

---

## 📊 Risk Assessment

### Overall Risk Level: **LOW-MEDIUM** ✅

**Justification:**
- Strong authentication (AWS-based)
- Time-limited keys (2 hours)
- No admin backdoors
- Input validation present
- Main gap: job ownership check (medium impact)

**With Recommended Fixes: LOW** ✅

After implementing:
1. Job ownership verification
2. Rate limiting
3. Request logging

The API will be production-ready with strong security posture.

---

## 🔐 Compliance Notes

### Data Protection
- No passwords stored (AWS-based auth)
- API keys hashed (SHA-256)
- No PII stored except username (from AWS ARN)
- Database credentials in Kubernetes secrets

### Audit Trail
- API key creation logged (description field)
- Last used timestamp tracked
- TODO: Add request logging for full audit trail

### Access Control
- Role-based (AWS IAM role required)
- Time-limited access (2-hour keys)
- User isolation (jobs tied to user_id)

---

## ✅ Conclusion

The API is **secure for development/testing** and will be **production-ready** after implementing the high-priority recommendations:

1. ✅ Remove `/admin/users` - **DONE**
2. ⚠️ Add job ownership check - **TODO**
3. ⚠️ Add rate limiting - **TODO**
4. ⚠️ Add request logging - **TODO**

All other endpoints are properly secured with AWS authentication and time-limited API keys.

