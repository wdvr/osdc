# Karpenter - Node autoscaler for CPU dev nodes
# Karpenter provisions nodes on-demand when pods are pending, and consolidates when idle.

locals {
  karpenter_namespace = "kube-system"

  # Extract Karpenter-managed GPU types
  karpenter_managed_types = {
    for gpu_type, config in local.current_config.supported_gpu_types : gpu_type => config
    if try(config.karpenter_managed, false)
  }
}

# --- IAM Role for Karpenter Controller (IRSA) ---

resource "aws_iam_role" "karpenter_controller" {
  name = "${local.workspace_prefix}-karpenter-controller"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = aws_iam_openid_connect_provider.eks.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:aud" = "sts.amazonaws.com"
            "${replace(aws_eks_cluster.gpu_dev_cluster.identity[0].oidc[0].issuer, "https://", "")}:sub" = "system:serviceaccount:${local.karpenter_namespace}:karpenter"
          }
        }
      }
    ]
  })

  tags = {
    Name        = "${var.prefix}-karpenter-controller"
    Environment = local.current_config.environment
  }
}

resource "aws_iam_role_policy" "karpenter_controller" {
  name = "${local.workspace_prefix}-karpenter-controller-policy"
  role = aws_iam_role.karpenter_controller.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ec2:CreateFleet",
          "ec2:CreateLaunchTemplate",
          "ec2:CreateTags",
          "ec2:DeleteLaunchTemplate",
          "ec2:DescribeAvailabilityZones",
          "ec2:DescribeImages",
          "ec2:DescribeInstances",
          "ec2:DescribeInstanceTypeOfferings",
          "ec2:DescribeInstanceTypes",
          "ec2:DescribeLaunchTemplates",
          "ec2:DescribeSecurityGroups",
          "ec2:DescribeSpotPriceHistory",
          "ec2:DescribeSubnets",
          "ec2:RunInstances",
          "ec2:TerminateInstances",
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = "iam:PassRole"
        Resource = aws_iam_role.eks_node_role.arn
      },
      {
        Effect = "Allow"
        Action = [
          "eks:DescribeCluster",
        ]
        Resource = aws_eks_cluster.gpu_dev_cluster.arn
      },
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
        ]
        Resource = "arn:aws:ssm:${local.current_config.aws_region}::parameter/aws/service/eks/optimized-ami/*"
      },
      {
        Effect = "Allow"
        Action = [
          "pricing:GetProducts",
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
          "sqs:GetQueueUrl",
          "sqs:ReceiveMessage",
        ]
        Resource = aws_sqs_queue.karpenter_interruption.arn
      },
    ]
  })
}

# --- SQS Queue for node interruption/spot events ---

resource "aws_sqs_queue" "karpenter_interruption" {
  name                      = "${var.prefix}-karpenter-interruption"
  message_retention_seconds = 300
  sqs_managed_sse_enabled   = true

  tags = {
    Name        = "${var.prefix}-karpenter-interruption"
    Environment = local.current_config.environment
  }
}

resource "aws_sqs_queue_policy" "karpenter_interruption" {
  queue_url = aws_sqs_queue.karpenter_interruption.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = ["events.amazonaws.com", "sqs.amazonaws.com"] }
        Action    = "sqs:SendMessage"
        Resource  = aws_sqs_queue.karpenter_interruption.arn
      }
    ]
  })
}

# EventBridge rules to forward EC2 events to Karpenter's SQS queue
resource "aws_cloudwatch_event_rule" "karpenter_instance_state_change" {
  name = "${var.prefix}-karpenter-instance-state"
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Instance State-change Notification"]
  })
}

resource "aws_cloudwatch_event_target" "karpenter_instance_state_change" {
  rule = aws_cloudwatch_event_rule.karpenter_instance_state_change.name
  arn  = aws_sqs_queue.karpenter_interruption.arn
}

resource "aws_cloudwatch_event_rule" "karpenter_spot_interruption" {
  name = "${var.prefix}-karpenter-spot-interruption"
  event_pattern = jsonencode({
    source      = ["aws.ec2"]
    detail-type = ["EC2 Spot Instance Interruption Warning"]
  })
}

resource "aws_cloudwatch_event_target" "karpenter_spot_interruption" {
  rule = aws_cloudwatch_event_rule.karpenter_spot_interruption.name
  arn  = aws_sqs_queue.karpenter_interruption.arn
}

# --- Helm Release ---

resource "helm_release" "karpenter" {
  name       = "karpenter"
  repository = "oci://public.ecr.aws/karpenter"
  chart      = "karpenter"
  version    = "1.1.1"
  namespace  = local.karpenter_namespace

  wait    = true
  timeout = 600

  set {
    name  = "settings.clusterName"
    value = aws_eks_cluster.gpu_dev_cluster.name
  }

  set {
    name  = "settings.clusterEndpoint"
    value = aws_eks_cluster.gpu_dev_cluster.endpoint
  }

  set {
    name  = "settings.interruptionQueue"
    value = aws_sqs_queue.karpenter_interruption.name
  }

  set {
    name  = "serviceAccount.annotations.eks\\.amazonaws\\.com/role-arn"
    value = aws_iam_role.karpenter_controller.arn
  }

  # Run Karpenter controller on the management CPU nodes (not Karpenter-managed nodes)
  set {
    name  = "nodeSelector.NodeType"
    value = "cpu-management"
  }

  set {
    name  = "tolerations[0].operator"
    value = "Exists"
  }

  depends_on = [
    aws_eks_cluster.gpu_dev_cluster,
    aws_iam_role_policy.karpenter_controller,
    aws_autoscaling_group.cpu_nodes, # Management CPU nodes must exist first
  ]
}

