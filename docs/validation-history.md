# SRE Platform Validation History

**Document type:** Public validation record

**Last reviewed:** 2026-09-09

## Purpose

This document preserves the verified engineering history of the SRE Platform
without confusing historical validation with the state of the currently
deployed environment.

The project has two distinct validation periods:

1. **April-May 2026:** the platform reached an advanced, evidence-backed dev
   validation state in a previous Google Cloud environment.
2. **September 2026:** the platform began a controlled reconstruction in the
   current Google Cloud project, starting with a new foundation and a minimal
   GKE staging runtime.

Earlier results remain valid as historical evidence of implemented behavior.
They do not prove that the same components are currently deployed or healthy in
the reconstructed environment. Current reconstruction work must revalidate the
required behavior before making a current-runtime claim.

## Status Vocabulary

This document uses the following terms consistently:

| Status | Meaning |
| --- | --- |
| `Historically validated` | The capability was exercised successfully in a previous live environment and has retained evidence. |
| `Currently deployed` | The resource or capability exists in the current Google Cloud environment and has current read-only verification. |
| `Revalidation required` | Historical evidence exists, but the capability has not yet been exercised in the current reconstructed environment. |
| `Partially validated` | Some required behavior was demonstrated, but at least one explicit completion condition remains open. |
| `Not yet validated` | No accepted live evidence currently proves the capability. |

These states are intentionally different. In particular:

> Historically validated does not mean currently deployed, and revalidation
> does not erase the earlier achievement.

## Validation Period 1 — April-May 2026

### Environment Context

The first major implementation period used a previous Google Cloud account and
live GKE environment. The environment is not the current deployment target, but
the repository retains configuration, reports, screenshots, CLI excerpts, and
load-test evidence from that work.

The validated scope was the `online-shop-dev` environment. Stage and production
convergence were not completed during this period.

### Infrastructure and GitOps Foundation

**Historical status:** `Historically validated`

The recorded dev deployment flow demonstrated:

- Terraform-based bootstrap of a fresh Google Cloud project and GKE cluster.
- Required API enablement.
- A dedicated GKE node service account and IAM baseline.
- VPC and subnet provisioning.
- Argo CD installation and root application bootstrap.
- A canonical dev app-of-apps path.
- Successful convergence of the platform, shared monitoring, and application
  resources.

The accepted dev application chain was:

```text
online-shop-platform
-> bootstrap-dev
-> monitoring-shared-dev
-> online-shop-dev
```

The resulting applications were recorded as `Synced` and `Healthy`.

Primary reference:

- [Dev deployment guide](deployment_guide.md)

### Dev Application Runtime

**Historical status:** `Historically validated`

The `online-shop-dev` application was deployed and served traffic through
ingress-nginx using a LoadBalancer IP without requiring DNS or TLS.

The implementation work resolved several real deployment blockers, including:

- Helm values and nil-pointer rendering failures.
- Service environment and runtime port inconsistencies.
- Probe behavior across mixed workloads.
- Monitoring-stack convergence issues.
- Ingress metrics discovery.
- IP-only request-label alignment.
- Controlled `/break` routing for failure injection.

The absence of the previous environment today does not invalidate these
historical implementation and troubleshooting results.

### Observability and SLO Validation

**Historical status:** `Historically validated`, with one explicitly partial
slow-alert lifecycle

The dev environment validated the complete short-window signal path:

```text
real traffic
-> ingress request metrics
-> Prometheus recording rules
-> SLO error ratio and burn rate
-> alert state
-> Grafana visualization
```

Validated signals included:

- `nginx_ingress_controller_requests`.
- `slo:error_ratio_5m`.
- `slo:burn_rate_5m`.
- Longer 30-minute, 1-hour, and 6-hour burn-rate behavior.
- Fast-burn alert transitions from pending to firing and recovery.

Recorded healthy baseline:

- request traffic near `0.95 rps`;
- `slo:error_ratio_5m = 0`;
- `slo:burn_rate_5m = 0`;
- no active SLO alert.

Recorded controlled error reaction:

- HTTP 500 responses generated through `/break`;
- `slo:error_ratio_5m` increased to approximately `0.45` in the short
  observability validation;
