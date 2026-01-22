# CloudFront distribution for API service
# Provides HTTPS endpoint with AWS-managed SSL certificate
# No custom domain needed - uses *.cloudfront.net with free SSL

resource "aws_cloudfront_distribution" "api_service" {
  enabled = true
  comment = "GPU Dev API Service - HTTPS endpoint"

  # Point to the Classic LoadBalancer created by Kubernetes
  origin {
    domain_name = try(
      kubernetes_service.api_service_public.status[0].load_balancer[0].ingress[0].hostname,
      "pending"
    )
    origin_id = "api-service-elb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  # Default cache behavior - NO caching for API responses
  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD", "OPTIONS"]
    target_origin_id       = "api-service-elb"
    viewer_protocol_policy = "redirect-to-https"

    # Use AWS managed policies for API (no caching)
    # CachingDisabled: No caching, always fetch from origin
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    # AllViewer: Forward all headers, query strings, cookies to origin
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3"

    # Compress responses for bandwidth savings
    compress = true
  }

  # Required - no geo restrictions
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # Use AWS-provided certificate for *.cloudfront.net
  viewer_certificate {
    cloudfront_default_certificate = true
    minimum_protocol_version       = "TLSv1.2_2021"
  }

  tags = {
    Name        = "${var.prefix}-api-cloudfront"
    Environment = local.current_config.environment
    Purpose     = "HTTPS endpoint for API service"
  }
}

# Primary API URL - CloudFront HTTPS endpoint
output "api_service_url" {
  description = "API service URL (HTTPS via CloudFront) - use this for GPU_DEV_API_URL"
  value       = "https://${aws_cloudfront_distribution.api_service.domain_name}"
}

output "api_service_cloudfront_domain" {
  description = "CloudFront domain name (without https://)"
  value       = aws_cloudfront_distribution.api_service.domain_name
}

output "api_service_loadbalancer_url" {
  description = "Direct LoadBalancer URL (HTTP only - for debugging)"
  value = try(
    "http://${kubernetes_service.api_service_public.status[0].load_balancer[0].ingress[0].hostname}",
    "pending"
  )
}

