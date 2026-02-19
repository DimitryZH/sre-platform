.PHONY: bootstrap deploy break break-spike break-sustained break-clean clean terraform-init terraform-apply terraform-destroy kubeconfig helm-bootstrap install-ingress install-argocd install-argo-rollouts argocd-root status

# Set this when running:
#   make bootstrap PROJECT_ID=your-gcp-project
PROJECT_ID ?=

check-project:
	@if "$(PROJECT_ID)"=="" (echo ERROR: PROJECT_ID is required. Example: make bootstrap PROJECT_ID=my-gcp-project & exit /b 1)

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
	helm upgrade --install ingress-nginx ingress-nginx/ingress-nginx -n ingress-nginx --create-namespace

install-argocd: helm-bootstrap
	@echo Installing ArgoCD
	helm upgrade --install argo-cd argo/argo-cd -n argocd --create-namespace
	@echo Waiting for ArgoCD server deployment to be ready
	kubectl -n argocd rollout status deploy/argo-cd-argocd-server --timeout=10m

install-argo-rollouts: helm-bootstrap
	@echo Installing Argo Rollouts
	helm upgrade --install argo-rollouts argo/argo-rollouts -n argo-rollouts --create-namespace
	@echo Waiting for Argo Rollouts controller to be ready
	kubectl -n argo-rollouts rollout status deploy/argo-rollouts-argo-rollouts --timeout=10m

argocd-root:
	@echo Applying ArgoCD root app-of-apps
	kubectl apply -f argocd/apps/root.yaml

bootstrap: kubeconfig install-ingress install-argocd install-argo-rollouts argocd-root
	@echo Bootstrap complete. ArgoCD will reconcile apps in argocd/apps/apps automatically.

deploy: argocd-root
	@echo Deploy requested. ArgoCD automated sync should converge the platform.

break: break-spike

break-spike:
	@echo Running deterministic break scenario: baseline + 5m spike
	kubectl apply -f k6/k8s-base.yaml
	-kubectl -n online-shop-load delete job online-shop-baseline-load --ignore-not-found
	-kubectl -n online-shop-load delete job online-shop-break-spike --ignore-not-found
	kubectl apply -f k6/job-baseline.yaml
	kubectl apply -f k6/job-spike.yaml
	@echo Jobs started in namespace online-shop-load

break-sustained:
	@echo Running deterministic break scenario: baseline + sustained (~70m)
	kubectl apply -f k6/k8s-base.yaml
	-kubectl -n online-shop-load delete job online-shop-baseline-load --ignore-not-found
	-kubectl -n online-shop-load delete job online-shop-break-sustained --ignore-not-found
	kubectl apply -f k6/job-baseline.yaml
	kubectl apply -f k6/job-sustained.yaml
	@echo Jobs started in namespace online-shop-load

break-clean:
	@echo Cleaning break/load jobs
	-kubectl -n online-shop-load delete job online-shop-baseline-load --ignore-not-found
	-kubectl -n online-shop-load delete job online-shop-break-spike --ignore-not-found
	-kubectl -n online-shop-load delete job online-shop-break-sustained --ignore-not-found
	-kubectl delete namespace online-shop-load --ignore-not-found

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
