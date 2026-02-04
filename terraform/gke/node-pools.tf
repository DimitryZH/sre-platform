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