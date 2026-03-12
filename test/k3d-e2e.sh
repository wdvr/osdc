#!/bin/bash
# K3d End-to-End Test
# Validates the full reservation flow in a local k3d cluster.
# Exit 0 on success, non-zero on failure.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
API_URL="http://localhost:8000"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; exit 1; }
info() { echo -e "${YELLOW}[INFO]${NC} $1"; }

# Bypass corporate proxy for local k3d connections
export NO_PROXY="${NO_PROXY:-},0.0.0.0,localhost,127.0.0.1"
export no_proxy="${no_proxy:-},0.0.0.0,localhost,127.0.0.1"

# Detect architecture for GPU type
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then
    GPU_TYPE="cpu-arm"
else
    GPU_TYPE="cpu-x86"
fi

# Use a known GitHub user with SSH keys (for SSH key injection)
GITHUB_USER="${E2E_GITHUB_USER:-wdvr}"

# -------------------------------------------------------------------
# Step 1: Run local/setup.sh (creates cluster + images + helm install)
# -------------------------------------------------------------------
info "Step 1: Setting up k3d cluster..."
"$ROOT_DIR/local/setup.sh"
pass "k3d cluster + helm install complete"

# -------------------------------------------------------------------
# Step 2: Wait for all pods to be ready
# -------------------------------------------------------------------
info "Step 2: Waiting for all pods to be ready..."
kubectl wait --for=condition=ready pod --all -n gpu-controlplane --timeout=180s
pass "All controlplane pods ready"

# -------------------------------------------------------------------
# Step 3: Health check
# -------------------------------------------------------------------
info "Step 3: API health check..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "$API_URL/health")
[ "$HTTP_CODE" = "200" ] || fail "Health check returned $HTTP_CODE"
pass "API health check OK"

# -------------------------------------------------------------------
# Step 4: Login (local dev user)
# -------------------------------------------------------------------
info "Step 4: Logging in as local dev user..."
LOGIN_RESPONSE=$(curl -s -X POST "$API_URL/v1/auth/aws-login" \
    -H "Content-Type: application/json" \
    -d '{"aws_access_key_id":"AKIAIOSFODNN7EXAMPLE","aws_secret_access_key":"wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"}')

API_KEY=$(echo "$LOGIN_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('api_key',''))" 2>/dev/null)
[ -n "$API_KEY" ] || fail "Login failed: $LOGIN_RESPONSE"
pass "Login successful (got API key)"

# -------------------------------------------------------------------
# Step 5: Check GPU availability
# -------------------------------------------------------------------
info "Step 5: Checking GPU availability..."
AVAIL_RESPONSE=$(curl -s -H "Authorization: Bearer $API_KEY" "$API_URL/v1/gpu/availability")
AVAIL=$(echo "$AVAIL_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['availability'].get('$GPU_TYPE',{}).get('available',0))" 2>/dev/null)
[ "$AVAIL" -gt 0 ] 2>/dev/null || fail "No $GPU_TYPE availability: $AVAIL_RESPONSE"
pass "GPU availability: $AVAIL $GPU_TYPE slots"

# -------------------------------------------------------------------
# Step 6: Reserve a CPU pod
# -------------------------------------------------------------------
info "Step 6: Reserving $GPU_TYPE pod..."
RESERVE_RESPONSE=$(curl -s -X POST "$API_URL/v1/jobs/submit" \
    -H "Authorization: Bearer $API_KEY" \
    -H "Content-Type: application/json" \
    -d "{
        \"instance_type\": \"$GPU_TYPE\",
        \"duration_hours\": 1,
        \"env_vars\": {
            \"GPU_TYPE\": \"$GPU_TYPE\",
            \"GPU_COUNT\": \"0\",
            \"GITHUB_USER\": \"$GITHUB_USER\"
        }
    }")

JOB_ID=$(echo "$RESERVE_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))" 2>/dev/null)
[ -n "$JOB_ID" ] || fail "Reserve failed: $RESERVE_RESPONSE"
pass "Job submitted: $JOB_ID"

# -------------------------------------------------------------------
# Step 7: Wait for pod to be ready (poll status)
# -------------------------------------------------------------------
info "Step 7: Waiting for reservation to become active..."
MAX_WAIT=300
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS_RESPONSE=$(curl -s -H "Authorization: Bearer $API_KEY" \
        "$API_URL/v1/jobs/$JOB_ID")
    STATUS=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status',''))" 2>/dev/null)

    if [ "$STATUS" = "active" ]; then
        break
    elif [ "$STATUS" = "failed" ] || [ "$STATUS" = "expired" ]; then
        fail "Reservation $STATUS: $STATUS_RESPONSE"
    fi

    sleep 5
    ELAPSED=$((ELAPSED + 5))
    info "  Status: $STATUS ($ELAPSED/${MAX_WAIT}s)"
done

[ "$STATUS" = "active" ] || fail "Reservation did not become active within ${MAX_WAIT}s (status: $STATUS)"

POD_NAME=$(echo "$STATUS_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin).get('pod_name',''))" 2>/dev/null)
pass "Reservation active, pod: $POD_NAME"

# -------------------------------------------------------------------
# Step 8: Connect via kubectl exec
# -------------------------------------------------------------------
info "Step 8: Testing kubectl exec connectivity..."
EXEC_OUTPUT=$(kubectl exec -n gpu-dev "$POD_NAME" -- echo "hello-from-e2e" 2>&1) || fail "kubectl exec failed: $EXEC_OUTPUT"
echo "$EXEC_OUTPUT" | grep -q "hello-from-e2e" || fail "Unexpected exec output: $EXEC_OUTPUT"
pass "kubectl exec works"

# -------------------------------------------------------------------
# Step 9: Cancel reservation
# -------------------------------------------------------------------
info "Step 9: Cancelling reservation..."
CANCEL_RESPONSE=$(curl -s -X POST "$API_URL/v1/jobs/$JOB_ID/cancel" \
    -H "Authorization: Bearer $API_KEY")
pass "Cancel request sent"

# -------------------------------------------------------------------
# Step 10: Verify cleanup (pod deleted)
# -------------------------------------------------------------------
info "Step 10: Verifying cleanup..."
sleep 15
if kubectl get pod -n gpu-dev "$POD_NAME" >/dev/null 2>&1; then
    # Pod might still be terminating
    sleep 15
    if kubectl get pod -n gpu-dev "$POD_NAME" >/dev/null 2>&1; then
        POD_STATUS=$(kubectl get pod -n gpu-dev "$POD_NAME" -o jsonpath='{.status.phase}' 2>/dev/null)
        [ "$POD_STATUS" = "Terminating" ] || fail "Pod $POD_NAME still exists after cancel (status: $POD_STATUS)"
        # Wait a bit more for terminating pods
        sleep 15
    fi
fi
pass "Pod cleaned up"

# -------------------------------------------------------------------
echo ""
echo -e "${GREEN}=== All E2E tests passed! ===${NC}"
exit 0
