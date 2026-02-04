variable "project_id" {}
variable "region" { default = "us-central1" }
variable "cluster_name" { default = "ecommerce-observability" }
variable "network_name" { default = "gke-observability-vpc" }
variable "subnet_cidr" { default = "10.10.0.0/16" }

variable "default_machine_type" { default = "e2-standard-4" }
variable "observability_machine_type" { default = "e2-standard-8" }
variable "chaos_machine_type" { default = "e2-standard-2" }