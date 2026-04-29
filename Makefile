.PHONY: bootstrap deploy break break-spike break-sustained break-clean clean \
  load-config load-baseline load-failure-10 load-failure-50 load-clean \
  load-capture-baseline load-capture-failure-10 load-capture-failure-50 \
  check-project preflight helm-lint render kubeconform-install kube-validate \
  tf-validate tf-plan terraform-init terraform-apply terraform-destroy kubeconfig \
  helm-bootstrap install-ingress install-argocd install-argo-rollouts argocd-root status

# Set this when running:
#   make bootstrap PROJECT_ID=your-gcp-project
PROJECT_ID ?=

# Optional environment overlay selection for local rendering/validation.
# Usage:
#   make render ENV=dev
#   make kube-validate ENV=dev
#   make preflight ENV=dev
#
# NOTE: Default behavior (no CLI ENV=...) remains unchanged.
ENV ?=

# Only enable overlays when ENV is provided on the make command line.
# This avoids accidental behavior changes if a user's shell exports an ENV variable.
ENV_EFFECTIVE :=
ifneq ($(filter command line,$(origin ENV)),)
  ENV_EFFECTIVE := $(strip $(ENV))
endif

ifneq ($(ENV_EFFECTIVE),)
  ifneq ($(filter $(ENV_EFFECTIVE),dev stage prod),$(ENV_EFFECTIVE))
    $(error ENV must be one of: dev stage prod)
  endif
  HELM_TEMPLATE_ARGS := -f environments/$(ENV_EFFECTIVE)/values/platform.yaml
else
  HELM_TEMPLATE_ARGS :=
endif

# Tooling is pinned for deterministic, reproducible preflight checks.
KUBECONFORM_VERSION ?= v0.6.7
KUBECONFORM_K8S_VERSION ?= 1.29.0
KUBECONFORM_BIN := tools\bin\kubeconform.exe
KUBECONFORM_URL := https://github.com/yannh/kubeconform/releases/download/$(KUBECONFORM_VERSION)/kubeconform-windows-amd64.zip
LOAD_NAMESPACE ?= online-shop-dev
K6_SCRIPTS_CONFIGMAP ?= online-shop-k6-scripts

check-project:
	@if "$(PROJECT_ID)"=="" (echo ERROR: PROJECT_ID is required. Example: make bootstrap PROJECT_ID=my-gcp-project & exit /b 1)

# WHY: One local, read-only entrypoint that catches broken Helm/Terraform/YAML before GitOps applies it.
preflight:
	@echo Running preflight checks (safe: no cluster required)
	@$(MAKE) helm-lint
	@$(MAKE) render ENV=$(ENV_EFFECTIVE)
	@$(MAKE) kube-validate ENV=$(ENV_EFFECTIVE)
	@$(MAKE) tf-validate
	@if "$(PROJECT_ID)"=="" (echo SKIP tf-plan: PROJECT_ID not set. Example: make tf-plan PROJECT_ID=my-gcp-project) else ($(MAKE) tf-plan PROJECT_ID=$(PROJECT_ID))

# WHY: Prevents broken Helm releases (bad templates/values/dependencies) from reaching ArgoCD.
helm-lint:
	@echo Linting Helm charts
	helm lint charts/platform/
	helm lint charts/online-shop-service/

