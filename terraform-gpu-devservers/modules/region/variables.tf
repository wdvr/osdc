variable "region" {
  type = string
}

variable "prefix" {
  type    = string
  default = "pytorch-gpu-dev"
}

variable "environment" {
  type = string
}

variable "vpc_cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "subnet_cidr" {
  type    = string
  default = "10.0.0.0/20"
}

variable "key_pair_name" {
  type = string
}

variable "gpu_types" {
  type = map(object({
    instance_type       = string
    instance_types      = optional(list(string))
    instance_count      = number
    gpus_per_instance   = number
    use_placement_group = bool
    architecture        = string
    efa_network_cards   = number
    use_spot            = optional(bool, false)
    virtual             = optional(bool, false)
    mig_parent          = optional(string)
  }))
}

variable "spot_gpu_types" {
  type    = string
  default = ""
}

variable "gpu_subnet_assignments" {
  type    = map(string)
  default = {}
}

variable "capacity_reservations" {
  type    = map(list(object({
    id             = string
    key            = string
    instance_count = number
    efa_network_cards = optional(number)
    mig_profile    = optional(string)
  })))
  default = {}
}

variable "capacity_reservation_azs" {
  type    = map(string)
  default = {}
}

variable "domain_name" {
  type    = string
  default = ""
}

variable "parent_domain" {
  type    = string
  default = ""
}

variable "baked_ami_id" {
  type        = string
  description = "Pre-baked GPU AMI ID (built in primary region, copied here)"
  default     = ""
}

variable "docker_image_uri" {
  type        = string
  description = "ECR image URI for the gpu-dev container"
}

variable "lambda_version" {
  type    = string
  default = "0.5.31"
}

variable "min_cli_version" {
  type    = string
  default = "0.5.31"
}

variable "max_reservation_hours" {
  type    = number
  default = 48
}

variable "grafana_admin_password" {
  type      = string
  sensitive = true
  default   = ""
}
