# Environments

This directory holds environment-specific overlays (dev / stage / prod).

## Purpose

Each environment has its own:

- `values/` overrides (for Helm values that should vary per environment)
- `argocd/apps/` root(s) (environment-specific Argo CD Application manifests)

Current root expansion references environment bootstrap apps from `argocd/apps/apps/envs.yaml`.
The dev environment path is the first canonical deployment target.

## Promotion model

The intended flow is:

1. Deploy and validate in **dev**
2. Promote the same change to **stage**
3. Promote to **prod**

The long-term goal is to keep the structure consistent across environments so promotion is primarily a Git change moving through directories rather than rewriting manifests.

## Current deployment entrypoint

The deployment entrypoint remains `argocd/apps/root.yaml`, which expands
`argocd/apps/apps/` and includes environment bootstrap apps.
Dev is automated via `bootstrap-dev`; stage/prod remain manual bootstrap paths.

