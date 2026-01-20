# Outputs for GPU Developer Servers

output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.gpu_dev_vpc.id
}

output "subnet_id" {
  description = "ID of the subnet"
  value       = aws_subnet.gpu_dev_subnet.id
}

output "eks_cluster_name" {
  description = "Name of the EKS cluster"
  value       = aws_eks_cluster.gpu_dev_cluster.name
}

output "eks_cluster_endpoint" {
  description = "Endpoint for EKS control plane"
  value       = aws_eks_cluster.gpu_dev_cluster.endpoint
}

output "eks_cluster_arn" {
  description = "ARN of the EKS cluster"
  value       = aws_eks_cluster.gpu_dev_cluster.arn
}

# Removed SQS and DynamoDB outputs - now using API service with PGMQ and PostgreSQL
# - reservation_queue_url / reservation_queue_arn (replaced by PGMQ)
# - reservations_table_name (replaced by PostgreSQL reservations table)
# - disks_table_name (replaced by PostgreSQL disks table)
# - servers_table_name (now using K8s API for GPU tracking)

# Removed reservation_processor_function_name output - Lambda replaced by job processor pod

output "placement_group_names" {
  description = "Names of the cluster placement groups by GPU type"
  value       = { for k, v in aws_placement_group.gpu_dev_pg : k => v.name }
}

output "security_group_id" {
  description = "ID of the security group"
  value       = aws_security_group.gpu_dev_sg.id
}

# GPU type configurations
output "supported_gpu_types" {
  description = "Supported GPU type configurations"
  value       = local.current_config.supported_gpu_types
}

# CLI configuration outputs
output "cli_config" {
  description = "Configuration for CLI tools (now uses API service)"
  value = {
    region              = local.current_config.aws_region
    cluster_name        = aws_eks_cluster.gpu_dev_cluster.name
    supported_gpu_types = local.current_config.supported_gpu_types
    # API service URL should be set via environment variable or config file
    # queue_url and reservations_table removed - CLI now uses API service
  }
  sensitive = false
}