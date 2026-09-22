"""Shared strict projections for the two private source operations."""

from typing import Any

from ..policy import SHA_RE, contains_unsafe_text

SOURCE_STATE_PATH = "/v1/evidence/staging/frontend/state"
SOURCE_REVISION_PATH = "/v1/evidence/staging/frontend/deployment-revision"
SOURCE_STATE_MAX_BYTES = 16 * 1024
SOURCE_REVISION_MAX_BYTES = 4096


def project_deployment_revision(value: Any) -> dict[str, Any]:
    _keys(value, {"application", "revision", "sync_status", "health_status"})
    if value["application"] != "online-shop-stage" or not isinstance(value["revision"], str) or not SHA_RE.fullmatch(value["revision"]):
        raise ValueError()
    return {"application": "online-shop-stage", "revision": value["revision"],
            "sync_status": _text(value["sync_status"], 32), "health_status": _text(value["health_status"], 32)}


def project_frontend_state(value: Any) -> dict[str, Any]:
    _keys(value, {"workload", "rollout", "ingress"})
    workload, rollout, ingress = value["workload"], value["rollout"], value["ingress"]
    _keys(workload, {"name", "namespace", "ready_replicas", "desired_replicas", "available_replicas", "conditions"})
    _keys(rollout, {"name", "namespace", "phase", "current_step", "stable_service", "canary_service", "analysis_runs"})
    _keys(ingress, {"name", "namespace", "class_name", "paths"})
    for item, name in ((workload, "frontend"), (rollout, "frontend"), (ingress, "online-shop-frontend")):
        if item["name"] != name or item["namespace"] != "online-shop-stage":
            raise ValueError()
    if rollout["stable_service"] != "frontend" or rollout["canary_service"] != "frontend-canary" or rollout["analysis_runs"] != []:
        raise ValueError()
    if ingress["paths"] != ["/stage"]:
        raise ValueError()
    conditions = workload["conditions"]
    if not isinstance(conditions, list) or len(conditions) > 8:
        raise ValueError()
    projected_conditions = []
    for condition in conditions:
        _keys(condition, {"type", "status", "reason"})
        projected_conditions.append({"type": _text(condition["type"], 64),
                                     "status": _text(condition["status"], 32), "reason": _text(condition["reason"], 128)})
    return {
        "workload": {"name": "frontend", "namespace": "online-shop-stage",
                     **{key: _integer(workload[key]) for key in ("ready_replicas", "desired_replicas", "available_replicas")},
                     "conditions": projected_conditions},
        "rollout": {"name": "frontend", "namespace": "online-shop-stage", "phase": _text(rollout["phase"], 64),
                    "current_step": _integer(rollout["current_step"]), "stable_service": "frontend",
                    "canary_service": "frontend-canary", "analysis_runs": []},
        "ingress": {"name": "online-shop-frontend", "namespace": "online-shop-stage",
                    "class_name": _text(ingress["class_name"], 64), "paths": ["/stage"]},
    }


def _keys(value: Any, expected: set[str]) -> None:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError()


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str) or len(value) > limit or contains_unsafe_text(value) or any(ord(c) < 32 for c in value):
        raise ValueError()
    return value


def _integer(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1000:
        raise ValueError()
    return value