# WHY: Produces deterministic rendered manifests for review + schema validation (no cluster required).
render:
	@echo Rendering charts/platform to _rendered_platform.yaml and _rendered_platform.utf8.yaml (UTF-8, no BOM)
	@powershell -NoProfile -Command "$$ErrorActionPreference='Stop'; $$helmTmp=Join-Path $$env:TEMP 'sre-platform-helm'; $$cache=Join-Path $$helmTmp 'cache'; $$repoCfg=Join-Path $$helmTmp 'repositories.yaml'; New-Item -ItemType Directory -Force $$cache | Out-Null; Set-Content -LiteralPath $$repoCfg -Value \"apiVersion: v1`nrepositories: []\" -NoNewline; $$env:HELM_REPOSITORY_CONFIG=$$repoCfg; $$env:HELM_REPOSITORY_CACHE=$$cache; helm dependency build charts/platform --skip-refresh"
	@powershell -NoProfile -Command "$$ErrorActionPreference='Stop'; $$enc=New-Object System.Text.UTF8Encoding($$false); $$nl=[char]10; $$out=helm template platform charts/platform $(HELM_TEMPLATE_ARGS); $$text=($$out -join $$nl); if(-not $$text.EndsWith($$nl)){ $$text+=$$nl }; [System.IO.File]::WriteAllText('_rendered_platform.yaml',$$text,$$enc); [System.IO.File]::WriteAllText('_rendered_platform.utf8.yaml',$$text,$$enc)"

# WHY: Lightweight, pinned schema validator download so validation works consistently across dev machines/CI.
kubeconform-install:
	@if not exist tools\bin (mkdir tools\bin)
	@if exist $(KUBECONFORM_BIN) (echo kubeconform present: $(KUBECONFORM_BIN)) else (powershell -NoProfile -Command "$$ErrorActionPreference='Stop'; $$zip='tools/bin/kubeconform.zip'; $$url='$(KUBECONFORM_URL)'; Invoke-WebRequest -UseBasicParsing -Uri $$url -OutFile $$zip; Expand-Archive -Force -Path $$zip -DestinationPath 'tools/bin'; Remove-Item $$zip -Force")

# WHY: Catches invalid Kubernetes YAML early (API drift, typos, wrong kinds) before cluster apply/ArgoCD sync.
# NOTE: -ignore-missing-schemas allows CRDs (ArgoCD Application, ServiceMonitor, Rollout) to be validated later.
kube-validate: kubeconform-install render
	@echo Validating rendered Helm output + raw manifests with kubeconform (Kubernetes $(KUBECONFORM_K8S_VERSION))
	@$(KUBECONFORM_BIN) -strict -summary -ignore-missing-schemas -kubernetes-version $(KUBECONFORM_K8S_VERSION) _rendered_platform.utf8.yaml
	@$(KUBECONFORM_BIN) -strict -summary -ignore-missing-schemas -kubernetes-version $(KUBECONFORM_K8S_VERSION) argocd/ observability/ingress/
	@$(KUBECONFORM_BIN) -strict -summary -ignore-missing-schemas -kubernetes-version $(KUBECONFORM_K8S_VERSION) k6/k8s/job-baseline.yaml k6/k8s/job-failure-10.yaml k6/k8s/job-failure-50.yaml

# WHY: Prevents Terraform formatting/syntax/provider config errors from breaking deployments and CI.
tf-validate:
	@echo Validating Terraform (fmt/init/validate) in ./terraform
	cd terraform && terraform fmt -check
	cd terraform && terraform init -backend=false -input=false
	cd terraform && terraform validate

# WHY: Dry-run safe infra preview (no refresh) to catch variable wiring and unexpected diffs before apply.
tf-plan: check-project tf-validate
	@echo Creating Terraform plan (dry-run safe: -refresh=false) in ./terraform/tfplan
	cd terraform && terraform plan -refresh=false -input=false -var "project_id=$(PROJECT_ID)" -out=tfplan

terraform-init: check-project
	@echo Initializing Terraform in ./terraform
	cd terraform && terraform init

terraform-apply: terraform-init
	@echo Applying Terraform (GKE + VPC). This may take several minutes.
	cd terraform && terraform apply -auto-approve -var "project_id=$(PROJECT_ID)"

terraform-destroy: terraform-init
	@echo Destroying Terraform-managed infra (GKE + VPC)
	cd terraform && terraform destroy -auto-approve -var "project_id=$(PROJECT_ID)"

