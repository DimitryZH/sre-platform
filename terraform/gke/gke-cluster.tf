resource "google_container_cluster" "gke" {
  name     = var.cluster_name
  location = var.region

  remove_default_node_pool = true
  initial_node_count       = 1

  network    = "${var.network_name}"
  subnetwork = "${var.network_name}-subnet"

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  ip_allocation_policy {}

  release_channel {
    channel = "REGULAR"
  }
}