# --- EC2NodeClass per architecture ---

resource "kubernetes_manifest" "karpenter_node_class_cpu_x86" {
  manifest = {
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = "cpu-x86"
    }
    spec = {
      role = aws_iam_role.eks_node_role.name

      amiSelectorTerms = [
        { alias = "al2023@latest" }
      ]

      subnetSelectorTerms = [
        {
          tags = {
            "kubernetes.io/cluster/${aws_eks_cluster.gpu_dev_cluster.name}" = "shared"
          }
        }
      ]

      securityGroupSelectorTerms = [
        {
          tags = {
            Name = "${var.prefix}-gpu-dev-sg"
          }
        }
      ]

      blockDeviceMappings = [
        {
          deviceName = "/dev/xvda"
          ebs = {
            volumeSize          = "500Gi"
            volumeType          = "gp3"
            deleteOnTermination = true
            encrypted           = true
          }
        }
      ]

      # Extra user data (runs before nodeadm init — Karpenter handles cluster join)
      userData = <<-EOT
        #!/bin/bash
        yum install -y htop wget
        cat >/etc/sysctl.d/99-net.conf <<'SYSCTL'
        net.core.rmem_default=262144000
        net.core.rmem_max=262144000
        net.core.wmem_default=262144000
        net.core.wmem_max=262144000
        SYSCTL
        sysctl --system
      EOT
    }
  }

  depends_on = [helm_release.karpenter]
}

resource "kubernetes_manifest" "karpenter_node_class_cpu_arm" {
  manifest = {
    apiVersion = "karpenter.k8s.aws/v1"
    kind       = "EC2NodeClass"
    metadata = {
      name = "cpu-arm"
    }
    spec = {
      role = aws_iam_role.eks_node_role.name

      amiSelectorTerms = [
        { alias = "al2023@latest" }
      ]

      subnetSelectorTerms = [
        {
          tags = {
            "kubernetes.io/cluster/${aws_eks_cluster.gpu_dev_cluster.name}" = "shared"
          }
        }
      ]

      securityGroupSelectorTerms = [
        {
          tags = {
            Name = "${var.prefix}-gpu-dev-sg"
          }
        }
      ]

      blockDeviceMappings = [
        {
          deviceName = "/dev/xvda"
          ebs = {
            volumeSize          = "500Gi"
            volumeType          = "gp3"
            deleteOnTermination = true
            encrypted           = true
          }
        }
      ]

      userData = <<-EOT
        #!/bin/bash
        yum install -y htop wget
        cat >/etc/sysctl.d/99-net.conf <<'SYSCTL'
        net.core.rmem_default=262144000
        net.core.rmem_max=262144000
        net.core.wmem_default=262144000
        net.core.wmem_max=262144000
        SYSCTL
        sysctl --system
      EOT
    }
  }

  depends_on = [helm_release.karpenter]
}

# --- NodePool per CPU type ---

resource "kubernetes_manifest" "karpenter_node_pool_cpu_x86" {
  manifest = {
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "cpu-x86"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            NodeType = "gpu"
            GpuType  = "cpu-x86"
          }
        }
        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "cpu-x86"
          }
          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["amd64"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["on-demand"]
            },
            {
              key      = "node.kubernetes.io/instance-type"
              operator = "In"
              values   = [local.current_config.supported_gpu_types["cpu-x86"].instance_type]
            },
          ]

          # Consolidate idle nodes after 30 seconds (fast scale-down)
          expireAfter = "Never"
        }
      }

      limits = {
        # Max 30 nodes worth of CPU (matches previous ASG max)
        cpu = tostring(30 * (local.current_config.environment == "prod" ? 32 : 16))
      }

      disruption = {
        consolidationPolicy = "WhenEmptyOrUnderutilized"
        consolidateAfter    = "60s"
      }
    }
  }

  depends_on = [
    kubernetes_manifest.karpenter_node_class_cpu_x86,
  ]
}

resource "kubernetes_manifest" "karpenter_node_pool_cpu_arm" {
  manifest = {
    apiVersion = "karpenter.sh/v1"
    kind       = "NodePool"
    metadata = {
      name = "cpu-arm"
    }
    spec = {
      template = {
        metadata = {
          labels = {
            NodeType = "gpu"
            GpuType  = "cpu-arm"
          }
        }
        spec = {
          nodeClassRef = {
            group = "karpenter.k8s.aws"
            kind  = "EC2NodeClass"
            name  = "cpu-arm"
          }
          requirements = [
            {
              key      = "kubernetes.io/arch"
              operator = "In"
              values   = ["arm64"]
            },
            {
              key      = "karpenter.sh/capacity-type"
              operator = "In"
              values   = ["on-demand"]
            },
            {
              key      = "node.kubernetes.io/instance-type"
              operator = "In"
              values   = [local.current_config.supported_gpu_types["cpu-arm"].instance_type]
            },
          ]

          expireAfter = "Never"
        }
      }

      limits = {
        cpu = tostring(30 * (local.current_config.environment == "prod" ? 32 : 16))
      }

      disruption = {
        consolidationPolicy = "WhenEmptyOrUnderutilized"
        consolidateAfter    = "60s"
      }
    }
  }

  depends_on = [
    kubernetes_manifest.karpenter_node_class_cpu_arm,
  ]
}
