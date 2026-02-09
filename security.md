# GPU Dev Server Security Review

**Date:** 2026-02-09
**Reviewer:** Claude Code Security Analysis
**Scope:** Multi-cloud GPU development server infrastructure
**Version:** Based on commit main branch

---

## Executive Summary

This security review analyzed the GPU Dev Server codebase focusing on authentication, authorization, input validation, secrets management, network security, container security, and infrastructure RBAC.

### Risk Summary

| Severity | Count | Description |
|----------|-------|-------------|
| **Critical** | 2 | Immediate action required |
| **High** | 5 | Should be addressed before production |
| **Medium** | 8 | Address in near-term roadmap |
| **Low** | 6 | Best practice improvements |

### Critical Findings Overview

1. **CRITICAL-001:** AWS credentials transmitted over API in plaintext (HTTPS required)
2. **CRITICAL-002:** Privileged containers with full host access (BuildKit)

---

## Detailed Findings

---

### 1. Authentication & Authorization

#### CRITICAL-001: AWS Credentials Transmitted to API Service

**Severity:** Critical
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/api_client.py`
**Lines:** 172-178

**Description:**
The CLI sends raw AWS credentials (access key, secret key, session token) to the API service for authentication. While this is verified server-side via AWS STS, transmitting secrets over the network creates risk.

```python
def authenticate(self, force: bool = False) -> bool:
    # ...
    # Get AWS credentials
    aws_creds = self._get_aws_credentials()

    # Call API login endpoint
    url = f"{self.api_url}/v1/auth/aws-login"
    response = requests.post(url, json=aws_creds, timeout=30)  # Credentials sent in body
```

**Impact:**
- If HTTPS is not enforced, credentials could be intercepted
- Credentials are logged in request bodies on some API gateways
- Increases attack surface for credential theft

**Remediation:**
1. Ensure HTTPS is strictly enforced (CloudFront already configured)
2. Consider using AWS STS AssumeRole with web identity for server-side verification
3. Add certificate pinning in CLI for defense-in-depth
4. Implement request body sanitization in logging

---

#### HIGH-001: API Key Storage Without Encryption at Rest

**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/api_client.py`
**Lines:** 104-122

**Description:**
API keys are stored in a local JSON file with restricted permissions (0o600), but without encryption at rest.

```python
def _save_credentials(self, api_key: str, expires_at: str) -> None:
    try:
        self.CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)

        creds = {
            "api_key": api_key,
            "expires_at": expires_at,
        }

        with open(self.CREDENTIALS_FILE, "w") as f:
            json.dump(creds, f, indent=2)

        # Set restrictive permissions (owner read/write only)
        os.chmod(self.CREDENTIALS_FILE, 0o600)
```

**Impact:**
- Malware or compromised user account could read API keys
- Keys persist until expiration even if user logs out

**Remediation:**
1. Use OS-native credential storage (macOS Keychain, Linux Secret Service)
2. Consider encrypting credentials file with user-derived key
3. Add explicit logout command that deletes stored credentials

---

#### HIGH-002: Missing Authorization Check on Job Actions

**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`
**Lines:** 1170-1212

**Description:**
The cancel_job, extend_job, enable_jupyter, disable_jupyter, and add_user endpoints verify API key but do not verify the user owns the target job before queuing the action.

```python
@app.post("/v1/jobs/{job_id}/cancel", response_model=JobActionResponse)
async def cancel_job(
    job_id: str,
    user_info: dict[str, Any] = Security(verify_api_key)
) -> JobActionResponse:
    # No check that user_info["username"] owns job_id
    try:
        async with db_pool.acquire() as conn:
            message = {
                "action": "cancel",
                "job_id": job_id,
                # ...
            }
            msg_id = await conn.fetchval("SELECT pgmq.send($1, $2)", ...)
```

**Impact:**
- Any authenticated user could cancel/modify another user's jobs
- Authorization check happens in job processor, but action is already queued

**Remediation:**
1. Add authorization check before queuing message:
```python
# Verify user owns the job before allowing action
row = await conn.fetchrow(
    "SELECT user_id FROM reservations WHERE reservation_id = $1",
    job_id
)
if not row or row["user_id"] != user_info["username"]:
    raise HTTPException(status_code=403, detail="Not authorized")
```

---

#### MEDIUM-001: Role Verification Uses Exact String Match

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`
**Lines:** 1697-1706

**Description:**
The AWS role verification uses exact string comparison, which is correct but could be vulnerable if role names are case-sensitive differently across AWS accounts.

```python
role = extract_role_from_arn(identity['arn'])
if role != ALLOWED_AWS_ROLE:
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=(
            f"Access denied. Required role: {ALLOWED_AWS_ROLE}, "
            f"got: {role or 'none'}"
        )
    )
```

