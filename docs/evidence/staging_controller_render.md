# Staging Controller Render Evidence

## Scope

This evidence covers Issue #9 repository inputs only. No Helm release, Kubernetes resource, cloud resource, Terraform state, budget, IAM policy, or network resource was changed.

The target Kubernetes server version was confirmed through a sanitized read-only check as `v1.35.7-gke.1150000`. No project identifier, endpoint, credential, or identity is recorded.

## Pinned Inputs And Compatibility

| Controller | Chart version | Application version | Source repository | Compatibility verification |
| --- | --- | --- | --- | --- |
| Argo CD | `7.8.28` | `v2.14.11` | `https://argoproj.github.io/argo-helm` | Chart metadata declares `kubeVersion: >=1.25.0-0`, which includes the verified target version. |
| ingress-nginx | `4.12.1` | `1.12.1` | `https://kubernetes.github.io/ingress-nginx` | Chart metadata declares `kubeVersion: >=1.21.0-0`, which includes the verified target version. |

The render script creates an isolated temporary Helm configuration and cache, adds only the two source repositories there, pulls the exact chart versions, then runs `helm lint` and `helm template --include-crds --kube-version 1.35.7` against the reviewed stage values. It does not use `helm repo update`, normal local Helm configuration, `latest`, or a floating version.

## Reviewed Values Decisions

| Area | Decision |
| --- | --- |
| Argo CD long-running components | Application controller, repo server, API server, and standalone Redis are enabled with requests and limits. |
| Argo CD optional components | Dex and notifications are disabled. ApplicationSet is rendered at zero replicas because this chart version has no boolean enable switch. No extension configuration or extension containers are enabled. |
| Argo CD exposure | API server ingress is disabled and all rendered Services are ClusterIP. |
| ingress-nginx scope | One replica watches only the staging application namespace. The IngressClass is not default. |
| ingress-nginx exposure | Controller external Service is disabled; the only rendered Service is ClusterIP. |
| ingress-nginx metrics | Enabled through a ClusterIP metrics Service and one ServiceMonitor with a 30-second interval. |
| ingress-nginx admission | Admission webhooks are disabled for this first vertical slice, so no admission Jobs are rendered. |
| ingress-nginx optional backend | Default backend is disabled. |

## Rendered Inventory

| Kind | Argo CD | ingress-nginx |
| --- | ---: | ---: |
| Deployments | 4, including one ApplicationSet deployment at zero replicas | 1 |
| StatefulSets | 1 | 0 |
| Jobs | 1 Redis secret-init hook | 0 |
| Services | 4 ClusterIP | 1 ClusterIP metrics Service |
| ServiceAccounts | 5 | 1 |
| Roles / RoleBindings | 5 / 5 | 1 / 1 |
| ClusterRoles / ClusterRoleBindings | 2 / 2 | 1 / 1 |
| CRDs | 3 | 0 |
| PVCs | 0 | 0 |
| Ingresses | 0 | 0 |
| LoadBalancer Services | 0 | 0 |
| ServiceMonitors | 0 | 1 |

The Argo CD cluster-scoped RBAC is expected for application reconciliation. ingress-nginx retains one reviewed ClusterRole and ClusterRoleBinding for controller discovery and IngressClass handling while its watch scope is limited to the staging application namespace. No additional controller, persistent storage, or public endpoint is rendered.

## Resource Envelope Comparison

| Component | Steady-state request | Limit |
| --- | --- | --- |
| Argo CD application controller | 200m CPU / 256Mi memory | 500m CPU / 512Mi memory |
| Argo CD repo server Pod | 100m CPU / 256Mi memory | 250m CPU / 384Mi memory |
| Argo CD API server | 75m CPU / 128Mi memory | 200m CPU / 256Mi memory |
| Argo CD Redis | 50m CPU / 64Mi memory | 150m CPU / 128Mi memory |
| ingress-nginx controller | 75m CPU / 128Mi memory | 250m CPU / 256Mi memory |
| Controller steady-state total | 500m CPU / 704Mi memory | 1350m CPU / 1408Mi memory |
| Redis secret-init Job, temporary | 50m CPU / 64Mi memory | 100m CPU / 128Mi memory |

The steady-state total exactly fits the Argo CD and ingress-nginx allocation in the Issue #7 capacity plan. The single temporary Job matches that plan's temporary-job allowance. ApplicationSet has zero replicas and therefore has no scheduled steady-state footprint.

## Readiness, Stop, Rollback, And Cleanup

Before a future install, repeat the sanitized budget and IAM preflight, verify the exact chart archives and values, and review the rendered inventory again. Stop if a render introduces a LoadBalancer Service, Ingress, PVC, unreviewed cluster-scoped RBAC, additional controller, missing resources, or an envelope overrun.

Any install, rollback, or cleanup requires separate explicit live-action approval. Cleanup must remove only the separately approved controller releases after dependent workloads have been removed. The temporary-capacity Terraform plan remains separate and approval-gated.

Neither controller is deployed or live-validated.
