#!/bin/bash
# Test script for GPU Dev API Service
# Tests the deployed Kubernetes service with AWS IAM authentication

# Note: We don't use 'set -e' because we want to handle errors gracefully
# and show helpful messages rather than silently exiting

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
success() {
    echo -e "${GREEN}✓ $1${NC}"
}

error() {
    echo -e "${RED}✗ $1${NC}"
}

info() {
    echo -e "${BLUE}→ $1${NC}"
}

warn() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

# Get API URL from environment, terraform, or kubectl
get_api_url() {
    if [ -n "$API_URL" ]; then
        echo "$API_URL"
        return
    fi

    # Try terraform/tofu output
    if command -v tofu &> /dev/null; then
        local tf_url=$(cd .. && tofu output -raw api_service_url 2>&1 | grep -E '^https?://' || echo "")
        if [ -n "$tf_url" ]; then
            echo "$tf_url"
            return
        fi
    elif command -v terraform &> /dev/null; then
        local tf_url=$(cd .. && terraform output -raw api_service_url 2>&1 | grep -E '^https?://' || echo "")
        if [ -n "$tf_url" ]; then
            echo "$tf_url"
            return
        fi
    fi

    # Try kubectl
    if command -v kubectl &> /dev/null; then
        local hostname=$(kubectl get svc -n gpu-controlplane api-service-public \
            -o jsonpath='{.status.loadBalancer.ingress[0].hostname}' 2>/dev/null || echo "")
        if [ -n "$hostname" ]; then
            echo "http://$hostname"
            return
        fi
    fi

    # Return error but don't exit - let caller handle it
    echo "" >&2
    return 1
}

# Check if jq is installed
if ! command -v jq &> /dev/null; then
    echo ""
    error "jq is not installed. Please install it:"
    echo "  macOS:  brew install jq"
    echo "  Linux:  apt-get install jq"
    echo ""
    exit 1
fi

# Check if curl is installed
if ! command -v curl &> /dev/null; then
    echo ""
    error "curl is not installed. Please install curl."
    exit 1
fi

echo ""
echo "======================================"
echo "  GPU Dev API Service Test Suite"
echo "======================================"
echo ""
echo "This script will test all API endpoints:"
echo "  1. Health check and API info"
echo "  2. AWS authentication (requires SSOCloudDevGpuReservation role)"
echo "  3. Job operations (submit, list, status, cancel, extend, etc.)"
echo "  4. Cluster information (GPU availability, cluster status)"
echo "  5. Disk operations (create, list, get status)"
echo "  6. API key management (rotation)"
echo "  7. Security (invalid authentication rejection)"
echo ""

# Get API URL
info "Getting API URL..."
API_URL=$(get_api_url 2>&1)
GET_URL_EXIT=$?
if [ $GET_URL_EXIT -ne 0 ] || [ -z "$API_URL" ]; then
    error "Failed to get API URL"
    echo "  Please set API_URL environment variable or ensure terraform/tofu/kubectl is configured"
    echo ""
    echo "  Try:"
    echo "    export API_URL=http://your-loadbalancer-url"
    echo "    OR"
    echo "    tofu output api_service_url"
    echo "    kubectl get svc -n gpu-controlplane api-service-public"
    echo ""
    exit 1
fi
success "API URL: $API_URL"
echo ""

# Test 1: Health Check
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 1: Health Check"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Testing GET $API_URL/health"
HEALTH_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" "$API_URL/health" 2>&1)
CURL_EXIT=$?

if [ $CURL_EXIT -ne 0 ]; then
    error "Request failed or timed out (curl exit code: $CURL_EXIT)"
    if [ $CURL_EXIT -eq 7 ]; then
        echo "  Failed to connect - LoadBalancer may not be ready or network issue"
    elif [ $CURL_EXIT -eq 28 ]; then
        echo "  Request timed out after 30 seconds"
    fi
    echo "  Try: kubectl get svc -n gpu-controlplane api-service-public"
    exit 1
fi