kubeconfig: terraform-apply
	@echo Fetching kubeconfig via gcloud (requires gcloud auth)
	@for /f "delims=" %%i in ('cd terraform ^&^& terraform output -raw cluster_name') do @for /f "delims=" %%j in ('cd terraform ^&^& terraform output -raw region') do gcloud container clusters get-credentials %%i --region %%j --project $(PROJECT_ID)

helm-bootstrap:
	@echo Adding Helm repos
	helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx --force-update
	helm repo add argo https://argoproj.github.io/argo-helm --force-update
	helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
	helm repo update

install-ingress: helm-bootstrap
	@echo Installing ingress-nginx
	helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace -f observability/helm/ingress-nginx/values.yaml

install-argocd: helm-bootstrap
	@echo Installing ArgoCD
	helm upgrade --install argo-cd argo/argo-cd -n argocd --create-namespace
	@echo Waiting for ArgoCD server deployment to be ready
	kubectl -n argocd rollout status deploy/argo-cd-argocd-server --timeout=10m

install-argo-rollouts: helm-bootstrap
	@echo Installing Argo Rollouts
	helm upgrade --install argo-rollouts argo/argo-rollouts -n argo-rollouts --create-namespace
	@echo Waiting for Argo Rollouts controller to be ready
	kubectl -n argo-rollouts rollout status deploy/argo-rollouts --timeout=10m

argocd-root:
	@echo Applying ArgoCD root app-of-apps
	kubectl apply -f argocd/apps/root.yaml

bootstrap: kubeconfig install-ingress install-argocd install-argo-rollouts argocd-root
	@echo Bootstrap complete. ArgoCD will reconcile apps in argocd/apps/apps automatically.

deploy: argocd-root
	@echo Deploy requested. ArgoCD automated sync should converge the platform.

break: break-spike

break-spike:
	@echo Deprecated alias: use make load-failure-10
	@$(MAKE) load-failure-10

break-sustained:
	@echo Deprecated alias: use make load-failure-50
	@$(MAKE) load-failure-50

break-clean:
	@echo Deprecated alias: use make load-clean
	@$(MAKE) load-clean

load-config:
	@echo Refreshing k6 scripts ConfigMap in namespace $(LOAD_NAMESPACE)
	kubectl -n $(LOAD_NAMESPACE) create configmap $(K6_SCRIPTS_CONFIGMAP) --from-file=k6/scripts --dry-run=client -o yaml | kubectl apply -f -

load-baseline: load-config
	@echo Running k6 baseline scenario in namespace $(LOAD_NAMESPACE)
	-kubectl -n $(LOAD_NAMESPACE) delete job online-shop-load-baseline --ignore-not-found
	kubectl apply -f k6/k8s/job-baseline.yaml
	@echo Next commands:
	@echo "  kubectl -n $(LOAD_NAMESPACE) get job online-shop-load-baseline -w"
	@echo "  kubectl -n $(LOAD_NAMESPACE) logs -f job/online-shop-load-baseline"
	@echo "  kubectl -n $(LOAD_NAMESPACE) get rollout frontend"
	@echo "  kubectl -n $(LOAD_NAMESPACE) get analysisrun"
	@if "$(RUN_ID)"=="" (echo "  make load-capture-baseline RUN_ID=baseline-YYYYMMDD-HHMM") else (echo "  make load-capture-baseline RUN_ID=$(RUN_ID)")

load-failure-10: load-config
	@echo Running k6 failure-10 scenario in namespace $(LOAD_NAMESPACE)
	-kubectl -n $(LOAD_NAMESPACE) delete job online-shop-load-failure-10 --ignore-not-found
	kubectl apply -f k6/k8s/job-failure-10.yaml
	@echo Next commands:
	@echo "  kubectl -n $(LOAD_NAMESPACE) get job online-shop-load-failure-10 -w"
	@echo "  kubectl -n $(LOAD_NAMESPACE) logs -f job/online-shop-load-failure-10"
	@echo "  kubectl -n $(LOAD_NAMESPACE) get rollout frontend -w"
	@echo "  kubectl -n $(LOAD_NAMESPACE) get analysisrun -w"
	@if "$(RUN_ID)"=="" (echo "  make load-capture-failure-10 RUN_ID=failure-10-YYYYMMDD-HHMM") else (echo "  make load-capture-failure-10 RUN_ID=$(RUN_ID)")

