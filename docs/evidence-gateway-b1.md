# Evidence Gateway B1 Offline Security Core

This document describes the repository-only security core implemented for
Issue #26 Pass B1. It is not a deployment guide and does not authorize live
Kubernetes, Prometheus, log, GitOps, cloud, network, model, Scheduler, or
cross-project access.

## Boundary

The B1 core implements one typed operation:

```text
collect_staging_frontend_evidence
```

The caller cannot provide Kubernetes resource names, selectors, PromQL, Git
paths, repository names, refs, pod names, or container names. Those values are
owned by the server policy and fixed to the approved staging target:

- environment: `staging`
- namespace: `online-shop-stage`
- workload/service: `frontend`
- Rollout: `frontend`
- Ingress: `online-shop-frontend`
- Argo CD Application: `online-shop-stage`
- maximum time range: 60 minutes

The only B1 caller identity is the neutral `staging-evidence-client`. This is
an offline contract identity, not a HolmesGPT or AI Operations identity. The
concrete private transport identity is intentionally deferred to B3.

All providers in B1 are fake in-memory providers. They are used only to prove
contract shape, projection, sanitization, limits, fail-closed behavior, and
zero provider calls for policy rejections. Provider parameters are built only
from the validated server-approved request and include the approved target plus
the approved start/end time range where applicable.

## Allowed Evidence

Allowed evidence kinds are:

- `kubernetes_state`
- `kubernetes_events`
- `logs`
- `prometheus`
- `deployment_revision`
- `gitops`

Allowed Prometheus template IDs are:

- `slo_error_ratio_5m` -> `slo:error_ratio_5m`
- `slo_burn_rate_5m` -> `slo:burn_rate_5m`

The namespace-only ingress request-rate query is intentionally not included in
B1 because the current contract does not prove an exact frontend/ingress
boundary for that expression.

Prometheus samples must not contain conflicting `namespace`,
`exported_namespace`, `service`, or `ingress` labels. Samples with `NaN`,
`Infinity`, or timestamps outside the approved time window fail closed.

Allowed GitOps path IDs are:

- `stage_argocd_application`
- `stage_values`
- `frontend_rollout`
- `frontend_ingress`
- `frontend_analysis_template`
- `prometheus_rules`
- `burn_rate_alerts`

GitOps file reads require an immutable deployment commit SHA resolved through
the approved Argo CD Application contract. Caller-supplied repository, ref,
path, wildcard, traversal, Terraform, private, or arbitrary file access is not
part of the request schema.

Kubernetes Events are limited to exact approved `(kind, name)` pairs:

- `Rollout/frontend`
- `Deployment/frontend`
- `Ingress/online-shop-frontend`

AnalysisRuns are returned only when ownership is deterministically proven by
`Rollout/frontend`. Ingress paths must match the exact approved `/stage` path.
Events, log lines, and Prometheus samples must fall inside the approved
request time range.

## Limits

The B1 core enforces conservative offline limits:

- time range: 60 minutes
- auth lifetime: 10 minutes
- evidence sections: 32
- sanitized result size: 32 KiB
- log lines: 80
- log line length: 240 characters
- log bytes: 8 KiB
- GitOps file bytes: 4 KiB per approved file
- event message length: 240 characters
- workload conditions: 8
- Rollout AnalysisRuns: 8
- Events: 16
- Prometheus values per template: 16
- replay entries: 128, expiry-aware and fail-closed at capacity
- offline per-subject rate policy: 60 requests per minute, in memory only

Unknown, malformed, future-window, stale, replayed, mutation-like,
out-of-scope, ambiguous, unsafe, malformed-provider, unavailable-backend, or
oversized requests fail closed. Policy rejections happen before any provider is
called; provider failures return deterministic safe errors without backend
detail leakage.

## Identifiers

`request_fingerprint` is:

```text
SHA256(canonical JSON of the approved request)
```

The approved request is the server-normalized operation, fixed target, bounded
time range, requested approved evidence kinds, limits, Prometheus template IDs,
and GitOps path IDs. Authentication material, request IDs, and caller identity
are validated but not included in the approved request fingerprint.

`evidence_id` is:

```text
SHA256(canonical JSON of schema/type, approved target, approved time range,
source revision where applicable, and result digest)
```

The result is projected into explicit allowlisted fields before hashing. Raw
backend objects are never returned. Canonical JSON is deterministic and rejects
non-standard numeric values.

## Response Boundary

Allowed envelopes are:

- allowed envelope: schema version, outcome, request fingerprint, evidence ID,
  approved target, time range, limits, sanitized result, and audit metadata;
- denied envelope: schema version, outcome, deterministic error code/message,
  and audit metadata.

Per-request provider call counts and exact safe provider parameters are
included in B1 audit metadata so tests can prove policy rejections caused zero
provider calls and replay denials do not reuse cumulative audit state. Audit
also includes the validated request ID, safe caller identity, operation, target,
time range, decision or denial reason, request fingerprint, evidence ID/result
digest where available, revision where applicable, counts, and byte totals. A
future deployment-facing envelope may remove or further restrict that
diagnostic detail if needed.

## Non-Claims

B1 does not include:

- live providers;
- credentials;
- ServiceAccounts, RBAC, IAM, network, or private endpoint resources;
- deployable resources under actively reconciled GitOps paths;
- HolmesGPT or model invocation;
- AI Operations Platform integration;
- Scheduler, PagerDuty, remediation, or production access.
