# SLO-Gated Rollout Evidence - Dev (`online-shop-dev`)

## 1. System Overview
Validation target: metric-gated progressive delivery for frontend releases in dev.

Components in decision path:
- Argo Rollouts (`Rollout/frontend`, canary steps at 10% and 50%)
- Argo AnalysisRuns (`frontend-slo-check`)
- Prometheus SLO signals (`slo:error_ratio_5m`, `slo:burn_rate_5m`)
- Ingress traffic path (healthy `/`, failure injection `/break`)

## 2. Healthy Path Evidence
### First Gate Pass (10%)
- Rollout reached 10% canary and executed first analysis (`step-index=2`).
- AnalysisRun `frontend-54dfb97d88-4-2` = `Successful`.
- Metric measurements were finite and clean (`error_ratio_5m=0`, `burn_rate_5m=0`).

### Second Gate Pass (50%)
- Rollout progressed to 50% and executed second analysis (`step-index=5`).
- AnalysisRun `frontend-5f574997f4-10-5` = `Successful`.
- Rollout completed to `Healthy` at 100% stable promotion.

## 3. Failure Path Evidence
### First Gate Failure (10%)
- Controlled `/break` degradation introduced during first canary stage.
- AnalysisRun `frontend-5c7856ccc7-5-2` = `Failed`.
- Breach evidence: `error_ratio_5m=0.7022254079816378`, `burn_rate_5m=702.2254079816378`.
- Rollout aborted (`Degraded`) and did not advance to 50% or 100%.

### Second Gate Failure (50%)
- Rollout first gate passed, then reached 50%.
- Controlled `/break` degradation introduced only after first gate success.
- AnalysisRun `frontend-795ff59896-11-5` = `Failed`.
- Breach evidence: `error_ratio_5m=0.23618560428363544`, `burn_rate_5m=236.18560428363543`.
- Rollout aborted before 100% promotion.

## 4. Recovery Evidence
- Post-abort rollout recovered from `Degraded` to `Healthy`.
- `abort` flag cleared.
- Traffic weights restored to stable `100` / canary `0`.
- Stable serving path remained available (`/` returned `200`).
- Failed AnalysisRuns were preserved as historical evidence, not active blockers.

## 5. Key Evidence Snapshots
### Healthy first gate (10%) snapshot
```yaml
rollout:
  phase: Healthy
  stepIndex: 4
analysisRun:
  name: frontend-54dfb97d88-4-2
  phase: Successful
metrics:
  error_ratio_5m: 0
  burn_rate_5m: 0
```

### Healthy second gate (50%) snapshot
```yaml
rollout:
  phase: Healthy
  stepIndex: 7
  weights:
    stable: 100
    canary: 0
analysisRun:
  name: frontend-5f574997f4-10-5
  phase: Successful
metrics:
  error_ratio_5m: 0
  burn_rate_5m: 0
```

### Failure at first gate (10%) snapshot
```yaml
rollout:
  phase: Degraded
  abort: true
  weights:
    stable: 100
    canary: 0
analysisRun:
  name: frontend-5c7856ccc7-5-2
  phase: Failed
metrics:
  error_ratio_5m: 0.7022254079816378
  burn_rate_5m: 702.2254079816378
```

### Failure at second gate (50%) snapshot
```yaml
rollout:
  phase: Degraded
  abort: true
analysisRun:
  name: frontend-795ff59896-11-5
  phase: Failed
metrics:
  error_ratio_5m: 0.23618560428363544
  burn_rate_5m: 236.18560428363543
```

### Recovery-to-baseline snapshot
```yaml
rollout:
  phase: Healthy
  abort: false
  stepIndex: 7
  weights:
    stable: 100
    canary: 0
serviceHealth:
  ingress_root: 200
```

## 6. What This Proves
- Healthy releases are promoted through 10% and 50% canary gates to full rollout.
- Unhealthy releases are blocked at whichever gate observes SLO breach.
- Promotion/abort decisions are metric-driven (`error_ratio_5m`, `burn_rate_5m`).
- Abort keeps stable service available (no forced full outage).
- The rollout process is operationally recoverable after intentional failure testing.
