# SLO-Gated Rollout CLI Excerpts - Dev

All snippets below are **excerpted from validation evidence** and intentionally compact (reconstructed, not full raw logs).

## 1. Healthy Path (10% -> 50% -> 100%)
```text
# rollout watch (compact)
STEP 1  setWeight: 10
STEP 2  analysis: frontend-slo-check -> Successful (frontend-5f574997f4-10-2)
STEP 4  setWeight: 50
STEP 5  analysis: frontend-slo-check -> Successful (frontend-5f574997f4-10-5)
STEP 7  phase: Healthy
weights: stable=100 canary=0
```

```bash
kubectl get analysisrun -n online-shop-dev
```

```text
NAME                      STATUS      AGE
frontend-5f574997f4-10-2  Successful  <age>
frontend-5f574997f4-10-5  Successful  <age>
```

```text
metrics (both successful runs)
burn-rate-5m: [0]
error-ratio-5m: [0]
```

What this proves: healthy rollout passed both canary gates and promoted to a healthy 100% stable state.

## 2. First-Gate Failure (10%)
```text
# rollout watch (compact)
STEP 1  setWeight: 10
STEP 2  analysis: frontend-slo-check -> Failed (frontend-5c7856ccc7-5-2)
phase: Degraded
abort: true
weights: stable=100 canary=0
```

```text
failed analysis values (exact)
burn-rate-5m: [702.2254079816378]    # threshold > 10
error-ratio-5m: [0.7022254079816378] # threshold > 0.02
```

What this proves: first-gate SLO breach immediately aborted rollout and protected stable traffic.

## 3. Second-Gate Failure (50%)
```text
# rollout watch (compact)
STEP 2  analysis -> Successful (frontend-795ff59896-11-2)
STEP 4  setWeight: 50
STEP 5  analysis -> Failed (frontend-795ff59896-11-5)
phase: Degraded
abort: true
weights: stable=100 canary=0
```

```text
second-gate failed values (exact)
slo:error_ratio_5m = 0.23618560428363544
slo:burn_rate_5m  = 236.18560428363543
```

What this proves: rollout can pass gate 1, then correctly fail and abort at gate 2 under controlled degradation.

## 4. Recovery
```bash
kubectl get rollout frontend -n online-shop-dev -o yaml
```

```yaml
status:
  phase: Healthy
  abort: false
  currentStepIndex: 7
  canary:
    weights:
      stable:
        weight: 100
      canary:
        weight: 0
```

What this proves: post-abort recovery restored a clean healthy baseline with stable=100 and canary=0.
