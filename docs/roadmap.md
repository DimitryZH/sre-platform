# SRE Platform Roadmap

**Status:** Active

**Last reviewed:** 2026-09-17

## Purpose

This document describes the stable development direction of the SRE Platform.
It intentionally focuses on milestones, ownership boundaries, and completion
criteria rather than transient deployment state or command-level execution.

Related documents have separate responsibilities:

- [`validation-history.md`](validation-history.md) records what was validated,
  in which environment, and with which evidence.
- `README.md` explains the platform purpose and demonstrated capabilities.
- [`architecture.md`](architecture.md) describes the platform architecture and
  control loops.
- A private gitignored work plan may track current commands, approvals,
  blockers, costs, temporary decisions, and cleanup actions.

## Responsibility Boundaries

| System | Primary responsibility | Must not become |
| --- | --- | --- |
| SRE Platform | GitOps delivery, observability, SLO evaluation, progressive-delivery decisions, and recovery | Dependent on an AI service for basic monitoring, paging, or recovery |
| PagerDuty | On-call notification, escalation, acknowledgement, and incident lifecycle | A Kubernetes remediation controller or AI investigation state store |
| AI Operations Platform | Durable and governed AI task execution, evidence, audit, and human review | A replacement for PagerDuty or a mandatory component in the paging path |
| HolmesGPT | Replaceable read-only investigator operating within an approved evidence boundary | Owner of incident lifecycle or an autonomous production remediator |

These systems are complementary:

- Prometheus and Alertmanager detect known conditions deterministically.
- PagerDuty routes actionable incidents to the responsible person.
- HolmesGPT investigates and explains evidence.
- The AI Operations Platform governs AI task state, capability checks,
  evidence, retries, and human review.
- The SRE Platform retains ownership of rollout and recovery behavior.

## Roadmap Principles

- Validate one bounded vertical slice before expanding scope.
- Keep deterministic monitoring and SRE recovery independent of AI services.
- Keep PagerDuty and AI investigation paths independently available.
- Use SLO and user-impact signals rather than raw infrastructure noise for
  paging and release decisions.
- Enforce read-only evidence boundaries below the investigator.
- Treat prompt instructions as guidance, not as authorization controls.
- Require explicit approval before cloud writes, cost expansion, or new
  external integrations.
- Preserve failed, partial, and blocked evidence alongside successful results.
- Distinguish repository configuration, historical validation, and current
  runtime validation.
- Defer autonomous remediation until read-only investigation is proven.

## Milestone 1 — Current GKE Staging Reconstruction

**Status:** Completed in staging — September 2026

### Goal

Reconstruct the proven SRE Platform design in the current Google Cloud staging
environment and revalidate its core behavior without overstating historical
results as current runtime state.

### Scope

1. Produce an exact capacity and deployment plan for the approved staging
   environment.
2. Render and inventory every controller, workload, replica, Service,
   persistent volume, and external endpoint.
3. Define explicit CPU and memory requests and limits.
4. Reconcile staging replica counts with the approved capacity envelope.
5. Define Prometheus retention, scrape scope, storage, and observability cost
   controls.
6. Deploy the minimum approved controller and application footprint in a
   reviewed sequence.
7. Converge GitOps resources to an exact repository revision.
8. Establish controlled baseline traffic.
9. Verify Prometheus discovery, SLO recording rules, dashboards, and alert
   evaluation.
10. Run one controlled failure and recovery scenario.

### Expected Components

- Argo CD.
- Argo Rollouts.
- ingress-nginx.
- Approved Online Boutique staging workloads.
- Prometheus and Alertmanager.
- Grafana.
- ServiceMonitors and PrometheusRules.
- Existing SLO and multi-window burn-rate calculations.
- Temporary controlled traffic and failure resources.

### Completion Criteria

- The deployed footprint fits an explicitly reviewed capacity envelope.
- GitOps applications converge to the approved revision.
- Required workloads are ready and serve controlled staging traffic.
- Prometheus discovers the approved targets and evaluates the expected rules.
- Healthy traffic produces a valid SLO baseline.
- Controlled failure produces the expected user-impacting signal.
- The rollout or approved SRE procedure restores healthy service behavior.
- Temporary validation resources are removed.
- Evidence is recorded as current staging validation in
  [`validation-history.md`](validation-history.md).

