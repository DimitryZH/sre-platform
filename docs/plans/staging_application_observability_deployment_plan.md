# Staging Application And Observability Deployment Plan

## Decision

Select **Option B: temporary capacity increase**. The current node exposes 940m CPU and 2799Mi memory. After reserving 700m CPU and 768Mi memory for GKE system overhead, it cannot safely schedule the proposed application, ingress, delivery, and observability footprint. A temporary increase to one `e2-standard-4` node for a tightly bounded validation window is the smallest option that leaves meaningful scheduling and disruption headroom.

The temporary capacity window must be limited to six hours and returned to the existing node shape through a separately reviewed, exact Terraform plan. This is a planning decision, not an approved live change.

## Evidence And Alternatives

The complete stage render has 13 workloads and 21 replicas, but no workload currently defines requests or limits. It also enables the load generator and failure-injection deployment by default. See [the sanitized preflight evidence](../evidence/staging_application_observability_preflight.md).

**Option A: reduced staging footprint** was rejected as the primary option. The existing single node cannot safely host the minimum controller and observability footprint alongside a representative application, even after disabling load generation and failure injection.

**Option C: split deployment** remains a fallback. It reduces simultaneous resource demand but makes application and observability validation less representative, increases operational sequencing, and does not provide safe canary-surge headroom for the intended integrated check.

## Proposed Resource Policy

The following values are proposals for review. They are not active chart values.

| Workload | Steady replicas | Peak replicas | CPU request / limit | Memory request / limit |
| --- | ---: | ---: | --- | --- |
| frontend Rollout | 1 | 2 | 75m / 200m | 128Mi / 256Mi |
| cart | 1 | 1 | 50m / 150m | 128Mi / 256Mi |
| checkout | 1 | 1 | 75m / 250m | 128Mi / 256Mi |
| payment | 1 | 1 | 50m / 150m | 96Mi / 192Mi |
| ad | 1 | 1 | 50m / 150m | 128Mi / 256Mi |
| currency | 1 | 1 | 25m / 100m | 64Mi / 128Mi |
| email | 1 | 1 | 25m / 100m | 64Mi / 128Mi |
| productcatalog | 1 | 1 | 75m / 250m | 128Mi / 256Mi |
| recommendation | 1 | 1 | 75m / 250m | 128Mi / 256Mi |
| redis | 1 | 1 | 50m / 150m | 128Mi / 256Mi |
| shipping | 1 | 1 | 50m / 150m | 96Mi / 192Mi |
| loadgenerator | 0 | 0 | Disabled | Disabled |
| break failure injection | 0 | 0 | Disabled | Disabled |

The application steady-state total is 600m CPU and 1216Mi memory. The controlled frontend canary surge adds 75m CPU and 128Mi memory. The future Rollout policy must explicitly set `maxSurge: 1` and `maxUnavailable: 0`.

| Platform component | CPU request / limit | Memory request / limit |
| --- | --- | --- |
| ingress-nginx controller | 75m / 250m | 128Mi / 256Mi |
| Argo CD application controller | 200m / 500m | 256Mi / 512Mi |
| Argo CD repository server | 100m / 250m | 256Mi / 384Mi |
| Argo CD server | 75m / 200m | 128Mi / 256Mi |
| Argo CD Redis | 50m / 150m | 64Mi / 128Mi |
| Argo Rollouts controller | 75m / 200m | 128Mi / 256Mi |
| Prometheus | 300m / 750m | 512Mi / 1Gi |
| Prometheus Operator | 75m / 250m | 128Mi / 256Mi |
| Admission patch Job, temporary | 50m / 100m | 64Mi / 128Mi |

The long-running platform-component subtotal is 950m CPU and 1600Mi memory. The 50m CPU and 64Mi admission patch Job is temporary and excluded from steady state. Combining the application subtotal with the long-running platform subtotal gives a steady-state footprint of 1550m CPU and 2816Mi memory. The peak, including the frontend canary surge and one temporary admission Job, is 1675m CPU and 3008Mi memory. Adding the 700m CPU and 768Mi system reserve yields 2375m CPU and 3776Mi memory. Future scheduling verification must require at least 1200m CPU and 4Gi memory of additional headroom after those totals; the proposed temporary node shape is expected to meet this but must be verified from live allocatable capacity before controllers are installed.

## Observability Policy

