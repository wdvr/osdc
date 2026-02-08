# Setup Docker certificates for local development
# This ensures the local Docker daemon trusts the registry's self-signed certificate

resource "null_resource" "setup_docker_certs" {
  # Re-run whenever the certificate changes
  triggers = {
    cert_content = filesha256("${path.module}/.certs/registry.crt")
  }

  provisioner "local-exec" {
    command = <<-EOT
      set -e
      
      echo "==================================================================="
      echo "Setting up Docker registry certificates"
      echo "==================================================================="
      
      # Create Docker cert directories for host.docker.internal (for build step)
      for port in 5001 5002 5003 5004 5005; do
        CERT_DIR="$HOME/.docker/certs.d/host.docker.internal:$port"
        echo "Creating $CERT_DIR"
        mkdir -p "$CERT_DIR"
        cp ${path.module}/.certs/registry.crt "$CERT_DIR/ca.crt"
        echo "✓ Installed certificate for host.docker.internal:$port"
      done
      
      # Create cert directory for cluster-internal registry name (for push step)
      CLUSTER_CERT_DIR="$HOME/.docker/certs.d/registry.internal.pytorch-gpu-dev.local:5000"
      echo "Creating $CLUSTER_CERT_DIR"
      mkdir -p "$CLUSTER_CERT_DIR"
      cp ${path.module}/.certs/registry.crt "$CLUSTER_CERT_DIR/ca.crt"
      echo "✓ Installed certificate for registry.internal.pytorch-gpu-dev.local:5000"
      
      # Also add to system keychain for curl/other tools
      echo ""
      echo "Adding certificate to system keychain..."
      
      # Remove old cert if it exists
      security delete-certificate -c "registry-native" -t 2>/dev/null || true
      
      # Add new cert
      security add-trusted-cert -d -r trustRoot \
        -k ~/Library/Keychains/login.keychain-db \
        ${path.module}/.certs/registry.crt
      
      echo ""
      echo "==================================================================="
      echo "✓ Docker certificate setup complete!"
      echo "==================================================================="
      echo ""
      echo "IMPORTANT: You must restart Docker Desktop for changes to take effect:"
      echo "  killall Docker && sleep 3 && open -a Docker"
      echo ""
      echo "Wait 30-60 seconds for Docker to fully restart before building images."
      echo "==================================================================="
    EOT
  }
}
