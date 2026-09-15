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
- The controller endpoint exposes a Kubernetes `namespace` label, but the
  reviewed ServiceMonitor relabels it to `exported_namespace` in Prometheus.
  Recording rules must select the post-relabel label.

## Safety Decision

An initial canary/failure attempt was stopped and cleaned up after the selector
was found to be ineffective. Its successful AnalysisRuns are not validation
evidence because they evaluated empty SLO recording rules. After this
correction is merged and reconciled, rerun the healthy denominator check before
beginning the approved canary and reversible failure sequence.

## Scope Confirmation

The correction keeps the pinned controller and chart versions unchanged. It
does not introduce public exposure, persistent storage, new controllers,
Terraform changes, or external integrations.