### September 2026 Outcome

The reviewed staging baseline, GitOps reconciliation, internal traffic path,
SLO evaluation, canary abort, and clean recovery were validated. Prometheus,
Alertmanager, ServiceMonitors, PrometheusRules, and the reviewed dashboard
configuration were included. Grafana UI was deliberately disabled and is not a
completed validation claim.

## Milestone 2 — PagerDuty Incident Response

**Status:** Completed in staging — September 2026

### Goal

Add PagerDuty as the human on-call and incident-lifecycle layer for actionable
SRE Platform alerts.

### Minimum Scope

- One PagerDuty service for SRE Platform staging.
- One on-call schedule.
- One escalation policy.
- One Alertmanager receiver using a secret-managed Events API integration key.
- One customer-impacting SLO alert routed to PagerDuty.
- Stable alert fingerprinting or deduplication behavior.
- Incident context containing environment, service, severity, dashboard,
  runbook, and deployment revision where available.
- One controlled `Triggered -> Acknowledged -> Resolved` validation.

### Routing Policy

- Page only for actionable user-impacting conditions.
- Do not page on every CPU spike, transient pod event, or routine rollout gate.
- Keep warning and ticket-level signals separate from page-level signals.
- Resolve the incident from restored monitored health, not from an AI
  conclusion.
- Keep integration keys and notification credentials out of Git.

### Completion Criteria

- One staging SLO alert creates one deduplicated PagerDuty incident.
- The on-call responder receives and acknowledges the incident.
- The incident resolves when the monitored condition recovers.
- The incident contains enough context to begin investigation without first
  searching for the affected environment or service.
- PagerDuty does not perform Kubernetes remediation.
- PagerDuty remains functional when the AI Operations Platform is unavailable.

### September 2026 Outcome

One constrained staging SLO alert completed the human incident lifecycle:
`Triggered -> Acknowledged -> Resolved`. The delivery path remains independent
of AI investigation and does not perform Kubernetes remediation.

## Milestone 3 — Bounded Read-Only Evidence Interface

**Priority:** After current staging behavior is validated

### Goal

Provide a narrow, enforceable evidence interface for the first external SRE
investigation without granting an investigator broad cluster or observability
access.

### Scope

- A dedicated investigation identity with no mutation permissions.
- Kubernetes access restricted by namespace, approved resource types, names,
  and read verbs.
- Prometheus access restricted to approved parameterized queries and time
  ranges.
- Log access restricted by namespace, workload, pod or container, time range,
  and output size.
- GitOps access restricted to approved repository paths and read operations.
- Stable, sanitized evidence identifiers.
- Cross-project authentication and transport designed explicitly for the
  approved integration boundary.
- Audit records that correlate evidence access with one investigation and time
  window.

### Security Boundary

The following controls are insufficient by themselves:

- natural-language instructions to remain read-only;
- result filtering after an unrestricted query;
- namespace RBAC presented as workload-level isolation;
- unrestricted PromQL with an approved prompt;
- broad pod-log access with a requested time range.

Resource and query restrictions must be enforced below HolmesGPT or any other
investigator.

### Completion Criteria

- The approved investigation can read all required evidence.
- Broader resources, namespaces, queries, write verbs, and unsafe output fail
  closed.
- No secret values or unrestricted endpoints appear in evidence packages.
- Evidence is attributable to the approved target, revision, and time range.

## Milestone 4 — First Governed AI Investigation

**Priority:** After the evidence interface passes its security gates

### Goal

Connect the SRE Platform evidence interface to the AI Operations Platform and a
bounded HolmesGPT executor for one live, read-only investigation.

### Initial Workflow

```text
operator request
-> durable AI Operations task and attempt
-> fail-closed capability verification
-> bounded read-only evidence collection
-> HolmesGPT investigation
-> schema-valid report
-> durable evidence and audit references
-> explicit human review and closeout
```

### Scope

