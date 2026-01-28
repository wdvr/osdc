# =============================================================================
# Registry Access via Port Forwarding
# =============================================================================
# Uses kubectl port-forward or SSH tunnel for secure local access
# No public exposure - registry only accessible via internal network

variable "registry_username" {
  description = "Username for registry authentication"
  type        = string
  default     = "admin"
}

variable "registry_password" {
  description = "Password for registry authentication (set via TF_VAR_registry_password)"
  type        = string
  sensitive   = true
  default     = ""
}

# Generate htpasswd entry for basic auth
resource "null_resource" "generate_htpasswd" {
  count = var.registry_password != "" ? 1 : 0

  triggers = {
    username = var.registry_username
    password = sha256(var.registry_password)
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e
      
      # Find htpasswd command
      if command -v htpasswd &> /dev/null; then
        HTPASSWD_CMD="htpasswd"
      elif [ -f "/usr/bin/htpasswd" ]; then
        HTPASSWD_CMD="/usr/bin/htpasswd"
      else
        echo "ERROR: htpasswd not found. Install with: apt-get install apache2-utils"
        exit 1
      fi

      # Generate htpasswd file with bcrypt
      echo "${var.registry_password}" | $HTPASSWD_CMD -iB -c /tmp/registry-htpasswd ${var.registry_username}
      
      echo "✓ Generated htpasswd file"
    EOF
  }
}

# Kubernetes secret for htpasswd
resource "kubernetes_secret" "registry_htpasswd" {
  depends_on = [
    kubernetes_namespace.controlplane,
    null_resource.generate_htpasswd
  ]

  metadata {
    name      = "registry-htpasswd"
    namespace = kubernetes_namespace.controlplane.metadata[0].name
  }

  data = {
    htpasswd = var.registry_password != "" ? file("/tmp/registry-htpasswd") : ""
  }

  lifecycle {
    ignore_changes = [data]
  }
}

# Setup kubectl port-forward for registry access during build
resource "null_resource" "setup_port_forward" {
  depends_on = [
    kubernetes_deployment.registry_ghcr,
    kubernetes_service.registry_ghcr
  ]

  triggers = {
    registry_deployment = kubernetes_deployment.registry_ghcr.id
    service             = kubernetes_service.registry_ghcr.id
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e
      
      echo "==================================================================="
      echo "Setting up port forwarding to registry..."
      echo "==================================================================="
      
      # Kill any existing port-forward on 5000
      echo "Checking for existing port-forwards on port 5000..."
      lsof -ti:5000 | xargs kill -9 2>/dev/null || true
      pkill -f "port-forward.*registry-ghcr" 2>/dev/null || true
      sleep 2
      
      # Verify registry pods are running
      echo "Verifying registry pods are running..."
      PODS=$(kubectl get pods -n gpu-controlplane -l app=registry-cache,upstream=ghcr --field-selector=status.phase=Running -o name 2>/dev/null | wc -l)
      if [ "$PODS" -eq 0 ]; then
        echo "ERROR: No running registry pods found"
        kubectl get pods -n gpu-controlplane -l app=registry-cache,upstream=ghcr
        exit 1
      fi
      echo "✓ Found $PODS running registry pod(s)"
      
      # Start kubectl port-forward in background
      echo "Starting kubectl port-forward..."
      kubectl port-forward -n gpu-controlplane svc/registry-ghcr 5000:5000 > /tmp/registry-port-forward.log 2>&1 &
      PORT_FORWARD_PID=$!
      echo $PORT_FORWARD_PID > /tmp/registry-port-forward.pid
      echo "✓ Port-forward started (PID: $PORT_FORWARD_PID)"
      
      # Wait for port-forward to be ready with better testing
      echo "Waiting for port-forward to be ready..."
      for i in {1..30}; do
        # Check if process is still running
        if ! kill -0 $PORT_FORWARD_PID 2>/dev/null; then
          echo "ERROR: Port-forward process died"
          cat /tmp/registry-port-forward.log
          exit 1
        fi
        
        # Test actual connectivity
        if curl -sf --max-time 2 http://localhost:5000/v2/ > /dev/null 2>&1; then
          echo "✓ Registry is accessible at localhost:5000"
          
          # Additional test: verify we can list catalog
          if curl -sf --max-time 2 http://localhost:5000/v2/_catalog > /dev/null 2>&1; then
            echo "✓ Registry API is fully functional"
            break
          fi
        fi
        
        if [ $i -eq 30 ]; then
          echo "ERROR: Port-forward did not become ready after 30 seconds"
          echo "Port-forward logs:"
          cat /tmp/registry-port-forward.log
          echo ""
          echo "Registry pod status:"
          kubectl get pods -n gpu-controlplane -l app=registry-cache,upstream=ghcr
          echo ""
          echo "Registry service:"
          kubectl get svc -n gpu-controlplane registry-ghcr
          exit 1
        fi
        
        echo "  Attempt $i/30..."
        sleep 1
      done
      
      # Docker login if password is set
      if [ -n "${var.registry_password}" ]; then
        echo ""
        echo "Logging in to registry..."
        echo "${var.registry_password}" | docker login localhost:5000 -u "${var.registry_username}" --password-stdin
        echo "✓ Docker login successful"
      fi
      
      echo ""
      echo "==================================================================="
      echo "✓ Registry is ready for builds at localhost:5000"
      echo "  Port-forward PID: $PORT_FORWARD_PID"
      echo "  Log file: /tmp/registry-port-forward.log"
      echo "==================================================================="
    EOF
  }
}

