output "cluster_endpoint" {
  value = aws_eks_cluster.gpu_dev_cluster.endpoint
}

output "cluster_name" {
  value = aws_eks_cluster.gpu_dev_cluster.name
}

output "vpc_id" {
  value = aws_vpc.gpu_dev_vpc.id
}

output "reservation_table_name" {
  value = aws_dynamodb_table.gpu_reservations.name
}

output "queue_url" {
  value = aws_sqs_queue.gpu_reservation_queue.url
}

output "hosted_zone_id" {
  value = try(aws_route53_zone.subdomain[0].zone_id, "")
}