- Begin with an operator-triggered request.
- Keep automatic scheduling disabled for the first live investigation.
- Preserve the AI Operations Platform as the owner of task, attempt, retry,
  timeout, evidence, publication, and human-review state.
- Keep HolmesGPT replaceable behind an executor adapter.
- Require a structured result with explicit findings, evidence references,
  limitations, and recommendations.
- Treat incomplete but schema-valid evidence as a reviewable partial result.
- Fail safely on capability mismatch, timeout, unavailable data, malformed
  output, or uncertain executor state.

### Completion Criteria

- One live investigation completes against the approved staging scenario.
- Every material finding refers to durable evidence.
- The report distinguishes observation, inference, and limitation.
- Retry and stale-attempt behavior remain explicit and auditable.
- Human review accepts, rejects, or requests another attempt.
- HolmesGPT does not mutate the cluster, change Git, or resolve PagerDuty
  incidents.

## Milestone 5 — Unified Incident Demonstration

**Priority:** First integrated MVP completion boundary

### Goal

Demonstrate PagerDuty, the AI Operations Platform, HolmesGPT, and native SRE
recovery working together without overlapping ownership.

### Target Sequence

```text
controlled failure
-> Prometheus and Alertmanager detect the SLO breach
-> PagerDuty opens one incident and notifies the on-call responder
-> the same normalized fingerprint creates one AI Operations task
-> the bounded evidence interface supplies read-only evidence
-> HolmesGPT produces a structured investigation report
-> Argo Rollouts or the approved SRE procedure owns recovery
-> monitored service health returns to normal
-> PagerDuty resolves the incident
-> a human reviews and closes the AI investigation task
```

### Integration Model

The monitoring system may fan out the same normalized condition to PagerDuty
and the AI Operations Platform. The paths must remain independent:

- PagerDuty must not depend on AI task creation to notify the on-call
  responder.
- The AI Operations Platform must not depend on PagerDuty availability to
  accept an approved operator request.
- A shared fingerprint correlates the incident and investigation.
- PagerDuty acknowledgement means that a person owns the incident.
- AI Operations human review means that a person accepts or rejects an AI
  result or follow-up request.
- These are separate decisions and must not be collapsed into one status.

A direct PagerDuty webhook into a private AI endpoint is not assumed. Any
future external ingress or relay requires separate authentication, replay
protection, rate limiting, network, and failure-mode review.

### Completion Criteria

- The complete sequence is repeatable and evidence-backed.
- One operational condition produces one correlated incident and one AI task.
- Repeated delivery does not create duplicate investigation attempts.
- SRE recovery works even if HolmesGPT or the AI Operations Platform fails.
- PagerDuty notification works even if AI investigation fails.
- No AI-generated conclusion directly mutates the cluster or resolves the
  incident.
- The accepted result and known limitations are recorded in
  [`validation-history.md`](validation-history.md).

## Milestone 6 — HolmesGPT Operator Mode Experiment

**Priority:** Post-MVP experiment

### Goal

Evaluate HolmesGPT Operator Mode as an additional proactive, read-only
detection source after the event-triggered integration is complete.

Operator Mode is not treated as a replacement for Prometheus, Alertmanager,
PagerDuty, or the AI Operations Platform.

### Experiment Sequence

1. Deploy Operator Mode only in non-production.
2. Configure one narrowly scoped `ScheduledHealthCheck` for the approved
   staging workload.
3. Begin with an hourly or daily schedule.
4. Run in shadow `monitor` mode without a PagerDuty destination.
5. Use the same bounded evidence interface as the incident-triggered
   investigator.
6. Record every LLM invocation, duration, result, and failure.
7. Disable Kubernetes remediation, unrestricted shell and internet access,
   GitHub writes, and pull-request creation.
8. Compare proactive findings with deterministic monitoring during healthy and
   controlled failure scenarios.

### Required Measurements

- Detection time relative to Prometheus alerts.
- True-positive findings.
- False-positive findings.
- Missed controlled conditions.
- Evidence quality and reproducibility.
- LLM calls and estimated cost per useful finding.
- Query load placed on observability systems.
- Duplicate findings and correlation behavior.
- Behavior during model, data-source, and network failure.