**Impact:**
- Role checking is secure but inflexible
- No support for multiple allowed roles

**Remediation:**
1. Support comma-separated list of allowed roles
2. Consider case-insensitive comparison for robustness
3. Log access attempts with role information for audit

---

### 2. Input Validation

#### HIGH-003: Dynamic SQL Query Construction

**Severity:** High
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`
**Lines:** 1074-1124

**Description:**
The list_jobs endpoint constructs SQL queries dynamically based on user input. While parameters are properly parameterized, the status filter is split and used in query construction.

```python
if status_filter:
    statuses = [s.strip() for s in status_filter.split(",")]
    placeholders = ", ".join(f"${i}" for i in range(param_index, param_index + len(statuses)))
    query_conditions.append(f"status IN ({placeholders})")
    query_params.extend(statuses)

# Later:
query = f"""
    SELECT ...
    FROM reservations
    WHERE {where_clause}
    ...
"""
```

**Impact:**
- While parameterized, the pattern is risky and could lead to injection if modified
- Status values are not validated against allowed enum

**Remediation:**
1. Validate status values against allowed enum before use:
```python
ALLOWED_STATUSES = {'active', 'preparing', 'queued', 'pending', 'cancelled', 'failed', 'expired'}
statuses = [s.strip().lower() for s in status_filter.split(",")]
invalid = set(statuses) - ALLOWED_STATUSES
if invalid:
    raise HTTPException(400, f"Invalid status values: {invalid}")
```

---

#### MEDIUM-002: Insufficient Disk Name Validation

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/disks.py`
**Lines:** 100-103

**Description:**
Disk name validation is performed but inconsistently between CLI and API, with different max length limits.

```python
# CLI disks.py
if not re.match(r'^[a-zA-Z0-9_-]+$', disk_name):
    print(f"Error: Disk name must contain only letters, numbers, hyphens, and underscores")
    return None
# No max length check

# Interactive.py (line 702)
if len(disk_name) > 50:
    return "Disk name too long (max 50 characters)"
```

**Impact:**
- Inconsistent validation could allow malformed names
- Long disk names could cause issues with EBS tags (max 256 chars)

**Remediation:**
1. Centralize validation in a shared module
2. Apply consistent max length (e.g., 50 characters)
3. Validate at API layer as well (currently done)

---

#### MEDIUM-003: GitHub Username Not Fully Validated

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/interactive.py`
**Lines:** 534-546

**Description:**
GitHub username validation is basic and doesn't match GitHub's actual rules (must start with alphanumeric, no consecutive hyphens).

```python
def _validate_github_username(username: str) -> bool:
    if not username or not username.strip():
        return "GitHub username cannot be empty"

    username = username.strip()
    if not username.replace("-", "").replace("_", "").replace(".", "").isalnum():
        return "Invalid GitHub username format"

    if len(username) > 39:  # GitHub's max username length
        return "GitHub username too long (max 39 characters)"
```

**Impact:**
- Invalid usernames could be passed to GitHub API
- Potential for injection in SSH authorized_keys if not sanitized server-side

**Remediation:**
1. Use proper GitHub username regex: `^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$`
2. Verify username exists via GitHub API before adding

---

#### MEDIUM-004: Environment Variable Injection Risk

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`
**Lines:** 889-932

**Description:**
Job submission accepts env_vars from user input which are passed to the reservation processor without sanitization.

```python
# Extract processor-required fields from env_vars
env_vars = job.env_vars or {}

message = {
    "action": "create_reservation",
    # ...
    "env_vars": job.env_vars,  # User-controlled
    # ...
}
```

**Impact:**
- Malicious env vars could override critical settings
- Could potentially affect pod security context if not filtered

**Remediation:**
1. Whitelist allowed environment variable names
2. Filter out reserved/dangerous variable names (PATH, LD_PRELOAD, etc.)
3. Validate variable values don't contain shell injection characters

---

### 3. Secrets Management

#### MEDIUM-005: Database Password in Environment Variables

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/charts/gpu-dev-server/templates/api-service/deployment.yaml`
**Lines:** 45-52

**Description:**
Database password is passed via environment variable from Kubernetes secret, which is standard practice but could be exposed in pod specs.

```yaml
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ include "gpu-dev-server.postgresSecretName" . }}
      key: {{ .Values.postgres.auth.existingSecretKey }}
```

**Impact:**
- Environment variables visible in `kubectl describe pod`
- Could be logged or exposed via /proc filesystem

**Remediation:**
1. Consider using secret volumes mounted as files
2. Enable pod security policy to restrict /proc access
3. Use external secrets manager (AWS Secrets Manager, HashiCorp Vault)

---

#### LOW-001: Random Password Generation Without Entropy Verification

**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/charts/gpu-dev-server/templates/postgres/secret.yaml`
**Lines:** 12-16