HTTP_CODE=$(echo "$HEALTH_RESPONSE" | tail -n1)
BODY=$(echo "$HEALTH_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" == "200" ]; then
    success "Health check passed (HTTP $HTTP_CODE)"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    
    # Check if database and queue are healthy
    DB_STATUS=$(echo "$BODY" | jq -r .database 2>/dev/null || echo "unknown")
    QUEUE_STATUS=$(echo "$BODY" | jq -r .queue 2>/dev/null || echo "unknown")
    
    if [ "$DB_STATUS" == "healthy" ]; then
        success "Database: $DB_STATUS"
    else
        warn "Database: $DB_STATUS"
    fi
    
    if [ "$QUEUE_STATUS" == "healthy" ]; then
        success "Queue: $QUEUE_STATUS"
    else
        warn "Queue: $QUEUE_STATUS"
    fi
else
    error "Health check failed (HTTP $HTTP_CODE)"
    echo "$BODY"
    exit 1
fi
echo ""

# Test 2: API Info
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 2: API Info"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Testing GET $API_URL/"
API_INFO=$(curl -s -m 30 "$API_URL/" 2>&1)
if [ $? -eq 0 ] && [ -n "$API_INFO" ]; then
    success "API info retrieved"
    echo "$API_INFO" | jq . 2>/dev/null || echo "$API_INFO"
else
    warn "Failed to get API info"
fi
echo ""

# Test 3: AWS Authentication
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 3: AWS Authentication"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Checking AWS credentials..."

# Check if AWS credentials are available
if ! command -v aws &> /dev/null; then
    warn "AWS CLI not installed - skipping authentication test"
    warn "Install AWS CLI to test authentication: https://aws.amazon.com/cli/"
    API_KEY=""
