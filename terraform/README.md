# Terraform — GKE Observability Platform

This Terraform module provisions a **production regional GKE cluster** with:
- Workload Identity
- Cluster autoscaling
- Dedicated node pools: default, observability, chaos
- Separate namespaces for observability and chaos workloads

 
## Architecture


```mermaid
flowchart TB
    subgraph GCP["Google Cloud Platform"]
        VPC["Custom VPC"]
        subgraph GKE["Regional GKE Cluster"]
            NP1["Default Node Pool </br> (apps / system)"]
            NP2["Observability Node Pool </br> (Prometheus, Grafana)"]
            NP3["Chaos Node Pool </br> (k6, chaos jobs)"]

            NS1["namespace: default"]
            NS2["namespace: observability"]
            NS3["namespace: chaos-load"]
        end
    end

    VPC --> GKE
    NP1 --> NS1
    NP2 --> NS2
    NP3 --> NS3
```



## Folder structure
```text
terraform_modular/
├── README.md
├── networking/         # VPC + subnetwork
├── gke/                # Cluster + Node Pools + variables + provider + outputs
└── monitoring/         # Kubernetes namespaces + Workload Identity prep
```
## Deploy

```bash
terraform init
terraform apply
```