**Description:**
PostgreSQL password is generated using Helm's randAlphaNum if not provided.

```yaml
{{- if .Values.postgres.auth.password }}
POSTGRES_PASSWORD: {{ .Values.postgres.auth.password | b64enc | quote }}
{{- else }}
POSTGRES_PASSWORD: {{ randAlphaNum 32 | b64enc | quote }}
{{- end }}
```

**Impact:**
- Password is generated at template render time, not cryptographically verified
- Regenerated on each Helm upgrade if not stored

**Remediation:**
1. Store generated passwords in external secret manager
2. Use pre-generated passwords for production
3. Consider using AWS IAM database authentication

---

### 4. Network Security

#### HIGH-004: No Network Policies Defined

**Severity:** High
**Files:** `/Users/wouterdevriendt/dev/osdc/charts/gpu-dev-server/templates/`

**Description:**
No Kubernetes NetworkPolicy resources are defined in the Helm chart. All pods can communicate with each other and external services without restriction.

**Impact:**
- Lateral movement possible if pod is compromised
- No ingress/egress control on sensitive services
- Database accessible from any pod in cluster

**Remediation:**
1. Add NetworkPolicy for postgres to only allow connections from API and processor:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: postgres-network-policy
spec:
  podSelector:
    matchLabels:
      app: postgres
  policyTypes:
    - Ingress
  ingress:
    - from:
        - podSelector:
            matchLabels:
              app: api-service
        - podSelector:
            matchLabels:
              app: reservation-processor
      ports:
        - port: 5432
```

---

#### MEDIUM-006: SSH Proxy Domain Validation

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/cli-tools/gpu-dev-cli/gpu_dev_cli/ssh_proxy.py`
**Lines:** 22-28

**Description:**
SSH proxy domain validation only checks for `.devservers.io` suffix, which could be spoofed with subdomains.

```python
if ".test.devservers.io" in target_host:
    proxy_host = "ssh.test.devservers.io"
elif ".devservers.io" in target_host:
    proxy_host = "ssh.devservers.io"
else:
    print(f"Error: Unsupported domain: {target_host}", file=sys.stderr)
    sys.exit(1)
```

**Impact:**
- Malicious hostname like `fake.devservers.io.attacker.com` would pass validation
- Could redirect SSH traffic to attacker-controlled server

**Remediation:**
1. Use proper domain suffix validation:
```python
if target_host.endswith(".test.devservers.io"):
    proxy_host = "ssh.test.devservers.io"
elif target_host.endswith(".devservers.io"):
    proxy_host = "ssh.devservers.io"
```

---

### 5. Container Security

#### CRITICAL-002: Privileged BuildKit Container

**Severity:** Critical
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/reservation-processor-service/processor/buildkit_job.py`
**Lines:** 169-172

**Description:**
BuildKit containers run with full privileged access, allowing container escape and host compromise.

```python
security_context=client.V1SecurityContext(
    privileged=True,
    allow_privilege_escalation=True,
),
```

**Impact:**
- Container can escape to host system
- Full access to host kernel and devices
- Could compromise entire cluster if exploited

**Remediation:**
1. Use rootless BuildKit mode
2. Consider running BuildKit on dedicated isolated nodes
3. Use sysbox or kata containers for nested container builds
4. Implement strict network isolation for build pods
5. Add pod security admission to prevent privileged pods in other namespaces

---

#### HIGH-005: GPU Pods Run with CAP_SYS_ADMIN

**Severity:** High
**Documentation:** `CLAUDE.md` Line 180

**Description:**
GPU development pods are granted CAP_SYS_ADMIN capability for NVIDIA profiling support. While documented and intentional, this grants extensive system privileges.

**Impact:**
- Allows container escape via various kernel exploits
- Can mount filesystems, modify system settings
- Users have elevated privileges on development server

**Remediation:**
1. Only enable CAP_SYS_ADMIN on profiling-dedicated nodes
2. Implement audit logging for privileged operations
3. Consider using hardware profiling passthrough instead
4. Add security monitoring for privileged container activity

---

#### MEDIUM-007: No Pod Security Standards Enforcement

**Severity:** Medium
**Files:** Helm chart templates

**Description:**
No PodSecurityPolicy, PodSecurityAdmission, or OPA/Gatekeeper policies are defined to enforce pod security standards.

**Impact:**
- Developers could potentially create privileged pods
- No enforcement of security baselines
- Easier lateral movement after compromise

**Remediation:**
1. Enable Pod Security Admission in enforce mode
2. Use baseline or restricted security profile for workload namespaces
3. Implement Gatekeeper/Kyverno for custom policies

---

### 6. Infrastructure RBAC

#### MEDIUM-008: Broad ClusterRole Permissions

**Severity:** Medium
**File:** `/Users/wouterdevriendt/dev/osdc/charts/gpu-dev-server/templates/reservation-processor/rbac.yaml`
**Lines:** 1-37

**Description:**
Reservation processor has broad permissions including secrets read/write and pod exec across all namespaces via ClusterRole.

```yaml
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/log", "pods/status", "pods/exec"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: [""]
    resources: ["configmaps", "secrets"]
    verbs: ["get", "list", "watch", "create", "update", "patch"]
