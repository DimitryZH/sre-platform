# Environments

This directory is a **non-breaking skeleton** for future environment-specific overlays (dev / stage / prod).

## Purpose

Each environment will eventually have its own:

- `values/` overrides (for Helm values that should vary per environment)
- `argocd/apps/` root(s) (environment-specific Argo CD Application manifests)

For now, these are **placeholders only** and are not referenced by Argo CD.

## Promotion model

The intended flow is:

1. Deploy and validate in **dev**
2. Promote the same change to **stage**
3. Promote to **prod**

The long-term goal is to keep the structure consistent across environments so promotion is primarily a Git change moving through directories rather than rewriting manifests.

## Current deployment is unchanged

The current deployment entrypoint remains [`argocd/apps/root.yaml`](argocd/apps/root.yaml:1), which still points at the existing app-of-apps tree under `argocd/apps/apps/`.

