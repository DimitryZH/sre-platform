# Recovery Summary

- run_id: recovery-after-failure10-dev-001
- scenario: rollout-recovery-after-failure10
- namespace: online-shop-dev
- recovery_started_at: 2026-04-30T10:38:32-04:00
- recovery_retrigger_at: 2026-04-30T11:06:03-04:00
- method: annotation-only rollout patch (`rollout-trigger`) plus baseline healthy traffic
- expected_behavior: clear aborted/degraded state and return to healthy stable baseline
- outcome: PASS
- final_rollout: phase=Healthy, abort=false, stable=100, canary=0, revision=16
- notes: first retrigger paused inconclusive due low denominator; second retrigger overlapped baseline load and both gate analyses succeeded.