```

**Impact:**
- If reservation processor is compromised, attacker has broad cluster access
- Can read secrets from any namespace
- Can exec into any pod

**Remediation:**
1. Use namespace-scoped Role instead of ClusterRole where possible
2. Restrict secrets access to specific secret names
3. Limit pod/exec permissions to gpu-dev namespace only:
```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: reservation-processor-role
  namespace: gpu-dev
rules:
  - apiGroups: [""]
    resources: ["pods", "pods/exec"]
    verbs: ["create", "delete", "get", "list"]
```

---

#### LOW-002: Service Account Token Auto-Mount

**Severity:** Low
**Files:** Service account YAML templates

**Description:**
Service accounts don't explicitly disable automatic token mounting for pods that don't need K8s API access.

**Remediation:**
1. Add `automountServiceAccountToken: false` for pods that don't need K8s API access
2. Only enable for pods that require cluster access

---

#### LOW-003: No Audit Logging Configuration

**Severity:** Low
**Scope:** Infrastructure-wide

**Description:**
No audit logging configuration found for tracking security-relevant events.

**Remediation:**
1. Enable Kubernetes audit logging
2. Forward audit logs to SIEM
3. Alert on suspicious patterns (privilege escalation, secret access)

---

#### LOW-004: Missing Resource Quotas

**Severity:** Low
**Files:** Helm chart templates

**Description:**
No ResourceQuotas defined to limit resource consumption per namespace.

**Remediation:**
1. Add ResourceQuotas for gpu-dev namespace
2. Limit pod count, CPU, memory, GPU per user
3. Prevent resource exhaustion attacks

---

#### LOW-005: Availability Updater Has Read-Only Access (Good Practice)

**Severity:** Informational (Positive Finding)
**File:** `/Users/wouterdevriendt/dev/osdc/charts/gpu-dev-server/templates/availability-updater/rbac.yaml`

**Description:**
The availability updater follows principle of least privilege with only read access to nodes and pods.

```yaml
rules:
  - apiGroups: [""]
    resources: ["nodes"]
    verbs: ["get", "list", "watch"]
  - apiGroups: [""]
    resources: ["pods", "pods/status"]
    verbs: ["get", "list", "watch"]
```

This is a positive security practice that should be applied to other services.

---

#### LOW-006: API Key Expiration Not Enforced on Existing Sessions

**Severity:** Low
**File:** `/Users/wouterdevriendt/dev/osdc/terraform-gpu-devservers/api-service/app/main.py`

**Description:**
While API keys have expiration times, there's no mechanism to immediately revoke active sessions or force logout.

**Remediation:**
1. Implement key revocation endpoint
2. Add background cleanup of expired keys
3. Consider shorter TTL with refresh token pattern

---

## Recommendations Summary

### Immediate Actions (Critical/High)

1. **Implement HTTPS enforcement** - Verify CloudFront terminates all HTTP traffic
2. **Add authorization checks** before queuing job actions
3. **Restrict BuildKit** to isolated nodes or use rootless mode
4. **Add NetworkPolicies** for database isolation
5. **Fix SSH proxy domain validation** to use proper suffix matching

### Near-Term (Medium Priority)

6. Centralize and standardize input validation
7. Implement Pod Security Admission
8. Convert ClusterRoles to namespace-scoped Roles where possible
9. Add environment variable whitelisting for job submission
10. Implement audit logging

### Long-Term (Low Priority)

11. Migrate to OS-native credential storage
12. Add resource quotas
13. Implement secret rotation automation
14. Consider external secrets management

---

## Security Testing Recommendations

1. **Penetration Testing:** Test API endpoints for authorization bypass
2. **Container Escape Testing:** Verify isolation of privileged containers
3. **Credential Security:** Test for credential exposure in logs/memory
4. **Network Segmentation:** Verify pods cannot reach unauthorized services
5. **RBAC Testing:** Verify service accounts have minimal required permissions

---

## Compliance Considerations

- **SOC 2:** Implement audit logging, access controls, encryption at rest
- **GDPR:** User data handling, right to deletion (disk delete feature exists)
- **ISO 27001:** Document security policies, implement monitoring

---

*This report was generated through static code analysis. Dynamic testing and runtime security assessment are recommended for comprehensive coverage.*
