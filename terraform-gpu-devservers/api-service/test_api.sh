#!/bin/bash
# Quick test script for the GPU Dev API

set -e

API_URL="${API_URL:-http://localhost:8000}"

echo "=== Testing GPU Dev API ==="
echo "API URL: $API_URL"
echo

# 1. Health check
echo "1. Health Check..."
curl -s "$API_URL/health" | jq .
echo

# 2. Create a test user
echo "2. Creating test user..."
RESPONSE=$(curl -s -X POST "$API_URL/admin/users" \
  -H "Content-Type: application/json" \
  -d '{
    "username": "testuser",
    "email": "test@example.com"
  }')

echo "$RESPONSE" | jq .
API_KEY=$(echo "$RESPONSE" | jq -r .api_key)

if [ "$API_KEY" == "null" ]; then
  echo "Failed to create user (might already exist)"
  echo "Please create a user manually or use existing API key"
  exit 1
fi

echo
echo "✅ API Key: $API_KEY"
echo "   (Save this for later use!)"
echo

# 3. Test authenticated endpoint - submit job
echo "3. Submitting test job..."
curl -s -X POST "$API_URL/v1/jobs/submit" \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "image": "pytorch/pytorch:2.1.0-cuda12.1-cudnn8-runtime",
    "instance_type": "p5.48xlarge",
    "duration_hours": 4,
    "disk_name": "test-disk",
    "env_vars": {"WANDB_API_KEY": "test123"},
    "command": "python train.py"
  }' | jq .
echo

# 4. Test key rotation
echo "4. Testing key rotation..."
NEW_KEY_RESPONSE=$(curl -s -X POST "$API_URL/v1/keys/rotate" \
  -H "Authorization: Bearer $API_KEY")
echo "$NEW_KEY_RESPONSE" | jq .
echo

# 5. Test invalid auth
echo "5. Testing invalid auth (should fail)..."
curl -s -X POST "$API_URL/v1/jobs/submit" \
  -H "Authorization: Bearer invalid-key-12345" \
  -H "Content-Type: application/json" \
  -d '{"image": "test", "instance_type": "p5.48xlarge"}' | jq .
echo

echo "=== All tests completed ==="

