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

All providers in B1 are fake in-memory providers. They are used only to prove
contract shape, projection, sanitization, limits, fail-closed behavior, and
zero provider calls for policy rejections.

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

Unknown, malformed, stale, replayed, mutation-like, out-of-scope, ambiguous,
unsafe, or oversized requests fail closed before any provider is called.

## Identifiers

`request_fingerprint` is:

```text
SHA256(canonical JSON of the approved request)
```

The approved request is the server-normalized operation, fixed target, bounded
time range, requested approved evidence kinds, limits, Prometheus template IDs,
and GitOps path IDs. Authentication material is validated but not included in
the approved request fingerprint.

`evidence_id` is:

```text
SHA256(canonical JSON of the sanitized result)
```

The result is projected into explicit allowlisted fields before hashing. Raw
backend objects are never returned.

## Response Boundary

Allowed envelopes are:

- allowed envelope: schema version, outcome, request fingerprint, evidence ID,
  approved target, time range, limits, sanitized result, and audit metadata;
- denied envelope: schema version, outcome, deterministic error code/message,
  and audit metadata.

Provider call counts are included in B1 audit metadata so tests can prove
policy rejections caused zero provider calls. A future deployment-facing
envelope may remove or further restrict that diagnostic detail if needed.

## Non-Claims

B1 does not include:

- live providers;
- credentials;
- ServiceAccounts, RBAC, IAM, network, or private endpoint resources;
- deployable resources under actively reconciled GitOps paths;
- HolmesGPT or model invocation;
- AI Operations Platform integration;
- Scheduler, PagerDuty, remediation, or production access.
