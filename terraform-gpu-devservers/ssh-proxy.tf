# SSH Domain Name Infrastructure
# Provides domain-based SSH access using Route53 and simplified routing

# DynamoDB table to store domain name -> NodePort mappings
resource "aws_dynamodb_table" "ssh_domain_mappings" {
  name           = "${var.prefix}-ssh-domain-mappings"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "domain_name"

  attribute {
    name = "domain_name"
    type = "S"
  }

  # TTL for automatic cleanup of expired mappings
  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name        = "${var.prefix}-ssh-domain-mappings"
    Environment = local.current_config.environment
  }
}