- `slo:burn_rate_5m` increased above `450`;
- the fast-burn alert progressed through its expected lifecycle.

Recovery showed short-window decay while longer windows retained incident
history. This behavior demonstrated realistic sliding-window SLO semantics
rather than an immediate reset after traffic recovery.

Primary references:

- [Dev SLO validation](slo_validation_dev_environment.md)
- [SLO verification summary](slo_verification_summary.md)
- [Grafana validation screenshots](observability/screenshots/)

### Slow-Burn Alert Boundary

**Historical status:** `Partially validated`

The sustained validation proved multi-window accumulation and observed the
slow-burn alert firing. It did not complete one clean-slate
`inactive -> pending -> firing -> resolved` sequence within the observed
window.

The remaining proof was blocked because the 6-hour burn rate still retained
historical error impact. A later clean-slate attempt correctly stopped before
execution when its start gate observed:

```text
slo:burn_rate_6h = 399.6126533247256
```

The incomplete clean-slate slow-alert lifecycle must remain visible as a known
historical limitation. It must not be generalized into a claim that every
multi-window alert lifecycle was fully validated.

Primary reference:

- [SLO verification summary](slo_verification_summary.md)

### SLO-Gated Progressive Delivery

**Historical status:** `Historically validated`

The frontend release path used Argo Rollouts with Prometheus-backed
AnalysisRuns at 10% and 50% canary gates.

Four important outcomes were validated.

#### Healthy promotion

- The rollout reached the 10% canary gate.
- The first `frontend-slo-check` AnalysisRun completed successfully.
- The rollout reached the 50% canary gate.
- The second AnalysisRun completed successfully.
- The release completed in a healthy state with stable traffic at 100% and
  canary traffic at 0%.

#### Failure at the 10% gate

- Controlled degradation was introduced through `/break`.
- The first AnalysisRun failed.
- Exact recorded values included:
  - `slo:error_ratio_5m = 0.7022254079816378`;
  - `slo:burn_rate_5m = 702.2254079816378`.
- The rollout aborted before reaching 50%.
- Stable traffic returned to 100%.

#### Failure at the 50% gate

- The first gate passed successfully.
- Controlled degradation was introduced after the rollout reached 50%.
- The second AnalysisRun failed.
- Exact recorded values included:
  - `slo:error_ratio_5m = 0.23618560428363544`;
  - `slo:burn_rate_5m = 236.18560428363543`.
- The rollout aborted before full promotion.

#### Recovery

- The rollout recovered from `Degraded` to `Healthy`.
- The abort flag cleared.
- Traffic returned to stable 100% and canary 0%.
- The stable ingress path continued returning HTTP 200.
- Failed AnalysisRuns remained available as historical decision evidence.

Primary references:

- [SLO-gated rollout case study](case-study/slo_rollout_demo.md)
- [SLO-gated rollout evidence](evidence/slo_gated_rollout_evidence_dev.md)
- [SLO-gated rollout CLI excerpts](evidence/slo_gated_rollout_cli_excerpts_dev.md)

### Load and Failure Testing

**Historical status:** `Historically validated` at the consolidated scenario
level, with run-level documentation inconsistencies noted below

Deterministic k6 and Kubernetes Job scenarios were used to establish traffic,
inject controlled failure, and support rollout recovery:

- healthy baseline;
- failure at the 10% gate;
- failure at the 50% gate;
- recovery after a 10% abort;
- recovery after a 50% abort.

The evidence records both successful and blocked attempts. For example, the
first failure-50 attempt was preserved as diagnostic evidence because baseline
traffic produced unexpected 5xx responses and the rollout aborted at 10%
before the intended 50% injection point.

Recovery after the 10% failure completed successfully at revision 16. Recovery
after the 50% failure completed successfully at revision 22 after temporary
low-load baseline traffic was used to avoid intermittent 5xx responses caused
by the default 20-VU baseline.

Primary references:

- [k6 operator guide](../k6/README.md)
- [Load-run evidence directory](evidence/load-runs/)
- [SLO-gated rollout case study](case-study/slo_rollout_demo.md)

### April-May Completion Boundary

The historical evidence proves an advanced and functioning dev platform. It
does not prove that the following were completed in that environment:

- Stage convergence.
- Production convergence.
- Production-grade DNS and TLS.
- PagerDuty or another finalized on-call integration.
- A complete clean-slate slow-burn alert lifecycle.
- AI Operations Platform integration.
- HolmesGPT investigation.
- Autonomous remediation.

## Validation Period 2 — September 2026

### Reconstruction Context

The project returned to active deployment work in September using a new Google
Cloud project. This is a reconstruction and revalidation period, not the first
time the platform capabilities were implemented.

The current target is:

- Google Cloud project: approved staging target;
- cluster: `online-shop-staging`;
- location model: Standard zonal GKE;
- current node baseline: one fixed `e2-medium` node.

### Current Foundation

**Current status:** `Currently deployed`

The current project has an applied, evidence-backed foundation that includes:

- project and billing boundary;
- CAD budget alert baseline;
- protected remote Terraform state;
- labels and bounded IAM;
- required foundation services;
- VPC and subnet assumptions used by the runtime.

References:

- [Foundation PR #2](https://github.com/DimitryZH/sre-platform/pull/2)
- [Runtime plan PR #4](https://github.com/DimitryZH/sre-platform/pull/4)

### Current Minimal GKE Runtime

**Current status:** `Currently deployed`

The exact approved Terraform plan created only:

- Compute Engine API enablement;
- GKE API enablement;
- the zonal Standard GKE cluster `online-shop-staging`;
- one fixed `e2-medium` node.

Post-apply verification confirmed:

- the cluster was running in the approved zone;
- the runtime state contained only the three approved Terraform addresses;
- GKE logging and monitoring collection component lists were empty;
- Managed Prometheus was disabled;
- no application, GitOps controller, observability stack, or integration was
  deployed by the runtime apply.

Reference:

- [Applied staging runtime PR #6](https://github.com/DimitryZH/sre-platform/pull/6)

### Current Revalidation Boundary

**Current status:** `Revalidation required`

The repository still contains the implementation and historical evidence for
GitOps, Online Boutique, observability, SLO evaluation, progressive delivery,
failure injection, and recovery. Those capabilities are not yet live in the
new staging cluster.

The current environment must separately demonstrate:

- capacity fit and explicit resource requests and limits;
- Argo CD and Argo Rollouts installation;
- Online Boutique staging convergence;
- ingress and controlled traffic;
- Prometheus, Alertmanager, Grafana, ServiceMonitor, and PrometheusRule
  operation;
- SLO and burn-rate behavior;
- controlled failure, rollout decision, and recovery;
- PagerDuty delivery and incident lifecycle;
- a bounded read-only evidence interface;
- AI Operations Platform and HolmesGPT integration.

Revalidation should reuse the repository's proven design and lessons without
claiming that a source manifest is equivalent to a currently running service.

## Capability History Matrix

| Capability | April-May 2026 | September 2026 current state | Primary evidence |
| --- | --- | --- | --- |
| Fresh-project cloud bootstrap | `Historically validated` | `Currently deployed` with a newly bounded foundation | Deployment guide; PRs #2 and #4 |
| GKE cluster | `Historically validated` | `Currently deployed`: one zonal `e2-medium` node | Deployment guide; PR #6 |
| Argo CD bootstrap | `Historically validated` | `Revalidation required` | Deployment guide |
| Argo Rollouts controller | `Historically validated` | `Revalidation required` | Rollout case study and evidence |
| `online-shop-dev` runtime | `Historically validated` | Previous dev environment is not the current runtime | Deployment guide and screenshots |
| Current `online-shop-stage` runtime | `Not yet validated` | `Not yet validated` | Stage manifests exist; no current live evidence |
| Ingress traffic path | `Historically validated` in dev | `Revalidation required` | Deployment guide and SLO validation |
| Prometheus target discovery | `Historically validated` in dev | `Revalidation required` | SLO validation and verification summary |
| SLO error ratio and burn rate | `Historically validated` in dev | `Revalidation required` | SLO validation and rollout evidence |
| Fast-burn alert lifecycle | `Historically validated` in dev | `Revalidation required` | SLO validation and verification summary |
| Clean-slate slow-burn lifecycle | `Partially validated` | `Not yet validated` | SLO verification summary |
| Healthy canary 10% -> 50% -> 100% | `Historically validated` | `Revalidation required` | Rollout case study and CLI excerpts |
| Abort at 10% SLO gate | `Historically validated` | `Revalidation required` | Rollout evidence and CLI excerpts |
| Abort at 50% SLO gate | `Historically validated` | `Revalidation required` | Rollout evidence and CLI excerpts |
| Post-abort recovery | `Historically validated` | `Revalidation required` | Rollout evidence and load-run recovery summaries |
| PagerDuty incident response | `Not yet validated` | `Not yet validated` | Future milestone |
| AI Operations investigation | `Not yet validated` | `Not yet validated` | Future milestone |
| HolmesGPT investigation | `Not yet validated` | `Not yet validated` | Future milestone |
| HolmesGPT Operator Mode | `Not yet validated` | `Deferred experiment` | Future post-MVP experiment |
| Production environment | `Not yet validated` | `Not yet validated` | Future milestone |

## Evidence Index

### Dev observability and SLO

- [Dev SLO validation](slo_validation_dev_environment.md)
- [SLO verification summary](slo_verification_summary.md)
- [Grafana screenshots](observability/screenshots/)

### Progressive delivery

- [SLO-gated rollout case study](case-study/slo_rollout_demo.md)
- [SLO-gated rollout evidence](evidence/slo_gated_rollout_evidence_dev.md)
- [SLO-gated rollout CLI excerpts](evidence/slo_gated_rollout_cli_excerpts_dev.md)

### Load, failure, and recovery

- [k6 operator guide](../k6/README.md)
- [Load-run evidence](evidence/load-runs/)

### Current Google Cloud reconstruction

- [Foundation PR #2](https://github.com/DimitryZH/sre-platform/pull/2)
- [Runtime plan PR #4](https://github.com/DimitryZH/sre-platform/pull/4)
- [Applied runtime PR #6](https://github.com/DimitryZH/sre-platform/pull/6)

## Documentation Reconciliation Notes

The repository contains documents written at different points in the project.
Their wording reflects the state known when each document was created.

The following differences should be interpreted explicitly:

1. The deployment guide describes the successful dev bootstrap and convergence
   flow, but its final limitations section predates the later completed
   SLO-gated rollout validation. The later rollout evidence is authoritative
   for canary promotion, abort, and recovery claims.
2. Several individual load-run `summary.md` files still contain
   `pass_fail: PENDING`, while later consolidated evidence records the accepted
   scenario outcomes. Pending run summaries should not independently be cited
   as completed evidence until reconciled.
3. The first failure-50 run is correctly retained as `BLOCKED` diagnostic
   evidence and must not be presented as a successful 50% gate test.
4. `load-to-slo-timeline.md` currently contains only a title and does not add
   validation evidence.
5. The historical working roadmap accurately records the implementation plan
   at that time, but it is not the authoritative description of the current
   Google Cloud runtime.

These differences are documentation history, not a reason to discard accepted
evidence. Where documents disagree, the most specific later evidence should be
used, with its environment and time period stated.

## Relationship to Other Project Documents

- `README.md` explains the platform purpose and demonstrated capabilities.
- `docs/architecture.md` explains the target architecture and control loops.
- `docs/roadmap.md` describes stable future milestones without duplicating
  transient runtime state.
- A private gitignored work plan may track current commands, approvals,
  blockers, costs, and cleanup actions.
- This document remains the public source for what was validated, where it was
  validated, and what requires current revalidation.

## Maintenance Rules

Update this history only when a milestone produces reviewable evidence.

For every new entry:

1. Identify the environment and validation period.
2. Distinguish repository implementation from live execution.
3. Link the exact report, evidence package, pull request, or immutable revision.
4. Record partial, failed, and blocked outcomes alongside successful outcomes.
5. Do not rewrite historical results merely because the original environment
   was removed.
6. Do not promote a historical result to `Currently deployed` without current
   read-only verification.
7. Do not claim production validation from dev or staging evidence.
8. Keep credentials, private endpoints, raw secrets, and unsafe runtime data
   out of the public record.