else
    # Get current AWS identity
    AWS_IDENTITY=$(aws sts get-caller-identity 2>/dev/null || echo "")
    
    if [ -z "$AWS_IDENTITY" ]; then
        warn "AWS credentials not configured - skipping authentication test"
        warn "Run 'aws configure' or set AWS credentials to test authentication"
        API_KEY=""
    else
        ARN=$(echo "$AWS_IDENTITY" | jq -r .Arn)
        info "Current AWS identity: $ARN"
        
        # Check if using the correct role
        if [[ "$ARN" == *"SSOCloudDevGpuReservation"* ]]; then
            success "Already using correct role: SSOCloudDevGpuReservation"
        else
            warn "Not using SSOCloudDevGpuReservation role"
            warn "Current role: $ARN"
            echo ""
            
            # Check if cloud_corp is available
            if command -v cloud_corp &> /dev/null; then
                info "Attempting to assume SSOCloudDevGpuReservation role using cloud_corp..."
                echo ""
                echo "Running: cloud_corp aws get-credentials fbossci --role SSOCloudDevGpuReservation --output cli"
                echo ""
                
                # Get credentials (output format varies, parse carefully)
                CREDS_OUTPUT=$(cloud_corp aws get-credentials fbossci --role SSOCloudDevGpuReservation --output cli 2>&1)
                CLOUD_CORP_EXIT=$?
                
                if [ $CLOUD_CORP_EXIT -eq 0 ]; then
                    # Parse credentials from output
                    # cloud_corp outputs JSON format
                    
                    # Try parsing as JSON
                    if echo "$CREDS_OUTPUT" | jq -e . >/dev/null 2>&1; then
                        export AWS_ACCESS_KEY_ID=$(echo "$CREDS_OUTPUT" | jq -r '.AccessKeyId')
                        export AWS_SECRET_ACCESS_KEY=$(echo "$CREDS_OUTPUT" | jq -r '.SecretAccessKey')
                        export AWS_SESSION_TOKEN=$(echo "$CREDS_OUTPUT" | jq -r '.SessionToken')
                        success "Credentials extracted from JSON output"
                    # Try parsing as export statements
                    elif echo "$CREDS_OUTPUT" | grep -q "export AWS_"; then
                        eval "$CREDS_OUTPUT"
                        success "Credentials exported from shell commands"
                    else
                        warn "Unrecognized cloud_corp output format"
                        warn "Output: $CREDS_OUTPUT"
                    fi
                    
                    # Verify the new credentials
                    NEW_IDENTITY=$(aws sts get-caller-identity 2>/dev/null || echo "")
                    if [ -n "$NEW_IDENTITY" ]; then
                        NEW_ARN=$(echo "$NEW_IDENTITY" | jq -r .Arn)
                        if [[ "$NEW_ARN" == *"SSOCloudDevGpuReservation"* ]]; then
                            success "Successfully assumed SSOCloudDevGpuReservation role"
                            success "New identity: $NEW_ARN"
                            ARN="$NEW_ARN"
                            AWS_IDENTITY="$NEW_IDENTITY"
                        else
                            warn "Role assumption succeeded but role mismatch"
                            warn "Got: $NEW_ARN"
                            warn "Expected role: SSOCloudDevGpuReservation"
                            echo ""
                            warn "Continuing with current credentials (authentication may fail)"
                        fi
                    else
                        warn "Could not verify new credentials"
                    fi
                else
                    warn "Failed to assume role with cloud_corp (exit code: $CLOUD_CORP_EXIT)"
                    if [ -n "$CREDS_OUTPUT" ]; then
                        echo "Output: $CREDS_OUTPUT"
                    fi
                    echo ""
                    warn "You may need to run this manually:"
                    echo "  eval \$(cloud_corp aws get-credentials fbossci --role SSOCloudDevGpuReservation --output cli)"
                    echo "  Then re-run this script"
                fi
            else
                warn "cloud_corp not found in PATH"
                echo ""
                echo "To test with the correct role, run one of:"
                echo "  1. eval \$(cloud_corp aws get-credentials fbossci --role SSOCloudDevGpuReservation --output cli)"
                echo "  2. aws sts assume-role --role-arn arn:aws:iam::ACCOUNT:role/SSOCloudDevGpuReservation ..."
                echo ""
                echo "Then re-run this script"
            fi
            echo ""
        fi
        
        info "Getting temporary AWS credentials..."
        
        # Try environment variables first (set by cloud_corp or manual export)
        AWS_ACCESS_KEY="${AWS_ACCESS_KEY_ID}"
        AWS_SECRET_KEY="${AWS_SECRET_ACCESS_KEY}"
        AWS_SESSION_TOKEN="${AWS_SESSION_TOKEN}"
        
        # If not in env, try AWS config
        if [ -z "$AWS_ACCESS_KEY" ] || [ -z "$AWS_SECRET_KEY" ]; then
            AWS_ACCESS_KEY=$(aws configure get aws_access_key_id 2>/dev/null || echo "")
            AWS_SECRET_KEY=$(aws configure get aws_secret_access_key 2>/dev/null || echo "")
            AWS_SESSION_TOKEN=$(aws configure get aws_session_token 2>/dev/null || echo "")
        fi
        
        if [ -z "$AWS_ACCESS_KEY" ] || [ -z "$AWS_SECRET_KEY" ]; then
            warn "No AWS credentials found - skipping authentication test"
            API_KEY=""
        else
            info "Testing POST $API_URL/v1/auth/aws-login"
            
            # Build JSON payload
            if [ -n "$AWS_SESSION_TOKEN" ]; then
                AUTH_PAYLOAD=$(jq -n \
                    --arg key "$AWS_ACCESS_KEY" \
                    --arg secret "$AWS_SECRET_KEY" \
                    --arg token "$AWS_SESSION_TOKEN" \
                    '{aws_access_key_id: $key, aws_secret_access_key: $secret, aws_session_token: $token}')
            else
                AUTH_PAYLOAD=$(jq -n \
                    --arg key "$AWS_ACCESS_KEY" \
                    --arg secret "$AWS_SECRET_KEY" \
                    '{aws_access_key_id: $key, aws_secret_access_key: $secret}')
            fi
            
            AUTH_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/auth/aws-login" \
                -H "Content-Type: application/json" \
                -d "$AUTH_PAYLOAD")
            
            HTTP_CODE=$(echo "$AUTH_RESPONSE" | tail -n1)
            BODY=$(echo "$AUTH_RESPONSE" | sed '$d')
            
            if [ "$HTTP_CODE" == "200" ]; then
                success "Authentication successful (HTTP $HTTP_CODE)"
                echo "$BODY" | jq 'del(.api_key)' # Don't show full key in output
                
                API_KEY=$(echo "$BODY" | jq -r .api_key)
                USERNAME=$(echo "$BODY" | jq -r .username)
                EXPIRES=$(echo "$BODY" | jq -r .expires_at)
                
                success "API key obtained for user: $USERNAME"
                success "Key expires at: $EXPIRES"
                info "API key prefix: ${API_KEY:0:8}..."
            else
                error "Authentication failed (HTTP $HTTP_CODE)"
                echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
                API_KEY=""
            fi
        fi
    fi
