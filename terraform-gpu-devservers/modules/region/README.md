# Region Module

Manages all GPU dev server infrastructure in a single AWS region.

## Usage from root

```hcl
# Root main.tf
provider "aws" {
  region = "us-east-2"
  alias  = "east2"
}

provider "aws" {
  region = "us-east-1"
  alias  = "east1"
}

module "east2" {
  source      = "./modules/region"
  region      = "us-east-2"
  environment = "prod"
  key_pair_name = var.key_pair_name
  docker_image_uri = local.latest_image_uri
  baked_ami_id     = local.baked_ami_id  # built once at root level
  gpu_types = {
    h100 = { instance_type = "p5.48xlarge", ... }
    b200 = { instance_type = "p6-b200.48xlarge", ... }
    ...
  }
  providers = {
    aws        = aws.east2
    kubernetes = kubernetes.east2
    helm       = helm.east2
  }
}

module "east1" {
  source      = "./modules/region"
  region      = "us-east-1"
  environment = "prod-east1"
  key_pair_name = var.key_pair_name
  docker_image_uri = local.latest_image_uri  # ECR replication
  baked_ami_id     = aws_ami_copy.east1.id   # copied from east2
  spot_gpu_types   = "b300,b200,h200,h100,a100,t4,l4,rtxpro6000"
  gpu_types = {
    b300 = { instance_type = "p6-b300.48xlarge", use_spot = true, ... }
    ...
  }
  providers = {
    aws        = aws.east1
    kubernetes = kubernetes.east1
    helm       = helm.east1
  }
}

# AMI: build once in east2, copy to east1
resource "aws_ami_copy" "east1" {
  provider          = aws.east1
  name              = "gpu-dev-baked-east1-copy"
  source_ami_id     = local.baked_ami_id
  source_ami_region = "us-east-2"
}
```

## Migration plan

1. `tofu workspace select prod-east1 && tofu destroy` (nobody using east1)
2. Move all .tf files from root into modules/region/
3. Replace `local.current_config` refs with `var.*` inputs
4. Replace `terraform.workspace` refs with `var.environment`
5. Create root main.tf with module calls + provider aliases
6. `tofu import module.east2.<resource> <id>` for all prod resources
7. Add east1 module — fresh create
8. `tofu workspace delete prod-east1`

## Key refactoring notes

- `terraform.workspace` → `var.environment` everywhere in the module
- `local.current_config.aws_region` → `var.region`
- `local.current_config.supported_gpu_types` → `var.gpu_types`
- `local.workspace_prefix` → `"${var.prefix}-${var.environment}"`
- Lambda zip paths: `${path.root}/lambda/*.zip` (root builds, module references)
- Template paths: `${path.root}/templates/*.sh`
- Provider passthrough: module needs aws, kubernetes, helm providers
- Kubernetes/helm providers need per-cluster config (cluster endpoint + token)

## Estimated effort

- ~4-6 hours focused work
- Most time: moving files + fixing references + state import for prod
- Risk: state import for 191 prod resources is tedious but mechanical
