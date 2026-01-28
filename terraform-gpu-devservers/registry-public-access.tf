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

# Note: Port-forward management is now embedded in each build resource
# Each build starts its own port-forward, uses it, and cleans it up
# This is more reliable than trying to maintain a long-running background port-forward

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
    kubectl port-forward -n gpu-controlplane svc/registry-native 5000:5000
    
    Then in another terminal:
    docker login localhost:5000 ${var.registry_password != "" ? "-u ${var.registry_username}" : "(no auth required)"}
    docker push 127.0.0.1:5000/myimage:v1
    
    Option 2: SSH tunnel via node
    ------------------------------
    # Get a node IP
    kubectl get nodes -o wide
    
    # Create SSH tunnel (registry DNS resolves to internal NLB)
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
