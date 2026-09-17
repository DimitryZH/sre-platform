# SLO-Driven Progressive Delivery Platform

## Overview

This repository implements a GitOps-based SRE platform that governs application releases using **Service Level Objectives (SLOs)** and error budgets instead of raw infrastructure metrics.

The platform is a production-oriented observability and release-governance example. Its completed live validation is limited to the documented staging scope; it is not a production-readiness claim.

End-to-end lifecycle:

> Build → Deploy → Observe → Evaluate SLO → Decide → Promote or Rollback

## Why SLO-driven delivery

Traditional deployments answer questions like **"Is CPU high?"** or **"Is memory above a threshold?"**.

This platform instead focuses on:

- **"Are users experiencing degraded service quality?"**
- **"How fast are we burning the error budget?"**

Release decisions are based on:

- Latency SLO
- Availability SLO
- Multi-window burn rate
- Remaining error budget

This produces delivery behavior that is aligned with user experience, not just infrastructure noise.

## April-May 2026 Dev Validation (Quick View)

![SLO Spike](docs/observability/screenshots/02_short_spike_2026-04-17_1153-1154.png)

This historical dev validation demonstrates **SLO-driven observability using real traffic and controlled error injection**.

### What is validated

- Healthy baseline (no errors, zero burn)
- Error injection (`/break` → HTTP 500)
- SLO impact (`error_ratio` and burn-rate spike)
- Multi-window behavior (fast vs long burn windows)
- Fast alert lifecycle (pending → firing → recovery)
- Clean-slate gating due to long-window burn persistence

---

### Explore

- Full visual evidence:  
  [`docs/observability/screenshots/`](docs/observability/screenshots/)

- Grafana dashboard:  
  [`observability/grafana/global-slo-dashboard.json`](observability/grafana/global-slo-dashboard.json)

- Full validation report:  
  [`docs/slo_validation_dev_environment.md`](docs/slo_validation_dev_environment.md)

## SLO-Gated Progressive Delivery (Validated)

![SLO-Gated Rollout Failure Decision (50% gate)](docs/assets/SC-05.png)

Validated outcomes in `online-shop-dev`:
- SLO-driven rollout decisions at both canary gates (10% and 50%)
- Automatic abort when burn-rate/error-ratio breach thresholds
- Multi-stage healthy-path promotion to 100% when signals stay healthy
- Operational recovery from `Degraded` back to `Healthy` after abort

Artifacts:
- Case Study: [`docs/case-study/slo_rollout_demo.md`](docs/case-study/slo_rollout_demo.md)
- Evidence: [`docs/evidence/slo_gated_rollout_evidence_dev.md`](docs/evidence/slo_gated_rollout_evidence_dev.md)
- CLI Evidence: [`docs/evidence/slo_gated_rollout_cli_excerpts_dev.md`](docs/evidence/slo_gated_rollout_cli_excerpts_dev.md)

## September 2026 Staging SRE Validation (Quick View)

The completed staging milestone validated the GitOps baseline, SLO signal path,
canary abort and recovery behavior, and one narrow human PagerDuty incident
lifecycle. The final verified baseline had five Argo CD Applications
`Synced/Healthy`, no temporary test resources or AnalysisRuns, one expected
Prometheus PVC, and no LoadBalancer or public exposure.

| Triggered | Acknowledged | Resolved |
| --- | --- | --- |
| <a href="docs/assets/staging-incident-response/pagerduty-staging-01-triggered.png"><img src="docs/assets/staging-incident-response/pagerduty-staging-01-triggered.png" alt="Staging PagerDuty incident triggered" width="100%"></a> | <a href="docs/assets/staging-incident-response/pagerduty-staging-02-acknowledged.png"><img src="docs/assets/staging-incident-response/pagerduty-staging-02-acknowledged.png" alt="Staging PagerDuty incident acknowledged" width="100%"></a> | <a href="docs/assets/staging-incident-response/pagerduty-staging-03-resolved.png"><img src="docs/assets/staging-incident-response/pagerduty-staging-03-resolved.png" alt="Staging PagerDuty incident resolved" width="100%"></a> |

