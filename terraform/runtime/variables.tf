variable "project_id" {
  description = "Existing staging project that hosts the runtime layer."
  type        = string
  default     = "sre-platform-staging-507220"
}

variable "region" {
  description = "Region containing the approved zonal runtime cluster."
  type        = string
  default     = "us-central1"
}

variable "zone" {
  description = "Single zone for the minimal Standard GKE cluster."
  type        = string
  default     = "us-central1-a"
}

variable "network_name" {
  description = "Existing network name. This configuration never creates a network."
  type        = string
  default     = "default"
}

variable "subnetwork_name" {
  description = "Existing subnetwork name. This configuration never creates a subnetwork."
  type        = string
  default     = "default"
}

variable "node_machine_type" {
  description = "Machine type for the single staging node; e2-standard-4 is temporary-window only."
  type        = string
  default     = "e2-medium"

  validation {
    condition     = contains(["e2-medium", "e2-standard-4"], var.node_machine_type)
    error_message = "The runtime permits only e2-medium or the separately approved temporary e2-standard-4 type."
  }
}

variable "temporary_capacity_window" {
  description = "Explicit gate for the six-hour e2-standard-4 validation window."
  type        = bool
  default     = false
}

variable "runtime_labels" {
  description = "Labels applied to the staging runtime cluster and its node."
  type        = map(string)
  default = {
    "cost-profile" = "demo"
    environment    = "staging"
    platform       = "sre-platform"
    scope          = "runtime"
  }
}
