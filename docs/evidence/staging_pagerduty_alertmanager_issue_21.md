# Staging PagerDuty Alertmanager Integration - Issue 21

## Repository Design

The staging Alertmanager is enabled with one constrained PagerDuty receiver.
It routes only `OnlineShopSLOFastBurnRatePage` when its explicit stage label
and page severity match. Alertmanager groups by alert name, environment, and
service, preserving its native deduplication behavior. The receiver sends a
resolved event after health recovers.

The receiver supplies environment, service, severity, dashboard, runbook, and
deployment-revision context. The deployment revision is reported as
`unavailable` when the aggregate SLO alert does not carry that label.

The internal `severity: page` label remains the narrow SLO-routing selector.
The PagerDuty event payload maps that page to the API-valid `critical` severity;
this preserves internal routing while preventing rejected delivery requests.

## Validation Finding

A controlled staging validation reached the PagerDuty Events API, but the API
rejected the event with HTTP 400 because `page` is not a valid payload severity.
No incident was created. This finding is limited to the receiver payload; it
does not indicate a credential, routing, or transport failure.

## Secret Delivery Boundary

The routing key is not present in the repository, Helm values, rendered
configuration, or Kubernetes Secret. Alertmanager reads it with
`routing_key_file` from a read-only GKE Secret Manager CSI volume.

The committed configuration references only a fixed, non-secret
SecretProviderClass name. A tracked helper renders the corresponding
SecretProviderClass only into the ignored `.private` directory from a private
operator input. The resulting manifest is not a GitOps object and must not be
committed or logged.

## Completed Staging Integration

The approved staging delivery path was completed with a dedicated
least-privilege workload identity and a read-only secret-backed mount for
Alertmanager. The route remains limited to the reviewed fast-burn SLO alert;
no PagerDuty service, schedule, escalation policy, unrelated alert route, or
remediation behavior was changed.

## Staging Lifecycle Validation

The constrained staging alert was triggered by the reviewed reversible
application-level failure path. The operator acknowledged the resulting
PagerDuty incident. After the failure source was removed, the SLO alert cleared
and Alertmanager delivered the resolved event without a delivery error.

The validated lifecycle is `Triggered -> Acknowledged -> Resolved`. It is
staging-only evidence and does not establish production readiness.

The final validation found a GitOps structured-diff prerequisite in the desired
frontend Rollout: its container port must declare `protocol: TCP` before the
staging application can use Server-Side Apply. The Kubernetes API defaulted
the live field to TCP, so the omitted desired field prevented Argo CD from
constructing a structured diff. This is a reconciliation correctness fix; it
does not change workload ports, resource requests, chart versions, exposure,
storage, or alert-routing scope.

The five terminal AnalysisRuns observed during the reconciliation diagnosis were
controller-retained history, not orphaned temporary resources, and were not
involved in the structured-diff error. The final approved cleanup removed that
retained history after verifying its ownership and terminal status. See the
[consolidated September 2026 staging evidence](staging_delivery_incident_response_validation_september_2026.md).
