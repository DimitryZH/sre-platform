"""Exact-object Kubernetes and Argo CD backends for the private source service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..policy import SHA_RE
from ..providers import ProviderError
from .http_clients import KubernetesObjectHTTPClient

ROLLOUT_API_VERSION = "argoproj.io/v1alpha1"
INGRESS_API_VERSION = "networking.k8s.io/v1"
APPLICATION_API_VERSION = "argoproj.io/v1alpha1"
STAGE_NAMESPACE = "online-shop-stage"
FRONTEND_ROLLOUT = "frontend"
FRONTEND_INGRESS = "online-shop-frontend"
STAGE_APPLICATION = "online-shop-stage"
APPROVED_INGRESS_API_PATH = "/stage(?:/(?!break(?:$|/))|$)(.*)"
MAX_CONDITIONS = 8


@dataclass
class NarrowKubernetesBackend:
    client: KubernetesObjectHTTPClient

    def get_rollout(self, *, namespace: str, name: str) -> dict[str, Any]:
        if namespace != STAGE_NAMESPACE or name != FRONTEND_ROLLOUT:
            raise ProviderError("kubernetes object is outside the approved boundary")
        try:
            return _project_rollout(self.client.get_frontend_rollout())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("kubernetes object is unavailable") from exc

    def get_ingress(self, *, namespace: str, name: str) -> dict[str, Any]:
        if namespace != STAGE_NAMESPACE or name != FRONTEND_INGRESS:
            raise ProviderError("kubernetes object is outside the approved boundary")
        try:
            return _project_ingress(self.client.get_frontend_ingress())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("kubernetes object is unavailable") from exc

    def list_pods(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("pod status evidence is unavailable")

    def list_events(self, **_: Any) -> list[dict[str, Any]]:
        raise ProviderError("event evidence is unavailable")


@dataclass
class NarrowArgoCDBackend:
    client: KubernetesObjectHTTPClient

    def get_application(self, *, name: str) -> dict[str, Any]:
        if name != STAGE_APPLICATION:
            raise ProviderError("argocd application is outside the approved boundary")
        try:
            return _project_application(self.client.get_stage_application())
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError("argocd application is unavailable") from exc


class UnavailableGitOpsBackend:
    def read_file_at_commit(self, **_: Any) -> str:
        raise ProviderError("gitops contents evidence is unavailable")


def _project_rollout(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    _identity(raw, api_version=ROLLOUT_API_VERSION, kind="Rollout", namespace=STAGE_NAMESPACE, name=FRONTEND_ROLLOUT)
    spec = _mapping(raw.get("spec"))
    status = _mapping(raw.get("status"))
    strategy = _mapping(spec.get("strategy"))
    canary = _mapping(strategy.get("canary"))
    if canary.get("stableService") != "frontend" or canary.get("canaryService") != "frontend-canary":
        raise ProviderError("rollout service boundary is malformed")
    conditions = _list(status.get("conditions"))
    if len(conditions) > MAX_CONDITIONS:
        raise ProviderError("rollout conditions exceeded limit")
    projected_conditions = []
    for condition in conditions:
        item = _mapping(condition)
        projected_conditions.append({
            "type": _text(item.get("type"), 64),
            "status": _text(item.get("status"), 32),
            "reason": _text(item.get("reason"), 128),
        })
    return {
        "name": FRONTEND_ROLLOUT,
        "namespace": STAGE_NAMESPACE,
        "ready_replicas": _integer(status.get("readyReplicas")),
        "desired_replicas": _integer(spec.get("replicas")),
        "available_replicas": _integer(status.get("availableReplicas")),
        "conditions": projected_conditions,
        "phase": _text(status.get("phase"), 64),
        "current_step": _integer(status.get("currentStepIndex")),
        "stable_service": "frontend",
        "canary_service": "frontend-canary",
        "analysis_runs": [],
    }


def _project_ingress(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    _identity(raw, api_version=INGRESS_API_VERSION, kind="Ingress", namespace=STAGE_NAMESPACE, name=FRONTEND_INGRESS)
    spec = _mapping(raw.get("spec"))
    if spec.get("ingressClassName") != "nginx":
        raise ProviderError("ingress class is outside the approved boundary")
    rules = _list(spec.get("rules"))
    if len(rules) != 1:
        raise ProviderError("ingress rule boundary is ambiguous")
    rule = _mapping(rules[0])
    if set(rule) != {"http"}:
        raise ProviderError("ingress host boundary is ambiguous")
    paths = _list(_mapping(rule.get("http")).get("paths"))
    if len(paths) != 1:
        raise ProviderError("ingress path boundary is ambiguous")
    path = _mapping(paths[0])
    backend = _mapping(path.get("backend"))
    service = _mapping(backend.get("service"))
    port = _mapping(service.get("port"))
    if (
        path.get("path") != APPROVED_INGRESS_API_PATH
        or path.get("pathType") != "ImplementationSpecific"
        or service.get("name") != "frontend"
        or port != {"name": "http"}
    ):
        raise ProviderError("ingress path is outside the approved boundary")
    return {
        "name": FRONTEND_INGRESS,
        "namespace": STAGE_NAMESPACE,
        "class_name": "nginx",
        "paths": ["/stage"],
    }


def _project_application(value: Any) -> dict[str, Any]:
    raw = _mapping(value)
    _identity(raw, api_version=APPLICATION_API_VERSION, kind="Application", namespace="argocd", name=STAGE_APPLICATION)
    status = _mapping(raw.get("status"))
    sync = _mapping(status.get("sync"))
    health = _mapping(status.get("health"))
    revision = sync.get("revision")
    if not isinstance(revision, str) or not SHA_RE.fullmatch(revision):
        raise ProviderError("argocd revision is not immutable")
    return {
        "metadata": {"name": STAGE_APPLICATION},
        "status": {
            "sync": {"revision": revision, "status": _text(sync.get("status"), 32)},
            "health": {"status": _text(health.get("status"), 32)},
        },
    }


def _identity(raw: dict[str, Any], *, api_version: str, kind: str, namespace: str, name: str) -> None:
    metadata = _mapping(raw.get("metadata"))
    if (
        raw.get("apiVersion") != api_version
        or raw.get("kind") != kind
        or metadata.get("namespace") != namespace
        or metadata.get("name") != name
    ):
        raise ProviderError("backend object is outside the approved boundary")


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProviderError("backend object is malformed")
    return value


def _list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise ProviderError("backend collection is malformed")
    return value


def _integer(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1000:
        raise ProviderError("backend integer is malformed")
    return value


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or not value or len(value) > limit or any(ord(character) < 32 for character in value):
        raise ProviderError("backend text is malformed")
    return value
