#!/bin/bash
# Test database schema locally before applying via Terraform
#
# Usage:
#   ./test-schema.sh                    # Test against local postgres
#   ./test-schema.sh --port-forward     # Use kubectl port-forward
#   ./test-schema.sh --verify-only      # Only verify, don't apply

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default settings
POSTGRES_HOST="${POSTGRES_HOST:-localhost}"
POSTGRES_PORT="${POSTGRES_PORT:-5432}"
POSTGRES_USER="${POSTGRES_USER:-gpudev}"
POSTGRES_DB="${POSTGRES_DB:-gpudev}"
VERIFY_ONLY=false
PORT_FORWARD=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --verify-only)
            VERIFY_ONLY=true
            shift
            ;;
        --port-forward)
            PORT_FORWARD=true
            shift
            ;;
        --help|-h)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Test database schema locally before applying via Terraform"
            echo ""
            echo "Options:"
            echo "  --verify-only      Only verify tables exist, don't apply schema"
            echo "  --port-forward     Set up kubectl port-forward automatically"
            echo "  --help, -h         Show this help message"
            echo ""
            echo "Environment variables:"
            echo "  POSTGRES_HOST      Database host (default: localhost)"
            echo "  POSTGRES_PORT      Database port (default: 5432)"
            echo "  POSTGRES_USER      Database user (default: gpudev)"
            echo "  POSTGRES_PASSWORD  Database password (required)"
            echo "  POSTGRES_DB        Database name (default: gpudev)"
            echo ""
            echo "Examples:"
            echo "  # Test locally with port-forward"
            echo "  ./test-schema.sh --port-forward"
            echo ""
            echo "  # Just verify tables exist"
            echo "  ./test-schema.sh --verify-only"
            echo ""
            echo "  # Apply to custom database"
            echo "  POSTGRES_HOST=mydb.example.com ./test-schema.sh"
            exit 0
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# Check if PGPASSWORD is set
if [ -z "$POSTGRES_PASSWORD" ]; then
    echo -e "${YELLOW}POSTGRES_PASSWORD not set. Attempting to get from Kubernetes...${NC}"
    if command -v kubectl &> /dev/null; then
        export POSTGRES_PASSWORD=$(kubectl get secret -n gpu-controlplane postgres-credentials -o jsonpath='{.data.POSTGRES_PASSWORD}' 2>/dev/null | base64 -d)
        if [ -z "$POSTGRES_PASSWORD" ]; then
            echo -e "${RED}Failed to get password from Kubernetes${NC}"
            echo "Please set POSTGRES_PASSWORD environment variable"
            exit 1
        fi
        echo -e "${GREEN}Got password from Kubernetes secret${NC}"
    else
        echo -e "${RED}kubectl not found. Please set POSTGRES_PASSWORD environment variable${NC}"
        exit 1
    fi
fi

# Set up port-forward if requested
PORT_FORWARD_PID=""
if [ "$PORT_FORWARD" = true ]; then
    echo -e "${BLUE}Setting up port-forward to PostgreSQL...${NC}"
    kubectl port-forward -n gpu-controlplane svc/postgres-primary 5432:5432 &
    PORT_FORWARD_PID=$!
    
    # Wait for port-forward to be ready
    sleep 2
    
    # Cleanup on exit
    trap "echo -e '\n${YELLOW}Cleaning up port-forward...${NC}'; kill $PORT_FORWARD_PID 2>/dev/null" EXIT
fi

# Test connection
echo -e "${BLUE}Testing database connection...${NC}"
if ! PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT 1" > /dev/null 2>&1; then
    echo -e "${RED}Failed to connect to database${NC}"
    echo "Host: $POSTGRES_HOST:$POSTGRES_PORT"
    echo "User: $POSTGRES_USER"
    echo "Database: $POSTGRES_DB"
    exit 1
fi
echo -e "${GREEN}✓ Connected to database${NC}"
echo ""

if [ "$VERIFY_ONLY" = true ]; then
    # Verify tables exist
    echo -e "${BLUE}Verifying database schema...${NC}"
    echo ""
    
    TABLES=("api_users" "api_keys" "reservations" "disks" "gpu_types")
    ALL_EXIST=true
    
    for table in "${TABLES[@]}"; do
        EXISTS=$(PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc \
            "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = '$table')")
        
        if [ "$EXISTS" = "t" ]; then
            echo -e "  ${GREEN}✓${NC} $table"
        else
            echo -e "  ${RED}✗${NC} $table (missing)"
            ALL_EXIST=false
        fi
    done
    
    echo ""
    
    if [ "$ALL_EXIST" = true ]; then
        echo -e "${GREEN}All required tables exist!${NC}"
        
        # Show GPU types if table exists
        echo ""
        echo -e "${BLUE}GPU Types in database:${NC}"
        PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
            "SELECT gpu_type, instance_type, max_per_node, total_cluster_gpus, is_active FROM gpu_types ORDER BY gpu_type" 2>/dev/null || true
    else
        echo -e "${RED}Some tables are missing. Run without --verify-only to create them.${NC}"
        exit 1
    fi
else
    # Apply schema
    echo -e "${BLUE}========================================${NC}"
    echo -e "${BLUE}Applying Database Schema${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    echo -e "${BLUE}Applying schema files...${NC}"
    for file in "$SCRIPT_DIR/schema"/*.sql; do
        if [ -f "$file" ]; then
            filename=$(basename "$file")
            echo -e "  → ${filename}"
            if ! PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f "$file" > /dev/null; then
                echo -e "${RED}ERROR: Failed to apply ${filename}${NC}"
                exit 1
            fi
        fi
    done
    echo -e "${GREEN}✓ Schema applied${NC}"
    echo ""
    
    echo -e "${BLUE}Applying fixture data...${NC}"
    for file in "$SCRIPT_DIR/fixtures"/*.sql; do
        if [ -f "$file" ]; then
            filename=$(basename "$file")
            echo -e "  → ${filename}"
            if ! PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f "$file" > /dev/null; then
                echo -e "${RED}ERROR: Failed to apply ${filename}${NC}"
                exit 1
            fi
        fi
    done
    echo -e "${GREEN}✓ Fixtures applied${NC}"
    echo ""
    
    echo -e "${BLUE}========================================${NC}"
    echo -e "${GREEN}Migration completed successfully!${NC}"
    echo -e "${BLUE}========================================${NC}"
    echo ""
    
    # Show table summary
    echo -e "${BLUE}Tables in database:${NC}"
    PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "\dt"
    echo ""
    
    # Show GPU types
    echo -e "${BLUE}GPU Types configured:${NC}"
    PGPASSWORD="$POSTGRES_PASSWORD" psql -h "$POSTGRES_HOST" -p "$POSTGRES_PORT" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
        "SELECT gpu_type, instance_type, max_per_node, total_cluster_gpus, is_active FROM gpu_types ORDER BY gpu_type"
fi

