# Staging Runtime Apply Preflight Evidence

## Scope

This evidence records the read-only preflight for Issue #5. It does not authorize an apply. A separately reviewed saved plan and an explicit approval for that plan's exact SHA-256 are required before any apply.

## Verified Foundation Boundary

The configured gcloud project matched the explicit approved staging target. The target project, existing `default` network, and existing `default` subnetwork in `us-central1` were reachable through explicit target-project read-only requests.

The foundation state bucket retained uniform bucket-level access, enforced public access prevention, versioning, bounded 30-day retention, and the 7-day soft-delete policy. The earlier sanitized project IAM read confirmed no public principals and no `roles/billing.admin` binding. No foundation, IAM, or budget changes were made during this preflight.

## Budget Verification Exception

The 100 CAD budget, scoped to the approved staging target, was manually verified in Google Cloud Console for this issue only. The terminal Budget API path remains unresolved: it returned authorization and not-found responses from the current CLI context. Do not use that API path as evidence for this Issue #5 runtime apply decision.

This exception does not authorize any budget change. Budget-management work must first restore and validate the terminal Budget API context through a separate approved task.

## Runtime Boundary

The planned runtime scope remains limited to the required Compute and GKE APIs, one zonal Standard GKE cluster named `online-shop-staging`, and one fixed `e2-medium` node. No workloads, GitOps, Prometheus deployment, traffic generation, controlled failures, AI Operations integration, Scheduler jobs, secrets, budget changes, IAM changes, foundation changes, or additional telemetry ingestion resources are in scope.

Live staging validation remains pending.

## Approved Runtime Apply

The separately approved saved plan with SHA-256 `05F730C1D2FCBEB559CF450D598A568C602E049A2F415E508FA65B55AB8B604F` was applied. The applied runtime state contains exactly the two approved runtime API resources and one GKE cluster resource. No state content, plan content, tfvars, credentials, identities, or budget details are recorded here.

Read-only verification confirmed that both runtime APIs are enabled, `online-shop-staging` is `RUNNING` in `us-central1-a`, and it has one `e2-medium` node pool. GKE logging and monitoring collection component lists are empty, and Managed Prometheus is not explicitly enabled. No workload, GitOps, traffic generator, controlled failure, AI Operations integration, Scheduler job, secret, budget change, IAM change, or foundation change was applied.

Runtime readiness is limited to control-plane and node-pool availability. Application and workload validation remain pending and require a separate approved step.