The sequence is staging-only evidence of a bounded alert path, not production
validation. It does not validate AI investigation, Scheduler operation, or
automated remediation. See the [September 2026 consolidated staging
evidence](docs/evidence/staging_delivery_incident_response_validation_september_2026.md).


## Load and Failure Testing with k6

k6 is used to run deterministic, repeatable traffic scenarios that directly validate SLO-driven rollout decisions in `online-shop-dev`.

Validated usage:
- baseline traffic maintains a healthy denominator for SLO evaluation
- failure-10 and failure-50 scenarios simulate controlled error conditions
- each scenario maps directly to a rollout promotion gate (10% and 50%)
- SLO breaches trigger automated abort decisions during canary rollout
- run evidence is captured under [`docs/evidence/load-runs/`](docs/evidence/load-runs/)

Scenarios are deterministic and aligned with rollout analysis windows to ensure reproducible SLO evaluation.

Scenario mapping:
- `baseline` → healthy steady-state traffic (no SLO impact)
- `failure-10` → early-stage SLO breach at 10% canary gate
- `failure-50` → mid-rollout SLO breach at 50% canary gate

Operator guide:
- [`k6/README.md`](k6/README.md)
- [`docs/case-study/slo_rollout_demo.md`](docs/case-study/slo_rollout_demo.md)
- [`docs/evidence/slo_gated_rollout_evidence_dev.md`](docs/evidence/slo_gated_rollout_evidence_dev.md)



```mermaid
flowchart TB
    K6[k6 Load / Failure Jobs] --> Ingress[Ingress]
    Ingress --> App[online-shop frontend]
    App --> Metrics[Ingress / App Metrics]
    Metrics --> Prometheus[Prometheus SLO Rules]
    Prometheus --> Analysis[Argo Rollouts AnalysisRun]
    Analysis --> Decision{SLO Gate Decision}
    Decision -->|Healthy| Promote[Promote Canary]
    Decision -->|SLO Breach| Abort[Abort Rollout]
```



## Core capabilities

### GitOps deployment

- ArgoCD-style GitOps workflow for Kubernetes state
- Helm-based reusable charts
- Clear separation of configuration and runtime state

### Progressive delivery

- Canary-style rollout strategy
- Gradual traffic shifting
- Automatic rollback when SLOs regress

### SLO-driven observability

- kube-prometheus-stack for metrics, alerting, and dashboards
- Prometheus recording rules for SLI/SLO computation
- Grafana dashboards focused on SLOs and error budgets

Key SLIs:

- Latency SLI
- Error-rate SLI
- Error-budget tracking over time

### Multi-window burn rate (Google SRE model)

Release health is evaluated using multiple time windows, for example:

- **Short window** – fast detection of sharp regressions
- **Long window** – noise protection and resilience to small spikes

This avoids noisy rollbacks while reacting quickly to real incidents.

### Policy-as-Code governance

Release decisions can be codified as policies, for example using OPA/Rego, to:

- Gate promotions when SLO risk is detected
- Block merges when error-budget burn is unsafe
- Keep release behavior auditable and reviewable as code

### Explainable delivery

Each deployment is intended to surface:

- Current SLO state at release time
- Burn-rate evaluation
- Clear decision: **promote** or **rollback**
- Human-readable explanation for the outcome

### Observability dashboards

Dashboards emphasize:

- Error budget remaining
- Live burn rate
- Canary health
- Release decision flag (GREEN / RED)
- Rollout progress

## Conceptual deployment flow

1. Developer opens a PR
2. CI builds and pushes container images
3. GitOps layer syncs desired state to the cluster
4. Canary rollout begins
5. Prometheus evaluates SLOs and burn rate
6. Policy engine evaluates release risk
7. The system either **promotes** or **rolls back** the release

## Demonstration scenario

The platform is designed for deterministic, repeatable failure tests. A typical scenario:

1. Deploy a healthy version of the service
2. Start synthetic load using k6
3. Inject latency and/or errors
4. Observe burn-rate spikes and error-budget consumption
5. Watch the rollout automatically abort
6. See merge or promotion blocked when risk is too high

