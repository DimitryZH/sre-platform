# Staging SLO Validation - Issue 17

## Session Status

The staging validation session established an in-cluster healthy traffic path
and confirmed that Prometheus scrapes the ingress metrics target. The full
canary and controlled-degradation scenario is blocked pending reconciliation of
the focused repository corrections in this change.

## Sanitized Findings

- The original staging traffic fixtures referenced a controller Service name
  that the pinned ingress chart did not render.
- The chart's `service.external.enabled` setting controls creation of its
  primary controller Service; enabling it with `type: ClusterIP` creates an
  internal-only traffic path and does not render a LoadBalancer.
- The hostless staging Ingress requires the pinned controller's
  `metrics-per-undefined-host` argument before it emits request SLI series.
- After the internal traffic path and metric argument were corrected, the
  controller emitted request series and Prometheus scraped the metrics target.
- The existing SLO recording rules selected `exported_namespace`, while the
  controller emits the Kubernetes `namespace` label. This made healthy traffic
  appear as a zero SLO denominator and would invalidate canary analysis.

## Safety Decision

No canary trigger, controlled degradation, alert validation, or abort/recovery
action was run while the recording rules used the ineffective selector. After
this correction is merged and reconciled, rerun the healthy denominator check
before beginning the approved canary and reversible failure sequence.

## Scope Confirmation

The correction keeps the pinned controller and chart versions unchanged. It
does not introduce public exposure, persistent storage, new controllers,
Terraform changes, or external integrations.