Use one Prometheus instance with a 2Gi PVC, six-hour retention, `retentionSize: 1GiB`, 30-second scrape and rule intervals, and a 10-second scrape timeout. Select only the staging application and ingress namespaces. Grafana, Alertmanager, kube-state-metrics, and node exporter remain disabled for this step.

Stop the future deployment if the PVC exceeds 75 percent in its first hour, Prometheus restarts, scrapes or rules fail, the node reports disk pressure, or an unexpected LoadBalancer is created. No monitoring ingestion resource is proposed in this work package.

## Exact Inputs And Version Gate

| Input | Reviewed value | Status |
| --- | --- | --- |
| Repository revision | `fc7197b` | Pinned |
| Platform chart | `0.1.0` | Pinned |
| Platform dependency lock digest | `b8386e371de8c6bf29ee67a937f0036365668dd76dbc95bfc34eaaaed3cff25b` | Must remain unchanged |
| Argo Rollouts chart | `2.32.0` | Existing pinned reference |
| kube-prometheus-stack chart | `65.5.1` | Existing pinned reference |
| Argo CD chart | `7.8.28` / `v2.14.11` | Pinned staging input; see controller render evidence |
| ingress-nginx chart | `4.12.1` / `1.12.1` | Pinned staging input; see controller render evidence |

Argo CD and ingress-nginx now have pinned staging inputs and reviewed values. Their deterministic local render is recorded in [the controller render evidence](../evidence/staging_controller_render.md). Do not refresh Helm repositories or accept a changed dependency lock; all remaining preflight, capacity, exact-plan, and live-action approval gates remain in force.

## Terraform Capacity And Node-Recreation Gates

The current runtime Terraform variable validation permits only `e2-medium`. A future, separately reviewed capacity PR must temporarily permit the selected `e2-standard-4` machine type. Its separately reviewed rollback PR and exact rollback plan must restore the original `e2-medium`-only restriction after the temporary validation window.

A GKE node machine-type update recreates nodes. The exact future Terraform plan must show the expected node-pool operation explicitly and demonstrate that the GKE cluster itself is not replaced. The temporary capacity change may occur only while the cluster has no application or controller workloads. Return to the baseline node shape may occur only after separately approved cleanup of the temporary controllers and workloads. A plan showing cluster replacement, unexpected resource changes, or any wider scope is a stop condition.

## Proposed Deployment Order

1. Repeat sanitized preflight. Require a fresh budget boundary check and a successful sanitized IAM public-principal check.
2. Re-render the pinned Argo CD and ingress-nginx inputs with their reviewed values. Stop on any dependency or version drift.
3. Create, review, and separately approve an exact Terraform plan for the temporary node capacity change, including the temporary machine-type validation change, an explicit node-pool operation, no cluster replacement, and a six-hour return plan.
4. Apply that capacity plan only after exact-plan approval, then verify allocatable capacity and node readiness.
5. Install ingress-nginx, Argo CD, and Argo Rollouts in separately reviewed actions; verify resource requests, readiness, and absence of unexpected public endpoints after each action.
6. Install the constrained kube-prometheus-stack profile and verify PVC, retention, scrape scope, and rule health.
7. Deploy the pinned application revision with load generation and failure injection disabled. Enable the frontend canary only after base readiness passes.
8. Treat traffic, k6, controlled failures, AI Operations integration, and all live investigation as separate approval-gated work.

## Stop Conditions, Rollback, And Cleanup

Stop before progressing on any Pending workload, OOMKilled container, eviction, disk pressure, node not Ready, unexpected LoadBalancer or Ingress, failed readiness check, failed analysis, failed scrape or rule, or missing budget/IAM precondition.

Rollback means halting the rollout, restoring the last known good application revision, and removing only the separately approved application/controller resources. Cleanup must remove temporary capacity through its exact reviewed Terraform rollback plan, restore the `e2-medium`-only validation restriction, and verify that the node shape is restored. No budget, IAM, foundation-state-bucket, or network change belongs in this plan.

## Cost Expectations And Approval Gate

The existing cluster control-plane fee and the current node cost continue while the cluster exists. The temporary node increase has an incremental planning estimate of about USD 0.10 per hour, capped at roughly USD 0.60 for six hours, excluding any regional price change, storage, egress, or observability consumption. Reconfirm the current Cloud Billing Calculator estimate before approval.

No live or cost-changing action is authorized by this document. A future action requires a fresh sanitized preflight, exact rendered manifests and merged values, an exact saved Terraform plan and SHA-256 where Terraform is involved, explicit approval for that exact plan, and an approved rollback window.
