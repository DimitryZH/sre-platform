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

locals {
  # Minimum APIs required for project bootstrap and GKE deployment.
  required_apis = toset([
    "serviceusage.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "monitoring.googleapis.com",
    "logging.googleapis.com",
  ])

  # Least-privilege baseline recommended by GKE for node service accounts.
  gke_node_sa_roles = toset([
    "roles/container.defaultNodeServiceAccount",
  ])
}

resource "google_project_service" "required" {
  for_each = local.required_apis

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "gke_nodes" {
  account_id   = "online-shop-gke-nodes"
  display_name = "online-shop GKE nodes"
  description  = "Node service account for the online-shop GKE cluster"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = local.gke_node_sa_roles

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

resource "google_compute_network" "vpc" {
  name                    = var.network_name
  auto_create_subnetworks = false

  depends_on = [google_project_service.required]
}

resource "google_compute_subnetwork" "subnet" {
  name          = "${var.network_name}-subnet"
  region        = var.region
  network       = google_compute_network.vpc.id
  ip_cidr_range = var.subnet_cidr

  depends_on = [google_project_service.required]
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

  depends_on = [google_project_service.required]
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
    service_account = google_service_account.gke_nodes.email
    machine_type    = each.value.machine
    labels          = each.value.labels
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
  }

  autoscaling {
    min_node_count = 0
    max_node_count = 5
  }

  depends_on = [
    google_project_service.required,
    google_project_iam_member.gke_nodes,
  ]
}

