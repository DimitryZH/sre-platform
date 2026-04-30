# Load Run Summary

- run_id: failure50-dev-001
- scenario: failure-50
- namespace: online-shop-dev
- start_time_utc: UNKNOWN
- expected_behavior: Failure traffic to /break during rollout 50% gate validation.
- pass_fail: BLOCKED
- actual_behavior: Rollout aborted at the 10% gate before failure-50 traffic started.
- root_cause: Baseline traffic produced unexpected 5xx responses.
- decision: Not valid as failure-50 validation evidence; kept as diagnostic evidence.
