
package slo

default allow = false

allow {
  input.fast_burn < 14
  input.slow_burn < 3
  input.error_budget_remaining > 20
}
