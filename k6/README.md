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
make load-baseline RUN_ID=baseline-20260429-1200
make load-failure-10 RUN_ID=failure-10-20260429-1230
make load-failure-50 RUN_ID=failure-50-20260429-1300
make load-clean
```

`RUN_ID` is used to organize evidence under one run folder.
If `RUN_ID` is omitted, the capture script generates a UTC timestamp-based ID automatically.

What these targets do:

- refresh k6 script ConfigMap from `k6/scripts/`
- delete existing Job with the same name (safe rerun behavior)
- apply the corresponding Job manifest from `k6/k8s/`
- print follow-up commands for logs, rollout checks, and evidence capture

---

## Operator Workflow

1. Start baseline traffic:
   `make load-baseline RUN_ID=<run-id>`
2. For first-gate failure validation, start:
   `make load-failure-10 RUN_ID=<run-id>` when rollout is at the 10% gate.
3. For second-gate failure validation, start:
   `make load-failure-50 RUN_ID=<run-id>` after first gate success and at the 50% gate.
4. Capture run evidence:
   `make load-capture-baseline RUN_ID=<run-id>` or scenario-specific capture target.
5. Clean up jobs when complete:
   `make load-clean`

---

## Observability and Evidence

Run evidence is captured under:

- `docs/evidence/load-runs/<RUN_ID>/`

Evidence capture targets:

```bash
make load-capture-baseline RUN_ID=<run-id>
make load-capture-failure-10 RUN_ID=<run-id>
make load-capture-failure-50 RUN_ID=<run-id>
```

Generated files:

- `summary.md`
- `k6.txt`
- `job.txt`
- `pods.txt`
- `rollout.txt`
- `analysisruns.txt`

Manual commands captured by the script include:

- k6 logs:
  - `kubectl -n online-shop-dev logs job/<job-name>`
- Job status and details:
  - `kubectl -n online-shop-dev get job <job-name> -o wide`
  - `kubectl -n online-shop-dev describe job <job-name>`
- Pod status:
  - `kubectl -n online-shop-dev get pods -l app.kubernetes.io/name=online-shop-k6 -o wide`
- rollout status:
  - `kubectl -n online-shop-dev get rollout frontend`
  - `kubectl -n online-shop-dev get rollout frontend -o yaml`
- AnalysisRun status:
  - `kubectl -n online-shop-dev get analysisrun --sort-by=.metadata.creationTimestamp`
- SLO signal impact in Grafana/Prometheus:
  - `slo:error_ratio_5m`
  - `slo:burn_rate_5m`

---

## Notes

- Rollout gate timing is not automated by these tests.
- Test timing is operator-controlled.
- Start failure scenarios at the intended rollout gate (10% or 50%).
- k6 Jobs keep completed resources for up to 1 hour (`ttlSecondsAfterFinished: 3600`) to support evidence collection.
