.PHONY: deploy break spike sustained load-clean

# NOTE:
# This Makefile is intentionally minimal for the current slice.
# `bootstrap/clean` will be wired once Terraform + ArgoCD app-of-apps are finalized.

deploy:
	@echo Deploying online-shop chart into namespace online-shop
	kubectl create namespace online-shop --dry-run=client -o yaml | kubectl apply -f -
	helm dependency build charts/platform
	helm upgrade --install online-shop charts/platform -n online-shop

break: spike

# 5m spike: should raise burn_rate_5m but NOT burn_rate_1h (with baseline load running)
spike:
	@echo Applying k6 baseline + spike jobs
	kubectl apply -f k6/k8s-job.yaml
	@echo Triggered jobs in namespace online-shop-load

# Sustained violation: intended to raise burn_rate_1h; run time is ~70m
sustained:
	@echo Applying k6 baseline + sustained jobs
	kubectl apply -f k6/k8s-job.yaml
	@echo Triggered jobs in namespace online-shop-load

load-clean:
	@echo Cleaning k6 load namespace
	kubectl delete namespace online-shop-load --ignore-not-found

