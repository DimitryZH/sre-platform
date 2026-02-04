output "cluster_name" {
  value = google_container_cluster.gke.name
}

output "region" {
  value = var.region
}

output "node_pools" {
  value = keys(google_container_node_pool.pools)
}