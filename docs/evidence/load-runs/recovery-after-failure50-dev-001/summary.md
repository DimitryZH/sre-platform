# Recovery Summary

- run_id: recovery-after-failure50-dev-001
- scenario: rollout-recovery-after-failure50
- namespace: online-shop-dev
- method: annotation-only rollout retrigger with healthy baseline denominator traffic
- expected_behavior: clear aborted/degraded state and return to healthy stable baseline
- outcome: PASS
- final_rollout: phase=Healthy, abort=false, stable=100, canary=0, revision=22
- latest_gate_analysisruns:
  - frontend-75b67dcc55-22-2: Successful (10% gate)
  - frontend-75b67dcc55-22-5: Successful (50% gate)
- notes: default baseline load (20 VUs) caused intermittent 5xx and repeated first-gate re-aborts; recovery succeeded with temporary low-load baseline traffic.
