package slo

default allow = false

allow {
    input.fast_burn < input.policy.fast_threshold
    input.slow_burn < input.policy.slow_threshold
    input.error_budget_remaining > input.policy.budget_min
}

deny_reasons[reason] {
    input.fast_burn >= input.policy.fast_threshold
    reason := sprintf("Fast burn %.2f exceeds threshold %.2f",
        [input.fast_burn, input.policy.fast_threshold])
}

deny_reasons[reason] {
    input.slow_burn >= input.policy.slow_threshold
    reason := sprintf("Slow burn %.2f exceeds threshold %.2f",
        [input.slow_burn, input.policy.slow_threshold])
}

deny_reasons[reason] {
    input.error_budget_remaining <= input.policy.budget_min
    reason := sprintf("Error budget remaining %.2f%% below minimum %.2f%%",
        [input.error_budget_remaining, input.policy.budget_min])
}