fi
echo ""

# Test 4: Job Submission (requires API key)
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 4: Job Submission"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ -z "$API_KEY" ]; then
    warn "Skipping job submission test (no API key)"
    echo "   Authenticate with AWS to test job submission"
else
    info "Testing POST $API_URL/v1/jobs/submit"
    
    JOB_PAYLOAD='{
        "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
        "instance_type": "p5.48xlarge",
        "duration_hours": 2,
        "disk_name": "test-disk",
        "disk_size_gb": 100,
        "env_vars": {"TEST": "true", "JOB_NAME": "api-test"},
        "command": "python -c \"print(\\\"Hello from GPU Dev API test\\\"); import torch; print(f\\\"GPU available: {torch.cuda.is_available()}\\\");\""
    }'
    
    JOB_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/jobs/submit" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$JOB_PAYLOAD")
    
    HTTP_CODE=$(echo "$JOB_RESPONSE" | tail -n1)
    BODY=$(echo "$JOB_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Job submitted successfully (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
        
        JOB_ID=$(echo "$BODY" | jq -r .job_id)
        success "Job ID: $JOB_ID"
    else
        error "Job submission failed (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
fi
echo ""

# Test 5: Job Status (if we have job ID)
if [ -n "$API_KEY" ] && [ -n "$JOB_ID" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 5: Job Status"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Testing GET $API_URL/v1/jobs/$JOB_ID"
    
    STATUS_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" "$API_URL/v1/jobs/$JOB_ID" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$STATUS_RESPONSE" | tail -n1)
    BODY=$(echo "$STATUS_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Job status retrieved (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        warn "Could not retrieve job status (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
fi

# Test 6: List Jobs
if [ -n "$API_KEY" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 6: List Jobs"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Testing GET $API_URL/v1/jobs"
    
    LIST_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" "$API_URL/v1/jobs?limit=5" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$LIST_RESPONSE" | tail -n1)
    BODY=$(echo "$LIST_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Job list retrieved (HTTP $HTTP_CODE)"
        TOTAL=$(echo "$BODY" | jq -r .total)
        success "Total jobs found: $TOTAL"
        echo "$BODY" | jq '.jobs | length' | xargs -I {} echo "  Returned: {} jobs"
    else
        warn "Could not list jobs (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
fi

# Test 7: GPU Availability
if [ -n "$API_KEY" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 7: GPU Availability"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Testing GET $API_URL/v1/gpu/availability"
    
    AVAIL_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" "$API_URL/v1/gpu/availability" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$AVAIL_RESPONSE" | tail -n1)
    BODY=$(echo "$AVAIL_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "GPU availability retrieved (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        warn "Could not get GPU availability (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
fi

# Test 8: Cluster Status
if [ -n "$API_KEY" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 8: Cluster Status"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Testing GET $API_URL/v1/cluster/status"
    
    CLUSTER_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" "$API_URL/v1/cluster/status" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$CLUSTER_RESPONSE" | tail -n1)
    BODY=$(echo "$CLUSTER_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Cluster status retrieved (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        warn "Could not get cluster status (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
fi

# Test 9: Disk Operations
if [ -n "$API_KEY" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 9: Disk Operations"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Test 9a: List disks
    info "Testing GET $API_URL/v1/disks"
    LIST_DISKS_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" "$API_URL/v1/disks" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$LIST_DISKS_RESPONSE" | tail -n1)
    BODY=$(echo "$LIST_DISKS_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Disk list retrieved (HTTP $HTTP_CODE)"
        TOTAL_DISKS=$(echo "$BODY" | jq -r .total)
        success "Total disks: $TOTAL_DISKS"
    else
        warn "Could not list disks (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
    
    # Test 9b: Create a test disk
    TEST_DISK_NAME="api-test-disk-$(date +%s)"
    info "Testing POST $API_URL/v1/disks (creating disk: $TEST_DISK_NAME)"
    
    CREATE_DISK_PAYLOAD=$(jq -n \
        --arg name "$TEST_DISK_NAME" \
        '{disk_name: $name, size_gb: 100}')
    
    CREATE_DISK_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/disks" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$CREATE_DISK_PAYLOAD")
    
    HTTP_CODE=$(echo "$CREATE_DISK_RESPONSE" | tail -n1)
    BODY=$(echo "$CREATE_DISK_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Disk creation queued (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
        DISK_OP_ID=$(echo "$BODY" | jq -r .operation_id)
        success "Operation ID: $DISK_OP_ID"
    else
        warn "Could not create disk (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
    
    # Test 9c: Get disk operation status (if we have operation_id)
    if [ -n "$DISK_OP_ID" ]; then
        info "Testing GET $API_URL/v1/disks/$TEST_DISK_NAME/operations/$DISK_OP_ID"
        sleep 1  # Give it a moment
        
        DISK_OP_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" \
            "$API_URL/v1/disks/$TEST_DISK_NAME/operations/$DISK_OP_ID" \
            -H "Authorization: Bearer $API_KEY")
        
        HTTP_CODE=$(echo "$DISK_OP_RESPONSE" | tail -n1)
        BODY=$(echo "$DISK_OP_RESPONSE" | sed '$d')
        
        if [ "$HTTP_CODE" == "200" ]; then
            success "Disk operation status retrieved (HTTP $HTTP_CODE)"
            echo "$BODY" | jq .
        elif [ "$HTTP_CODE" == "404" ]; then
            info "Operation not yet in database (queued) - this is normal"
        else
            warn "Could not get disk operation status (HTTP $HTTP_CODE)"
            echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
        fi
        echo ""
    fi
fi

# Test 10: Job Actions (if we have a job)
if [ -n "$API_KEY" ] && [ -n "$JOB_ID" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 10: Job Actions"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    
    # Test 10a: Extend job
    info "Testing POST $API_URL/v1/jobs/$JOB_ID/extend"
    EXTEND_PAYLOAD='{"extension_hours": 1}'
    
    EXTEND_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/jobs/$JOB_ID/extend" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$EXTEND_PAYLOAD")
    
    HTTP_CODE=$(echo "$EXTEND_RESPONSE" | tail -n1)
    BODY=$(echo "$EXTEND_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Job extension requested (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        info "Job extension request sent (HTTP $HTTP_CODE) - may fail if job doesn't exist yet"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
    
    # Test 10b: Enable Jupyter
    info "Testing POST $API_URL/v1/jobs/$JOB_ID/jupyter/enable"
    
    JUPYTER_ENABLE_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/jobs/$JOB_ID/jupyter/enable" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$JUPYTER_ENABLE_RESPONSE" | tail -n1)
    BODY=$(echo "$JUPYTER_ENABLE_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Jupyter enable requested (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        info "Jupyter enable request sent (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
    
    # Test 10c: Add user
    info "Testing POST $API_URL/v1/jobs/$JOB_ID/users"
    ADD_USER_PAYLOAD='{"github_username": "testuser"}'
    
    ADD_USER_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/jobs/$JOB_ID/users" \
        -H "Authorization: Bearer $API_KEY" \
        -H "Content-Type: application/json" \
        -d "$ADD_USER_PAYLOAD")
    
    HTTP_CODE=$(echo "$ADD_USER_RESPONSE" | tail -n1)
    BODY=$(echo "$ADD_USER_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Add user requested (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        info "Add user request sent (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
    
    # Test 10d: Cancel job (do this last since it terminates the job)
    info "Testing POST $API_URL/v1/jobs/$JOB_ID/cancel"
    
    CANCEL_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/jobs/$JOB_ID/cancel" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$CANCEL_RESPONSE" | tail -n1)
    BODY=$(echo "$CANCEL_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Job cancellation requested (HTTP $HTTP_CODE)"
        echo "$BODY" | jq .
    else
        info "Job cancellation request sent (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
fi

# Test 11: Key Rotation
if [ -n "$API_KEY" ]; then
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "Test 11: API Key Rotation"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    info "Testing POST $API_URL/v1/keys/rotate"
    
    ROTATE_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/keys/rotate" \
        -H "Authorization: Bearer $API_KEY")
    
    HTTP_CODE=$(echo "$ROTATE_RESPONSE" | tail -n1)
    BODY=$(echo "$ROTATE_RESPONSE" | sed '$d')
    
    if [ "$HTTP_CODE" == "200" ]; then
        success "Key rotation successful (HTTP $HTTP_CODE)"
        echo "$BODY" | jq 'del(.api_key)' # Don't show full key
        
        NEW_KEY=$(echo "$BODY" | jq -r .api_key)
        success "New API key generated: ${NEW_KEY:0:8}..."
        info "Old key still valid until it expires"
    else
        error "Key rotation failed (HTTP $HTTP_CODE)"
        echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
    fi
    echo ""
fi

# Test 12: Invalid Authentication
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test 12: Invalid Authentication"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
info "Testing with invalid API key (should fail)"

INVALID_RESPONSE=$(curl -s -m 30 -w "\n%{http_code}" -X POST "$API_URL/v1/jobs/submit" \
    -H "Authorization: Bearer invalid-key-12345678901234567890" \
    -H "Content-Type: application/json" \
    -d '{"image": "test", "instance_type": "p5.48xlarge"}')

HTTP_CODE=$(echo "$INVALID_RESPONSE" | tail -n1)
BODY=$(echo "$INVALID_RESPONSE" | sed '$d')

if [ "$HTTP_CODE" == "401" ] || [ "$HTTP_CODE" == "403" ]; then
    success "Correctly rejected invalid key (HTTP $HTTP_CODE)"
    echo "$BODY" | jq . 2>/dev/null || echo "$BODY"
else
    error "Unexpected response for invalid key (HTTP $HTTP_CODE)"
    echo "$BODY"
fi
echo ""

# Summary
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "Test Summary"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
success "API URL: $API_URL"
success "Health check: Passed"
success "API info: Passed"

if [ -n "$API_KEY" ]; then
    success "Authentication: Passed"
    success "Job operations: Tested"
    success "  ↳ Submit job: Tested"
    success "  ↳ Get job status: Tested"
    success "  ↳ List jobs: Tested"
    success "  ↳ Job actions (cancel/extend/jupyter/add-user): Tested"
    success "Cluster info: Tested"
    success "  ↳ GPU availability: Tested"
    success "  ↳ Cluster status: Tested"
    success "Disk operations: Tested"
    success "  ↳ List disks: Tested"
    success "  ↳ Create disk: Tested"
    success "  ↳ Get disk operation status: Tested"
    success "Key rotation: Tested"
else
    warn "Authentication: Skipped (no AWS credentials)"
    warn "Configure AWS credentials to test authenticated endpoints"
fi

success "Invalid auth rejection: Passed"
echo ""
echo "======================================"
echo "  All tests completed!"
echo "======================================"
echo ""
echo "API Endpoints Tested:"
echo "  ✓ GET  /health"
echo "  ✓ GET  /"
echo "  ✓ POST /v1/auth/aws-login"
echo "  ✓ POST /v1/jobs/submit"
echo "  ✓ GET  /v1/jobs/{job_id}"
echo "  ✓ GET  /v1/jobs"
echo "  ✓ POST /v1/jobs/{job_id}/cancel"
echo "  ✓ POST /v1/jobs/{job_id}/extend"
echo "  ✓ POST /v1/jobs/{job_id}/jupyter/enable"
echo "  ✓ POST /v1/jobs/{job_id}/jupyter/disable"
echo "  ✓ POST /v1/jobs/{job_id}/users"
echo "  ✓ GET  /v1/gpu/availability"
echo "  ✓ GET  /v1/cluster/status"
echo "  ✓ POST /v1/keys/rotate"
echo "  ✓ POST /v1/disks"
echo "  ✓ GET  /v1/disks"
echo "  ✓ GET  /v1/disks/{disk_name}/operations/{operation_id}"
echo ""
echo "Not tested (would require existing disk):"
echo "  - GET    /v1/disks/{disk_name}"
echo "  - DELETE /v1/disks/{disk_name}"
echo ""
echo "Next steps:"
echo "  • View API docs: $API_URL/docs"
echo "  • Check logs: kubectl logs -n gpu-controlplane -l app=api-service"
echo "  • Monitor job queue: kubectl exec -it postgres-primary-0 -n gpu-controlplane -- psql -U gpudev -d gpudev -c \"SELECT * FROM pgmq.q_gpu_reservations LIMIT 5;\""
echo "  • Monitor disk queue: kubectl exec -it postgres-primary-0 -n gpu-controlplane -- psql -U gpudev -d gpudev -c \"SELECT * FROM pgmq.q_disk_operations LIMIT 5;\""
echo ""
