provider "kubernetes" {
  host                   = google_container_cluster.gke.endpoint
  token                  = data.google_client_config.default.access_token
  cluster_ca_certificate = base64decode(
    google_container_cluster.gke.master_auth[0].cluster_ca_certificate
  )
}

data "google_client_config" "default" {}

resource "kubernetes_namespace" "observability" {
  metadata { name = "observability" }
}

resource "kubernetes_namespace" "chaos" {
  metadata { name = "chaos-load" }
}