# Cleanup port-forward after builds complete
resource "null_resource" "cleanup_port_forward" {
  depends_on = [
    # Only wait for builds that use the registry (not ssh_proxy which uses ECR)
    null_resource.api_service_build,
    null_resource.reservation_processor_build,
    null_resource.availability_updater_build,
    null_resource.reservation_expiry_build,
    null_resource.docker_build_and_push
  ]

  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e
      
      echo "Cleaning up port-forward..."
      
      if [ -f /tmp/registry-port-forward.pid ]; then
        PID=$(cat /tmp/registry-port-forward.pid)
        if kill -0 $PID 2>/dev/null; then
          kill $PID || true
          echo "✓ Port-forward stopped (PID: $PID)"
        fi
        rm /tmp/registry-port-forward.pid
      fi
      
      # Also kill any kubectl port-forward to registry
      pkill -f "port-forward.*registry-ghcr" || true
      
      echo "✓ Cleanup complete"
    EOF
  }
}

# Local variable for registry URL (localhost during builds)
locals {
  registry_url = "localhost:5000"
}

# Outputs
output "registry_url" {
  description = "Registry URL for Docker operations (via port-forward)"
  value       = "localhost:5000 (via kubectl port-forward or SSH tunnel)"
}

output "registry_access_instructions" {
  description = "How to access the registry"
  sensitive   = true
  value       = <<-EOT
    Registry Access (Secure - No Public Exposure):
    
    The registry is ONLY accessible via port-forward or SSH tunnel.
    During 'tofu apply', port-forward is automatically set up.
    
    Manual Access Options:
    
    Option 1: kubectl port-forward (recommended)
    -------------------------------------------
    kubectl port-forward -n gpu-controlplane svc/registry-ghcr 5000:5000
    
    Then in another terminal:
    docker login localhost:5000 ${var.registry_password != "" ? "-u ${var.registry_username}" : "(no auth required)"}
    docker push localhost:5000/myimage:v1
    
    Option 2: SSH tunnel via node
    ------------------------------
    # Get a node IP
    kubectl get nodes -o wide
    
    # Create SSH tunnel
    ssh -L 5000:registry.internal.${var.prefix}.local:5000 ec2-user@<node-ip> -N
    
    Then use localhost:5000 as above.
    
    Security: Registry is NOT exposed to the internet. Only accessible via:
    - kubectl (requires cluster access)
    - SSH to nodes (requires node access)
    - From within the cluster (pods use internal service)
  EOT
}

output "registry_internal_url" {
  description = "Internal registry URL for Kubernetes pods"
  value       = "registry.internal.${var.prefix}.local:5000"
}
