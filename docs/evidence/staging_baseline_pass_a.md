# Staging Baseline Pass A Evidence

## Scope

This evidence records the Issue #11 Pass A repository preparation, read-only preflight, and exact temporary-capacity plan. No cloud, Terraform state, Kubernetes, Helm release, IAM, budget, network, storage, or application resource was changed.

The staging target is intentionally unnamed. No project identifier, endpoint, identity, credential, secret value, or raw Terraform plan/state is recorded.

## Sanitized Read-Only Preflight

| Check | Result |
| --- | --- |
| Configured target matches the approved staging target | Yes |
| Active account available | Yes |
| Cluster shape | One running zonal Standard cluster with one `e2-medium` node pool |
| Ready nodes | 1 |
| Allocatable capacity | 940m CPU, 2866868Ki memory, 110 pods |
| Current non-system workloads | 0 Pods |
| Current PVCs, Ingresses, LoadBalancer Services | 0, 0, 0 |
| Foundation/runtime state address boundaries | Approved baseline present |
| Project-scoped budget alert | Verified through the Budget API |
| Public IAM principals / broad billing admin | Absent |

## Temporary Capacity Saved Plan

The local ignored plan is `terraform/runtime/staging-temporary-capacity.tfplan`.

| Property | Result |
| --- | --- |
| SHA-256 | `110A4C3DA6C0A107F59C2E5E6493CC4DDF9914CD22D25935DFDE7C1EE9C959E4` |
| Expected operation | One in-place `google_container_cluster.staging` update that changes the default node-pool machine type from `e2-medium` to `e2-standard-4` |
| Cluster replacement | Absent |
| Runtime APIs | Two `no-op` entries only |
| Foundation, IAM, budget, network, storage, or broader changes | Absent |

The tracked `terraform/runtime/temporary-capacity.tfvars.example` records the two required temporary inputs without storing a real tfvars artifact. Defaults preserve the `e2-medium` baseline. A separate rollback plan must return the node to `e2-medium` and restore the `e2-medium`-only validation restriction after approved cleanup.

## Deterministic Deployment Inputs

All Helm reads and renders used isolated temporary Helm configuration/cache and exact pinned versions. No repository refresh or dependency drift was accepted.

| Input | Rendered inventory | Exposure / storage |
| --- | --- | --- |
| Platform chart `0.1.0` | 10 Deployments, 1 Rollout, 13 Services, 11 ServiceMonitors, 2 PrometheusRules, 12 chart-generated Secrets, 12 ConfigMaps, 1 internal Ingress | No LoadBalancer or PVC; `break` and `loadgenerator` Deployments disabled |
| Argo Rollouts `2.32.0` | 1 Deployment, 5 CRDs, 4 ClusterRoles, 1 ClusterRoleBinding | No LoadBalancer, PVC, Ingress, or Job |
| kube-prometheus-stack `65.5.1` | 2 Deployments, 1 Prometheus, 10 CRDs, 35 PrometheusRules, 10 ServiceMonitors | No DaemonSet, LoadBalancer, Ingress, or Job; the Prometheus CR declares one expected 2Gi volume-claim template |
| Argo CD `7.8.28` | Reused Issue #9 isolated render evidence | ClusterIP-only controller exposure |
| Initial ingress-nginx `4.12.1` | 1 Deployment, 1 metrics Service, 1 ConfigMap, 1 ServiceAccount, 1 Role, 1 RoleBinding, 1 ClusterRole, 1 ClusterRoleBinding, 1 IngressClass; 0 ServiceMonitors | No Secret, PVC, Ingress, LoadBalancer Service, public endpoint, or additional controller |
| GitOps ingress metrics after monitoring is healthy | 1 ServiceMonitor selecting the pinned metrics Service on port `metrics` every 30 seconds | No Secret, PVC, Ingress, LoadBalancer Service, public endpoint, RBAC, or controller |

The application has explicit requests/limits for all 11 scheduled workloads. Its steady request total is 600m CPU and 1216Mi memory; the frontend canary peak adds 75m CPU and 128Mi memory. The controllers and constrained observability profile use 950m CPU and 1600Mi memory long-running platform requests, plus one 50m CPU / 64Mi temporary hook allowance. The repo-server allowance was raised after a bounded manifest-generation OOM restart; the temporary node capacity still exceeds the combined reviewed peak.

The static render contains no standalone PVC object. During Pass B, the approved Prometheus reconciliation may create exactly one expected 2Gi PVC and its backing storage from the reviewed volume-claim template. That storage is in the Pass B approval scope and must be verified after reconciliation. Any additional PVC, storage class, storage size, or storage resource is a stop condition.

Grafana is intentionally disabled in the constrained first slice. Pass B validates Prometheus, ServiceMonitors, PrometheusRules, and the reviewed dashboard JSON/input; it does not claim live visual Grafana-dashboard validation. Grafana UI activation is deferred and does not block the SLO/recovery validation path.

The app-of-apps dependency order is explicit: `monitoring-stage` wave `-2`, `argo-rollouts-stage` wave `-1`, then `ingress-nginx-metrics-stage` and `online-shop-stage` wave `1`. Monitoring must become healthy before either ServiceMonitor-bearing child is reconciled; Argo Rollouts must be healthy before the application creates its Rollout resources.

The direct ingress controller is scoped to `online-shop-stage`. That namespace must exist before the initial ingress-nginx release is installed, while it is still empty of application workloads; otherwise the controller exits before it becomes ready.

## Cost, Window, And Pass B Handoff

The six-hour maximum applies only to the temporary one-node `e2-standard-4` capacity window. Its incremental planning estimate is about USD 0.10 per hour, approximately USD 0.60, excluding regional pricing changes, persistent storage, and egress. Persistent-storage cost continues until separately approved cleanup. Record an auditable current persistent-storage cost estimate and planned cleanup time immediately before Pass B approval; the USD 0.60 estimate does not include storage.

Pass B may proceed only after explicit approval of the exact saved-plan SHA above and this six-hour window. Verify the node-pool operation without cluster replacement, then install the pinned controllers and GitOps root in the documented order. Stop on any public exposure, unexpected workload/RBAC/storage, readiness failure, resource-envelope overrun, failed discovery/rules/dashboard-input check, or budget/IAM regression.

Cleanup is a separately approved sequence: remove temporary controllers/workloads, verify no dependent workload remains, apply the exact rollback plan to `e2-medium`, and restore the baseline-only validation restriction. No traffic, canary execution, controlled failure, PagerDuty, AI Operations, HolmesGPT, Scheduler, or remediation is included.
