# sre-platform Deployment Guide (Current Working Flow)

This guide documents the currently validated dev deployment flow.
For the target final platform vision and advanced capabilities such as canary rollouts, SLO-gated rollback, and policy enforcement, see README.md and ARCHITECTURE.md.


This guide matches the current validated deployment flow for the repo:

- Terraform bootstraps a fresh GCP project and GKE cluster
- Argo CD bootstraps GitOps from `argocd/apps/root.yaml`
- Dev-only path is canonical (`bootstrap-dev` -> `monitoring-shared-dev` + `online-shop-dev`)
- Frontend is reachable over HTTP via LoadBalancer IP (no DNS required)

## 1. Prerequisites

- Tools: `terraform`, `gcloud`, `kubectl`, `helm`, `make`
- GCP access to target project (example: `sre-platform-dev`)
- Billing attached to the target project
- `gcloud auth login` completed

Set variables for target project and region:

```powershell
$env:PROJECT_ID="PROJECT_ID_HERE"
$env:REGION="us-central1"
```

## 2. Terraform Apply (Fresh Project)

From repo root:

```powershell
cd terraform
terraform init
terraform plan -var "project_id=$env:PROJECT_ID" -var "region=$env:REGION" -out=tfplan
terraform apply tfplan
```

What this creates:

- Required project APIs
- Dedicated GKE node service account + IAM binding
- VPC + subnet
- Regional GKE cluster + node pools

## 3. kubeconfig Setup

```powershell
$cluster = terraform -chdir=terraform output -raw cluster_name
$region  = terraform -chdir=terraform output -raw region
gcloud container clusters get-credentials $cluster --region $region --project $env:PROJECT_ID
kubectl get nodes
```

## 4. Install Cluster Controllers

From repo root:

```powershell
make install-ingress
make install-argocd
make install-argo-rollouts
```

Validate controller namespaces:

```powershell
kubectl get ns ingress-nginx,argocd,argo-rollouts,monitoring,online-shop-dev
```

`monitoring` and `online-shop-dev` may not exist yet; they are created by Argo CD sync.

## 5. Bootstrap Argo CD Root App

```powershell
make argocd-root
kubectl -n argocd get applications
```

Expected canonical app chain:

1. `online-shop-platform`
2. `bootstrap-dev`
3. `monitoring-shared-dev`
4. `online-shop-dev`

`online-shop-platform` is configured to include only `argocd/apps/apps/envs-dev.yaml` (dev-only path).

## 6. Dev App Convergence

Track convergence:

```powershell
kubectl -n argocd get applications -w
```

Wait until all are `Synced` and `Healthy`:

- `online-shop-platform`
- `bootstrap-dev`
- `monitoring-shared-dev`
- `online-shop-dev`

Optional local validation before/after cluster sync:

```powershell
make preflight ENV=dev PROJECT_ID=$env:PROJECT_ID
```

## 7. Access Frontend via LoadBalancer IP (HTTP)

Get ingress controller external IP:

```powershell
$lb = kubectl -n ingress-nginx get svc ingress-nginx-controller -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
$lb
```

Verify app ingress and backend mapping:

```powershell
kubectl -n online-shop-dev get ingress online-shop-frontend
kubectl -n online-shop-dev get svc frontend
```

Smoke test over HTTP (no DNS, no TLS):

```powershell
curl.exe -I "http://$lb/"
```

Open in browser:

```text
http://<LOADBALANCER_IP>
```

## Troubleshooting (Real Issues Seen)

### Helm rendering fails with nil pointer

Symptoms:

- `make preflight ENV=dev` fails in `helm lint`/`helm template`
- Errors around missing nested values (for example rollout analysis or serviceAccount keys)

Action:

- Add explicit defaults in chart `values.yaml`
- Make templates nil-safe when reading nested values

### Argo CD app stuck OutOfSync/Running for monitoring

Symptoms:

- `monitoring-shared-dev` does not converge
- CRD-heavy sync gets stuck during apply/pre-sync

Action:

- Ensure monitoring app has sync options for CRD-heavy installs:
  - `ServerSideApply=true`
  - `Replace=true`

### `online-shop-dev` becomes Degraded at runtime

Symptoms:

- App is `Synced` but `Degraded`
- Pods restart or readiness fails

Action:

1. `kubectl -n online-shop-dev get pods`
2. `kubectl -n online-shop-dev logs <pod> --previous`
3. Verify service-to-service env/config wiring from chart values and generated ConfigMaps
4. Re-sync app after fix

## Current Successful End State (Dev)

- `online-shop-platform`: `Synced/Healthy`
- `bootstrap-dev`: `Synced/Healthy`
- `monitoring-shared-dev`: `Synced/Healthy`
- `online-shop-dev`: `Synced/Healthy`
- Frontend reachable over HTTP via ingress LoadBalancer IP

## What this guide does not cover yet

- Stage / production environment convergence
- Full progressive delivery enablement (Argo Rollouts as primary release path)
- TLS / DNS / production-grade ingress configuration
- GitHub PR gate and OPA-based policy enforcement
- Full k6-based load and failure scenario validation

Additionally, the core SRE governance layer is not yet fully exercised in this guide:

- End-to-end SLO validation under real load
- Error budget tracking and burn rate evaluation in live scenarios
- Automated rollback decisions based on SLO violations
- Explainable release decisions (promote vs rollback)

These capabilities are part of the intended final platform design and are covered in `README.md` and `ARCHITECTURE.md`, but are not yet implemented or validated in the current dev deployment flow.