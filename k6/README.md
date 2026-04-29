# k6 Load and Failure Testing

## Purpose

This k6 setup is used to validate SLO-gated rollouts by generating controlled traffic and controlled failures in `online-shop-dev`.

It supports:

- healthy traffic validation during rollout analysis
- failure injection against `/break` for gate protection checks
- repeatable execution through Make targets

---

## Scenarios

### Baseline (Healthy Traffic)

Sends steady requests to `/` without failure injection.

Used to validate:

- healthy request behavior during rollout
- stable denominator for SLO calculations
- expected successful gate decisions under normal traffic

### Failure at 10% Gate

Runs failure traffic against `/break` using the `failure-10` scenario label.

Used to validate:

- first canary gate protection
- rollout abort behavior when SLO signals breach thresholds at 10%

### Failure at 50% Gate

Runs failure traffic against `/break` using the `failure-50` scenario label.

Used to validate:

- second canary gate protection after first gate success
- rollout abort behavior before full promotion

---

## Running Tests

Use Make targets from the repository root:

```bash
make load-baseline
make load-failure-10
make load-failure-50
make load-clean
```

What these targets do:

- refresh k6 script ConfigMap from `k6/scripts/`
- delete existing Job with the same name (safe rerun behavior)
- apply the corresponding Job manifest from `k6/k8s/`
- print follow-up commands for logs and rollout checks

---

## Operator Workflow

1. Start baseline traffic:
   `make load-baseline`
2. For first-gate failure validation, start:
   `make load-failure-10` when rollout is at the 10% gate.
3. For second-gate failure validation, start:
   `make load-failure-50` after first gate success and at the 50% gate.
4. Clean up jobs when complete:
   `make load-clean`

---

## Observability and Evidence

Capture evidence during each run:

- k6 logs:
  - `kubectl -n online-shop-dev logs -f job/online-shop-load-baseline`
  - `kubectl -n online-shop-dev logs -f job/online-shop-load-failure-10`
  - `kubectl -n online-shop-dev logs -f job/online-shop-load-failure-50`
- rollout status:
  - `kubectl -n online-shop-dev get rollout frontend -w`
- AnalysisRun status:
  - `kubectl -n online-shop-dev get analysisrun -w`
- SLO signal impact in Grafana/Prometheus:
  - `slo:error_ratio_5m`
  - `slo:burn_rate_5m`

---

## Notes

- This MVP does not automate rollout gate timing yet.
- Test timing is operator-controlled.
- Start failure scenarios at the intended rollout gate (10% or 50%).