### Promotion Gates

- The Operator remains read-only and bounded.
- Findings are consistently grounded in approved evidence.
- False-positive and cost levels are acceptable for the chosen schedule.
- Failed checks can be normalized and deduplicated safely.
- The Operator does not become a second owner of remediation or incident
  lifecycle.
- Human review approves enabling PagerDuty `alert` mode.

Only after these gates pass may a failed proactive check create a PagerDuty
incident or a normalized AI Operations intake event. Write-capable tools and
autonomous remediation require a separate future decision.

HolmesGPT currently documents Operator Mode as an alpha capability. Its
deployment and use must account for breaking changes and per-check LLM cost.
See the [Holmes Operator documentation](https://holmesgpt.dev/dev/operator/).

## Milestone 7 — Production Expansion

**Priority:** Deferred

### Goal

Design and validate a production environment only after the integrated staging
milestone is complete.

### Candidate Scope

- Production capacity and availability design.
- Production-grade secrets and workload identity.
- Backup, restore, and disaster-recovery validation.
- High-availability observability and alert routing.
- Production SLOs, error budgets, and escalation policies.
- Release promotion criteria from staging to production.
- Multi-cluster or multi-region evaluation where justified.
- Additional governed investigation targets.
- Separately approved remediation workflows with human authorization.

No staging milestone implies approval for production deployment.

## Dependency Order

```text
current staging reconstruction
-> live SLO and recovery validation
-> PagerDuty incident response
-> bounded read-only evidence interface
-> first governed HolmesGPT investigation
-> unified incident demonstration
-> HolmesGPT Operator Mode experiment
-> production expansion
```

Later planning may split a milestone into smaller issues, but it must not bypass
the preceding safety or evidence boundary.

## Cross-Cutting Guardrails

- Keep Git as the source of truth for desired state.
- Never commit credentials, routing keys, tokens, or generated secret values.
- Use exact approval gates before cloud writes or cost expansion.
- Keep budget alerts and a reviewed cleanup path for temporary infrastructure.
- Preserve least privilege and fail closed on ambiguous capability scope.
- Keep PagerDuty independent of the AI investigation path.
- Keep AI investigation independent of SRE recovery automation.
- Do not treat an LLM conclusion as an SLO measurement.
- Do not enable autonomous remediation as an incidental extension of a
  read-only experiment.
- Record sanitized evidence and identify its environment and time period.
- Update [`validation-history.md`](validation-history.md) only after a milestone
  produces reviewable evidence.

## Milestone Summary

| Milestone | Status | Intended outcome |
| --- | --- | --- |
| Current GKE Staging Reconstruction | Completed in staging — September 2026 | GitOps, application, observability, SLO alerting, failure, and recovery are revalidated in the current environment |
| PagerDuty Incident Response | Completed in staging — September 2026 | One actionable SLO alert completes the human incident lifecycle |
| Bounded Read-Only Evidence Interface | Pending | Approved SRE evidence is accessible without broad cluster or observability permissions |
| First Governed AI Investigation | Pending | AI Operations Platform and HolmesGPT complete one bounded read-only investigation |
| Unified Incident Demonstration | Pending | Paging, investigation, native recovery, evidence, and human review work together without ownership overlap |
| HolmesGPT Operator Mode Experiment | Pending experiment | Proactive scheduled checks are evaluated in shadow mode and promoted only if objective gates pass |
| Production Expansion | Pending | Production work begins only after staging evidence and a separate approval boundary |

## Definition of Done for the First Integrated MVP

The first integrated MVP is complete only when:

- the approved application and observability stack run in the current GKE
  staging environment;
- a controlled failure produces the expected SLO breach;
- PagerDuty delivers, acknowledges, and resolves one deduplicated incident;
- one bounded live investigation produces a schema-valid HolmesGPT report with
  durable evidence references;
- SRE-owned recovery restores the service;
- the AI workflow reaches explicit human review and closeout;
- all temporary resources are removed or intentionally retained with documented
  cost and ownership;
- the result is added to the public validation history;
- no autonomous remediation, production claim, or unverified capability is
  presented as complete.
