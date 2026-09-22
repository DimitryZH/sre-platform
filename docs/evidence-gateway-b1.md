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

The only caller identity is the neutral `staging-evidence-client`. This is an
offline contract identity; no concrete transport identity is implemented here.

The B1 fake providers remain in-memory test doubles for contract shape,
projection, sanitization, limits, fail-closed behavior, and zero provider calls
for policy rejections. Provider parameters are built only from the validated
server-approved request and include the approved target plus the approved
start/end time range where applicable. Events receive their maximum item count;
logs receive maximum lines, bytes, and line length; Prometheus receives maximum
values per template; and pod status receives the fixed frontend selector and
maximum pod count.

## Offline Provider Adapters

The repository also provides adapters whose backend protocols are injected by
local contract tests. They create no clients, read no credentials, and perform
no network I/O. The adapters retain the B1 provider method signatures and call
only narrow backend operations:

- Kubernetes state uses exact namespace/name reads for `Rollout/frontend` and
  the Ingress. The adapter derives both workload and rollout evidence from the
  normalized Rollout response; it does not request `Deployment/frontend`. Pod
  status uses the fixed frontend selector and limit; Events use exact
  involved-object pairs while consuming one bounded total.
- Container logs use the fixed namespace, workload, and frontend container with
  server-owned time, line, byte, and line-length limits.
- Prometheus accepts template IDs only and has no raw query method. The current
  B2 adapter marks both available recording-rule templates unavailable before
  transport because their aggregated output cannot distinguish the approved
  staging target.
- Deployment revision resolves the exact Argo CD Application and requires an
  immutable commit SHA. GitOps files use allowlisted path IDs only and can be
  read only at that resolved SHA with a server-owned byte limit.

Malformed, broad, ambiguous, or over-limit adapter requests fail before a
backend method is called. Backend exceptions and limit violations map to the
existing deterministic unavailable-backend response through the Gateway.

### Normalized Backend Inputs

Transport backends return narrow normalized records, not arbitrary Gateway
responses. A Rollout record contains its identity, replica counts, conditions,
phase, current step, stable/canary services, and Rollout-owned AnalysisRuns;
the adapter derives the two Kubernetes state inputs from it. Pod, Event, and
log records use the bounded fields listed in their evidence sections below.
Argo CD returns the requested Application identity and sync/health status.
GitOps returns only the content of the adapter-selected allowlisted path at the
adapter-verified SHA. The current B2 Prometheus adapter makes no backend query:
the available aggregated recording-rule output is not target-distinguishable.

## Allowed Evidence

Allowed evidence kinds are:

- `kubernetes_state`
- `kubernetes_pod_status`
- `kubernetes_events`
- `logs`
- `prometheus`
- `deployment_revision`
- `gitops`

Allowed Prometheus template IDs are:

- `slo_error_ratio_5m` -> `slo:error_ratio_5m`
- `slo_burn_rate_5m` -> `slo:burn_rate_5m`

The B1 fake provider continues to cover this abstract bounded template
contract. The current B2 adapter rejects both templates before calling its
backend because the recording-rule output is not target-distinguishable.

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
- `Ingress/online-shop-frontend`

AnalysisRuns are returned only when ownership is deterministically proven by
`Rollout/frontend`. Ingress paths must match the exact approved `/stage` path.
Events, log lines, and Prometheus samples must fall inside the approved
request time range.

Pod status uses a separate typed frontend provider call with the fixed
`app.kubernetes.io/name=frontend` selector. Its safe projection is limited to
logical pod identifier, phase, ready state, restart count, and frontend
container state. UID, node name, pod or host IP, image ID, labels,
annotations, and raw pod objects are never returned. Out-of-scope, malformed,
or excessive pod data fails closed.

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
- frontend pod status items: 10
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
deployment-facing implementation is not included in this repository slice.

Replay and offline rate-policy denials retain that safe context only after the
request has completed schema, authentication, target, and time-range
validation; malformed or unauthorized requests do not gain caller-attributed
audit fields. `audit.response_bytes` is the byte length of the final canonical
response envelope, including that field itself, and the allowed response limit
is enforced against the same final envelope.

## Private Runtime Boundary

The repository includes a private WSGI runtime boundary with one route only:

```text
POST /v1/evidence/staging/frontend
Authorization: Bearer <projected-service-account-token>
X-Request-Nonce: <canonical unpadded base64url nonce>
Content-Type: application/json
```

Its JSON body retains only the typed B1 request fields. An `auth` field in the
body is rejected. An injected TokenReview contract must return exactly the
audience `sre-platform-evidence-gateway` and the Kubernetes subject
`system:serviceaccount:evidence-gateway-validation:staging-evidence-client`.
The nonce must be canonical unpadded base64url that decodes to exactly 32
bytes. The runtime derives the B1 identity, scope, issue/expiry times, and
nonce envelope on the server; it never accepts them from the caller body. The
runtime token configuration reserves a separate projected Kubernetes API token
path with audience `https://kubernetes.default.svc`.

Replay entries, hourly request rate entries, and sanitized audit events are
stored in bounded SQLite state. After successful TokenReview, the gateway
atomically reserves one replay/rate decision keyed by the exact Kubernetes
subject and persists a sanitized audit event before returning any parsed-body
result, including format denials. The runtime serializes explicit SQLite
transactions, prunes expired replay/rate records and retained audit records
deterministically, and preserves timestamps at microsecond precision. It
checks SQLite integrity when opening state and enforces count, logical-byte,
and combined SQLite/WAL/SHM byte limits; recovery, capacity, integrity,
serialization, or audit failures deny the request without partial state.
Durable audit data contains event time plus only approved identity, request,
evidence-kind, decision, identifier, count, and byte-total fields. It excludes
bearer tokens, nonces, raw evidence, provider parameters, response content,
and backend error details.

Internal source and GitHub-proxy transports are typed and mTLS-validated before
their injected backend is called. The gateway client identity is the exact URI
SAN `spiffe://evidence-gateway-stage/gateway`. Source and GitHub-proxy server
certificates must be trusted by the configured CA, be currently valid between
their `not_before` and `not_after` times, match their exact DNS and URI SANs,
and use the appropriate client or server EKU. Any malformed certificate,
trust, validity, EKU, DNS, or URI mismatch fails closed. The source transport
exposes only frontend state and deployment-revision operations; it accepts no
generic resource, selector, URL, query, or Git reference input.

GitHub-proxy reads accept an allowlisted path ID and immutable SHA only. The
response JSON/base64 envelope is limited to 16 KiB and decoded content is
limited to 4 KiB. The decoder requires GitHub Contents `file`, `path`,
`encoding`, and `content` fields with `file`/`base64` semantics, and ignores
unneeded response fields. Invalid shapes, paths, revisions, encodings,
oversized content, and HTTP 403/429 responses return the deterministic
unavailable backend response without exposing transport detail.

In this runtime foundation, Events, logs, Prometheus, and pod-status requests
are unavailable and fail before a source or backend transport is called.
AnalysisRuns are excluded from runtime state evidence unless the source output
proves an empty set; unapproved AnalysisRun data fails closed.

## Non-Claims

B1 does not include:

- live providers;
- credentials;
- ServiceAccounts, RBAC, IAM, network, or private endpoint resources;
- deployable resources under actively reconciled GitOps paths;
- model invocation;
- downstream integration;
- Scheduler, PagerDuty, remediation, or production access.

The runtime foundation also does not include deployable Kubernetes manifests,
container registry references, image publication, live clients, or network
operations.
