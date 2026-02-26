# Project Context: SRE Platform

## Overview
This repository hosts the SRE platform configuration, including the observability stack and environment-specific overlays for the "Online Shop" application. It uses a GitOps approach with Argo CD.

## Architecture & Patterns

### GitOps (Argo CD)
- **Entrypoint**: The main entrypoint is `argocd/apps/root.yaml` (App of Apps).
- **Multi-Source**: Applications use Argo CD's multi-source feature to combine upstream Helm charts with local value files.
- **Path Referencing**: MUST use `$values/` prefix for value files to ensure stable paths (e.g., `$values/observability/helm/...`). Avoid relative paths like `../../`.

### Environments
- **Structure**: `environments/{dev,stage,prod}`.
- **Promotion Flow**: Dev → Stage → Prod.
- **State**:
  - `dev`: Automated sync enabled (prune, self-heal).
  - `stage` & `prod`: Manual sync for safety.
- **Values**: Environment-specific values are in `environments/{env}/values/`. Keep these minimal.

### Observability (Prometheus)
- **Stack**: `kube-prometheus-stack`.
- **Service Discovery**:
  - Prometheus is configured to be restrictive.
  - **Requirement**: ServiceMonitors MUST have the label `release: monitoring` to be scraped.
  - **Namespaces**: Scrapes are limited to `monitoring` and `online-shop` namespaces.
- **Access Control**:
  - Ingress is protected by Basic Auth.
  - Secret: `prometheus-basic-auth` in `monitoring` namespace.
  - **Action**: You must manually generate the `auth` string (htpasswd) and create the secret. Do NOT commit the hash.

## Development Rules

1. **Secrets**:
   - Never commit raw secrets.
   - Use placeholders (e.g., `CHANGE_ME`) in YAMLs.
   - See `observability/ingress/prometheus-basic-auth-secret.yaml` for examples.

2. **Image Tags**:
   - Always pin image tags in environment overlays (e.g., `tag: v1`) to ensure deterministic deployments.

3. **Namespaces**:
   - Global namespace variables are defined in `global.namespace`.
   - Current dev namespace: `online-shop-dev`.

## Common Commands

### Render Templates (Dev)
```bash
make render ENV=dev
```

### Preflight Check (Dev)
```bash
make preflight ENV=dev
```