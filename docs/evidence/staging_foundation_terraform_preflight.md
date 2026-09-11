# Staging Foundation Terraform Preflight

Date: 2026-09-07

## Scope

This began as a read-only preflight for the existing approved staging foundation. Imports and one separately approved apply were subsequently completed; the apply was limited to project labels and state-bucket labels plus bounded, non-locked retention.

## Verified Invariants

- An active gcloud authentication state was present; account identity details were not recorded.
- The configured project and explicit target both resolved to the approved staging target.
- The target project was active and had the existing foundation labels.
- Billing was enabled. One existing 100 CAD budget was scoped to exactly that project through its numeric project reference.
- The billing boundary retained `roles/billing.costsManager`; broad `roles/billing.admin` and public principals were absent.
- The direct state-bucket IAM policy contained no public principals or legacy bucket pseudo-principals.
- The state bucket is in `US-CENTRAL1` with uniform bucket-level access, public access prevention, versioning, a 30-day non-locked retention policy, a 7-day soft-delete policy, and foundation labels.

## Deliberate Limits

- Existing non-foundation APIs are not disabled or managed by this baseline.
- Existing IAM memberships are not copied into this repository. Optional IAM members require explicit approved operator input.
- No raw state, plan, tfvars, credentials, account identifiers, or principal identifiers are recorded.
- An obsolete, untracked local Terraform state was intentionally discarded without inspection. It is not part of this staging foundation and must not be migrated to the remote backend.
- The new remote backend starts with empty state and will receive resources only through separately approved imports.

## Import Status

- The empty remote backend now records the project, the three approved foundation APIs, the state bucket, and the existing project-scoped budget.
- A provider alias scopes quota-project attribution to Billing Budgets API calls only. No credential, IAM, API, budget, or other cloud resource change was made.
- The imported budget is protected from drift and apply through `ignore_changes = all` until a separate budget-management approval.
- Read-only verification found only the expected imported addresses and no unexpected addresses. State content was not displayed.

## Approved Apply Status

- The exact reviewed saved plan was applied after SHA-256, resource-address, action, cost-heavy-resource, and retention-lock checks.
- The apply changed only project labels plus state-bucket labels and the bounded 30-day retention policy.
- No budget, IAM, API, compute, network, workload, logging, monitoring, or other cloud resource change was applied.

Live staging validation remains pending. No live workload or investigation validation was performed.
