# CloudFront has been replaced by ALB-based HTTPS routing (see alb.tf).
# API is now served at https://api.<domain> via the ALB.
#
# MIGRATION STEPS:
# 1. tofu state rm aws_cloudfront_distribution.api_service
# 2. tofu state rm kubernetes_service.api_service_public  (if not already removed)
# 3. Delete this file
# 4. tofu apply
#
# After migration, the api_service_url output in alb.tf provides the new URL.
