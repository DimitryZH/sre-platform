#  E-commerce Observability & SRE Platform on GKE

SLO-driven reliability and observability platform for microservices on GKE, focusing on load-induced behavior, burn rate–based alerting, autoscaling, and recovery validation.

This project demonstrates a real-world SRE approach to operating microservices on GKE using Service Level Objectives instead of raw metrics.


## Core Pillars
- GKE production infrastructure (Terraform)
- Prometheus-based SLO observability
- k6 load testing inside Kubernetes
- Error-budget driven alerting and scaling

## Focus areas
- SLO
- burn rate
- load-driven behavior
- autoscaling
- recovery
- GKE


## Prerequisites
The SRE platform treats container images as immutable artifacts and are consumed from an external CI build system.
Image tags are pinned to specific Git commit SHAs to guarantee reproducible deployments.
Container images used in this platform are produced by a dedicated CI build system.
See my project: [CI Build Platform](https://github.com/DimitryZH/ci-build-platform).


## High-level architecture diagram


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

## Security diagram
``` mermaid
sequenceDiagram
    participant Pod
    participant KSA as Kubernetes Service Account
    participant GKE
    participant GSA as Google Service Account
    participant GCP_API as GCP APIs

    Pod->>KSA: Uses KSA
    KSA->>GKE: Bound via Workload Identity
    GKE->>GSA: Token exchange
    GSA->>GCP_API: Authenticated request
    GCP_API-->>Pod: Response

    Note over Pod,GSA: No JSON keys\nNo Kubernetes secrets\nNo static credentials
```

## Helm boundary vs SLO Control Plane

``` mermaid
flowchart LR
    subgraph User
        Load[Test / Real Traffic]
    end

    subgraph Kubernetes Cluster
        subgraph Workload Layer
            App[Microservices App<br/>Online Shop app]
        end

        subgraph Helm Deployment Layer
            Helm[Helm Charts]
            Values[values.yaml<br/>Image / Resources / Ports]
        end

        subgraph SLO Control Plane
            SLO[SLO Definitions<br/>Latency, Error Rate]
            Metrics[Prometheus Metrics]
            Budget[Error Budget Calculator]
            Policy[Scaling & Policy Engine]
        end

        subgraph Scaling Layer
            HPA[K8s HPA / KEDA]
        end
    end

    Load --> App
    Helm --> App
    Values --> Helm

    App --> Metrics
    Metrics --> SLO
    SLO --> Budget
    Budget --> Policy
    Policy --> HPA
    HPA --> App
```    