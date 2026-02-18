# 🚀 SLO-Driven Progressive Delivery Platform

## Full Deployment Guide (Production-Grade)

This guide explains how to deploy the full SLO-driven online-shop platform with:

- Helm-based services
- ArgoCD (GitOps)
- Argo Rollouts (Canary)
- Prometheus (multi-window burn rate)
- Grafana (SLO dashboards)
- OPA (policy as code)
- GitHub PR release gate

At the end you will see:

- Live burn rate metrics
- Auto rollback on SLO violation
- Grafana dashboards
- PR merge blocking
- Explainable release decisions

---

# 0️⃣ Prerequisites

## Local tools

- kubectl
- helm 3+
- docker
- jq
- GitHub repository
- Docker registry (DockerHub or GHCR)
- k6 (for load testing)

## Kubernetes cluster

Recommended:
- Kubernetes 1.26+
- 4 CPU minimum
- 8GB RAM minimum
- Ingress controller (nginx)
- Default StorageClass

You can use:
- Kind (demo)
- Minikube
- EKS / GKE / AKS

---

# 1️⃣ Install Platform Components

---

## 1.1 Install ArgoCD

```bash
kubectl create namespace argocd

helm repo add argo https://argoproj.github.io/argo-helm

helm install argocd argo/argo-cd -n argocd
```

Get admin password:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
  -o jsonpath="{.data.password}" | base64 -d
```

Port forward:

```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
```

## 1.2 Install Argo Rollouts

```bash
kubectl create namespace argo-rollouts

helm install argo-rollouts argo/argo-rollouts \
  -n argo-rollouts
```

Install kubectl plugin:

```bash
kubectl argo rollouts version
```

## 1.3 Install kube-prometheus-stack

```bash
kubectl create namespace monitoring

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts

helm install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring
```

This installs:

- Prometheus
- Alertmanager
- Grafana
- Node exporter

---

# 2️⃣ Deploy Online Shop Services (Helm)

We deploy 4 services:

- frontend
- cart
- checkout
- payment

Example:

```bash
helm dependency build charts/platform
helm upgrade --install online-shop charts/platform -n online-shop
```

Repeat for other services.

Verify:

```bash
kubectl get pods
```

---

# 3️⃣ Apply SLO Burn Rate Recording Rules

Apply rules:

```bash
kubectl get prometheusrule -A | findstr online-shop
```

Port-forward Prometheus:

```bash
kubectl port-forward svc/monitoring-kube-prometheus-prometheus 9090 -n monitoring
```

Verify metrics:

- `slo:burn_rate_5m`
- `slo:burn_rate_1h`

---

# 4️⃣ Import Grafana Dashboard

Port-forward:

```bash
kubectl port-forward svc/monitoring-grafana 3000:80 -n monitoring
```

Login:

- user: `admin`
- password: `prom-operator`

Import:

- `observability/grafana/global-slo-dashboard.json`

You now see live burn rate panels.

---

# 5️⃣ Enable Canary Rollouts

Apply rollout:

```bash
kubectl get rollout -n online-shop
```

Deploy new version:

```bash
kubectl argo rollouts set image frontend \
  frontend=dmitryzhuravlev/online-shop-frontend:v2
```

Watch rollout:

```bash
kubectl argo rollouts get rollout frontend --watch
```

---

# 6️⃣ Add Automatic Rollback via AnalysisTemplate

Create AnalysisTemplate:

```yaml
apiVersion: argoproj.io/v1alpha1
kind: AnalysisTemplate
metadata:
  name: slo-analysis
spec:
  metrics:
    - name: burn-rate-fast
      interval: 30s
      successCondition: result < 14
      provider:
        prometheus:
          address: http://monitoring-kube-prometheus-prometheus.monitoring.svc:9090
          query: slo:burn_rate_5m
```

Attach to Rollout:

```yaml
strategy:
  canary:
    analysis:
      templates:
        - templateName: slo-analysis
```

Now:

- If burn rate exceeds threshold → automatic rollback.

---

# 7️⃣ Enable GitOps with ArgoCD

Apply application:

```bash
kubectl apply -f argocd/apps/frontend.yaml
```

ArgoCD will:

- Watch Git
- Sync Helm
- Apply Rollouts automatically

---

# 8️⃣ Enable GitHub PR Release Gate

Workflow:

- `.github/workflows/slo-gate.yaml`

Enable branch protection in GitHub:

- Require status check
- Select: `slo-check`

Now:

- PR to main
- GitHub Action queries Prometheus
- OPA evaluates policy
- If SLO unhealthy → merge blocked

---

# 9️⃣ Generate Traffic (Simulate SLO Violation)

Example k6 script:

```javascript
import http from 'k6/http';

export default function () {
  http.get('http://frontend');
}
```

Run:

```bash
k6 run load.js
```

To simulate errors:

- Inject 5–10% HTTP 500
- Increase RPS

Observe:

- Burn rate increases
- Grafana turns red
- Rollout auto-aborts
- PR gets blocked

---

# 🔟 What You Achieved

| Capability | Component |
|---|---|
| Canary deployments | Argo Rollouts |
| Multi-window burn | Prometheus |
| Error budget tracking | Recording rules |
| Global SLO dashboard | Grafana |
| Policy-as-code | OPA |
| Merge blocking | GitHub Actions |
| GitOps sync | ArgoCD |

---

# 🎯 Interview Positioning

You implemented:

**SLO-driven progressive delivery with multi-window burn rate enforcement, GitOps synchronization, and policy-based release governance.**

This demonstrates:

- Production-grade SRE thinking
- Platform engineering capability
- Advanced DevOps automation
- Release governance architecture

---

# 🔥 Optional Enhancements

- Multi-environment overlays (dev/stage/prod)
- Argo Image Updater
- Error budget remaining calculation rule
- Slack alert integration
- Multi-service SLO aggregation
- Terraform infra bootstrap
- Cost-aware autoscaling

---

# ✅ Final Result

You now have:

- Real online shop
- Real canary
- Real SLO burn tracking
- Real rollback
- Real GitHub enforcement
- Real observability
- Real observability

Real observability
