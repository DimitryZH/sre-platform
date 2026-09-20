"""Offline security core for the staging evidence gateway."""

from .adapters import (
    ArgoCDGitOpsAdapter,
    FrontendLogsAdapter,
    KubernetesEvidenceAdapter,
    PrometheusEvidenceAdapter,
    ProviderBounds,
)
from .core import EvidenceGateway
from .policy import EvidenceError, EvidencePolicy
from .providers import (
    FakeGitOpsProvider,
    FakeKubernetesProvider,
    FakeLogsProvider,
    FakePrometheusProvider,
)

__all__ = [
    "EvidenceError",
    "EvidenceGateway",
    "EvidencePolicy",
    "ArgoCDGitOpsAdapter",
    "FrontendLogsAdapter",
    "FakeGitOpsProvider",
    "FakeKubernetesProvider",
    "FakeLogsProvider",
    "FakePrometheusProvider",
    "KubernetesEvidenceAdapter",
    "PrometheusEvidenceAdapter",
    "ProviderBounds",
]
