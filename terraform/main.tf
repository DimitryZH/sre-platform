terraform {
  required_version = ">= 1.5"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

resource "google_compute_network" "vpc" {
  name                    = var.network_name
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.network_name}-subnet"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = var.subnet_cidr
}

resource "google_container_cluster" "gke" {
  name     = var.cluster_name
  location = var.region

  remove_default_node_pool = true
  initial_node_count       = 1

  network    = google_compute_network.vpc.id
  subnetwork = google_compute_subnetwork.subnet.id

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  ip_allocation_policy {}

  release_channel {
    channel = "REGULAR"
  }
}

locals {
  pools = {
    default = {
      machine = var.default_machine_type
      labels  = { role = "default" }
    }
    observability = {
      machine = var.observability_machine_type
      labels  = { role = "observability" }
    }
    chaos = {
      machine = var.chaos_machine_type
      labels  = { role = "chaos" }
    }
  }
}

resource "google_container_node_pool" "pools" {
  for_each = local.pools

  name     = each.key
  cluster  = google_container_cluster.gke.name
  location = var.region

  node_config {
    machine_type = each.value.machine
    labels       = each.value.labels
    oauth_scopes = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  autoscaling {
    min_node_count = 0
    max_node_count = 5
  }
}

