# Staging Baseline Pass B Runbook

## Scope And Gate

This runbook describes the bounded future live sequence for Issue #11. It does not authorize execution. Pass B requires an explicit approval for the exact temporary-capacity plan SHA-256 recorded in [Pass A evidence](../evidence/staging_baseline_pass_a.md), a six-hour maximum window, and the stated rollback handoff.

Only the approved one-node `e2-standard-4` capacity window, exact pinned controller inputs, the existing GitOps root, the minimal application, Argo Rollouts, and the constrained Prometheus profile are in scope. The `e2-medium` default remains the rollback boundary.

The six-hour maximum applies only to the temporary `e2-standard-4` capacity window. Pass B may create exactly one expected 2Gi Prometheus PVC and its backing storage from the reviewed volume-claim template. This storage is in scope and must be verified after reconciliation. Persistent-storage cost continues until separately approved cleanup, so record an auditable current storage-cost estimate and planned cleanup time before approval. The temporary-node estimate does not include storage cost.

## Ordered Deployment

1. Repeat the sanitized target, budget-boundary, IAM-boundary, workload, and public-exposure preflight. Stop if its outcome differs from Pass A.
2. Verify the local plan checksum and run the temporary-capacity plan guardrail. Stop unless it contains one in-place default-node-pool update from `e2-medium` to `e2-standard-4`, two runtime API no-ops, and no cluster replacement or wider Terraform change.
3. Apply the exact approved temporary-capacity plan. Verify the expected node-pool operation, node readiness, and live allocatable capacity before creating controllers or application workloads.
4. Install ingress-nginx `4.12.1`, then Argo CD `7.8.28`, using only the reviewed staging values from Issue #9. Verify their requests/limits, readiness, and ClusterIP-only Services after each operation.
5. Create the existing stage GitOps root. It reconciles the existing minimal application and the reviewed Argo Rollouts `2.32.0` and kube-prometheus-stack `65.5.1` child inputs.
6. Verify application, controller, ServiceMonitor, PrometheusRule, and Prometheus readiness. Confirm exactly one expected 2Gi Prometheus PVC and its backing storage, six-hour retention, 1GiB retention size, 30-second scrape/rule intervals, and 10-second scrape timeout. Validate the reviewed dashboard JSON/input; Grafana remains disabled, so live visual Grafana-dashboard validation is not part of Pass B and Grafana UI activation does not block the SLO/recovery validation path.
7. Verify that the application-facing ingress is served by the ClusterIP-only ingress controller and that no LoadBalancer Service, public endpoint, traffic generation, failure injection, PagerDuty, AI Operations, HolmesGPT, Scheduler, or remediation action exists.

## Stop Conditions

Stop the sequence immediately for a Terraform cluster replacement; an unexpected API, IAM, budget, foundation, network, or resource change; a second PVC; an unexpected storage class, size, or storage resource; a public principal or public endpoint; a LoadBalancer Service; a workload without the reviewed resource policy; a pending or evicted Pod; OOMKilled containers; node disk pressure; failed readiness; failed Prometheus scrape or rule evaluation; or a capacity-envelope overrun.

## Rollback And Cleanup Handoff

The temporary `e2-standard-4` capacity window is at most six hours. Persistent Prometheus storage remains billable until separately approved cleanup. Cleanup requires separate approval: remove the temporary controllers and workloads, remove the expected Prometheus PVC and backing storage, verify that no application or controller workload remains, create and review an exact rollback Terraform plan to restore one `e2-medium` node, apply that rollback plan only after its checksum is approved, and restore the `e2-medium`-only Terraform validation restriction in a separately reviewed repository change.

No budget, IAM, foundation state bucket, network, traffic, controlled failure, PagerDuty, AI Operations, HolmesGPT, Scheduler, or remediation change belongs to this runbook.