## Documentation

- [Architecture](docs/architecture.md) - target design, control loops, and system boundaries.
- [Validation history](docs/validation-history.md) - public record of historical evidence, current reconstruction state, and revalidation requirements.
- [Roadmap](docs/roadmap.md) - public milestones, ownership boundaries, and production-entry criteria.
- [Deployment guide](docs/deployment_guide.md) - historical dev bootstrap and convergence procedure.
- [SLO validation](docs/slo_validation_dev_environment.md) - detailed dev observability and SLO validation evidence.
- [SLO verification summary](docs/slo_verification_summary.md) - accepted results and known validation limits.
- [SLO-gated rollout case study](docs/case-study/slo_rollout_demo.md) - healthy promotion, abort, and recovery narrative.
- [Evidence](docs/evidence/) - CLI excerpts, Terraform preflight records, and preserved load-run artifacts.

## Repository layout

- [`charts/`](charts/)
  - [`charts/platform/Chart.yaml`](charts/platform/Chart.yaml)
  - [`charts/platform/values.yaml`](charts/platform/values.yaml)
  - [`charts/platform/templates/frontend-rollout.yaml`](charts/platform/templates/frontend-rollout.yaml)

- [`argocd/`](argocd/)
  - [`argocd/apps/root.yaml`](argocd/apps/root.yaml)

- Progressive delivery manifests are packaged in Helm under [`charts/platform/templates/`](charts/platform/templates/frontend-rollout.yaml).

- [`observability/grafana/`](observability/grafana/)
  - [`observability/grafana/global-slo-dashboard.json`](observability/grafana/global-slo-dashboard.json)

- [`docs/`](docs/)
  - Public architecture, validation, roadmap, deployment, case-study, and evidence documentation

- [`validation/stage/`](validation/stage/)
  - Operator-run baseline, failure-traffic, and Prometheus precheck fixtures for staging validation

- [`k6/`](k6/)
  - [`k6/README.md`](k6/README.md)
  - [`k6/scripts/baseline.js`](k6/scripts/baseline.js)
  - [`k6/scripts/failure.js`](k6/scripts/failure.js)
  - [`k6/scripts/capture_load_run_evidence.sh`](k6/scripts/capture_load_run_evidence.sh)
  - [`k6/k8s/job-baseline.yaml`](k6/k8s/job-baseline.yaml)
  - [`k6/k8s/job-failure-10.yaml`](k6/k8s/job-failure-10.yaml)
  - [`k6/k8s/job-failure-50.yaml`](k6/k8s/job-failure-50.yaml)

- [`observability/`](observability/)
  - [`observability/slo/checkout-slo.yaml`](observability/slo/checkout-slo.yaml)
  - [`observability/slo/frontend-slo.yaml`](observability/slo/frontend-slo.yaml)
  - [`observability/helm/kube-prometheus-stack/values.yaml`](observability/helm/kube-prometheus-stack/values.yaml)

- [`policies/`](policies/)
  - [`policies/slo_v1.rego`](policies/slo_v1.rego)
  - [`policies/slo_v2.rego`](policies/slo_v2.rego)
  - [`policies/slo-policy.yaml`](policies/slo-policy.yaml)

- [`terraform/`](terraform/)
  - [`terraform/README.md`](terraform/README.md)
  - [`terraform/main.tf`](terraform/main.tf)
  - [`terraform/variables.tf`](terraform/variables.tf)
  - [`terraform/outputs.tf`](terraform/outputs.tf)

## Engineering principles

- GitOps-first operations
- Immutable artifacts
- SLOs instead of static thresholds
- Progressive-delivery safety mechanisms
- Policy-as-Code governance
- Observability-driven automation
- Explainable platform decisions

## What this project demonstrates

This repository is intended to showcase practical experience with:

- DevOps and platform architecture
- Site Reliability Engineering practices
- Kubernetes production delivery patterns
- Observability and SLO design
- Release-risk management with error budgets


