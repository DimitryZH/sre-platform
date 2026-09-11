locals {
  approved_runtime_apis = toset([
    "compute.googleapis.com",
    "container.googleapis.com",
  ])
}

resource "google_project_service" "runtime" {
  for_each = local.approved_runtime_apis

  project                    = var.project_id
  service                    = each.value
  disable_on_destroy         = false
  disable_dependent_services = false
}

resource "google_container_cluster" "staging" {
  name     = "online-shop-staging"
  project  = var.project_id
  location = var.zone

  network    = var.network_name
  subnetwork = var.subnetwork_name

  # A default pool avoids transient extra pools. Capacity remains one fixed node.
  initial_node_count = 1

  node_config {
    machine_type = var.node_machine_type
    image_type   = "COS_CONTAINERD"
    labels       = var.runtime_labels

    metadata = {
      disable-legacy-endpoints = "true"
    }
  }

  resource_labels = var.runtime_labels

  release_channel {
    channel = "REGULAR"
  }

  logging_config {
    enable_components = []
  }

  monitoring_config {
    enable_components = []

    managed_prometheus {
      enabled = false
    }
  }

  enable_shielded_nodes = true
  deletion_protection   = false

  lifecycle {
    precondition {
      condition = (
        (var.node_machine_type == "e2-medium" && !var.temporary_capacity_window) ||
        (var.node_machine_type == "e2-standard-4" && var.temporary_capacity_window)
      )
      error_message = "e2-standard-4 is permitted only with the explicit temporary capacity-window gate; the baseline is e2-medium."
    }
  }

  depends_on = [google_project_service.runtime]
}