load-failure-50: load-config
	@echo Running k6 failure-50 scenario in namespace $(LOAD_NAMESPACE)
	-kubectl -n $(LOAD_NAMESPACE) delete job online-shop-load-failure-50 --ignore-not-found
	kubectl apply -f k6/k8s/job-failure-50.yaml
	@echo Next commands:
	@echo "  kubectl -n $(LOAD_NAMESPACE) get job online-shop-load-failure-50 -w"
	@echo "  kubectl -n $(LOAD_NAMESPACE) logs -f job/online-shop-load-failure-50"
	@echo "  kubectl -n $(LOAD_NAMESPACE) get rollout frontend -w"
	@echo "  kubectl -n $(LOAD_NAMESPACE) get analysisrun -w"
	@if "$(RUN_ID)"=="" (echo "  make load-capture-failure-50 RUN_ID=failure-50-YYYYMMDD-HHMM") else (echo "  make load-capture-failure-50 RUN_ID=$(RUN_ID)")

load-capture-baseline:
	bash scripts/capture_load_run_evidence.sh baseline online-shop-load-baseline $(RUN_ID)

load-capture-failure-10:
	bash scripts/capture_load_run_evidence.sh failure-10 online-shop-load-failure-10 $(RUN_ID)

load-capture-failure-50:
	bash scripts/capture_load_run_evidence.sh failure-50 online-shop-load-failure-50 $(RUN_ID)

load-clean:
	@echo Cleaning k6 load/failure jobs in namespace $(LOAD_NAMESPACE)
	-kubectl -n $(LOAD_NAMESPACE) delete job online-shop-load-baseline --ignore-not-found
	-kubectl -n $(LOAD_NAMESPACE) delete job online-shop-load-failure-10 --ignore-not-found
	-kubectl -n $(LOAD_NAMESPACE) delete job online-shop-load-failure-50 --ignore-not-found
	-kubectl -n $(LOAD_NAMESPACE) delete configmap $(K6_SCRIPTS_CONFIGMAP) --ignore-not-found

status:
	@echo ArgoCD Applications:
	-kubectl -n argocd get applications
	@echo Online-shop Rollouts:
	-kubectl -n online-shop get rollouts
	@echo Online-shop Pods:
	-kubectl -n online-shop get pods

clean: break-clean
	@echo Removing ArgoCD Applications (app-of-apps + children)
	-kubectl -n argocd delete application online-shop-platform --ignore-not-found
	-kubectl -n argocd delete application online-shop --ignore-not-found
	-kubectl -n argocd delete application monitoring --ignore-not-found
	-kubectl -n argocd delete application observability-ingress --ignore-not-found
	-kubectl -n argocd delete application legacy-online-shop --ignore-not-found
	@echo Uninstalling Helm releases
	-helm uninstall argo-rollouts -n argo-rollouts
	-helm uninstall argo-cd -n argocd
	-helm uninstall ingress-nginx -n ingress-nginx
	@echo Deleting namespaces (best-effort)
	-kubectl delete namespace online-shop --ignore-not-found
	-kubectl delete namespace monitoring --ignore-not-found
	-kubectl delete namespace argo-rollouts --ignore-not-found
	-kubectl delete namespace argocd --ignore-not-found
	-kubectl delete namespace ingress-nginx --ignore-not-found
	@echo Cleaning infra via Terraform
	$(MAKE) terraform-destroy PROJECT_ID=$(PROJECT_ID)
