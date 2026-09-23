"""Private runtime foundation for the bounded staging evidence gateway.

All runtime dependencies are injected. This package creates no Kubernetes,
network, or cloud clients by itself.
"""

from .gateway import PrivateHTTPApplication, RuntimeGateway, RuntimeResponse, RuntimeTokenConfig, TokenReviewResult
from .assembly import RuntimeConfig, build_runtime, serve
from .state import RuntimeState, RuntimeStateLimits

__all__ = [
    "RuntimeGateway",
    "RuntimeConfig",
    "PrivateHTTPApplication",
    "RuntimeResponse",
    "RuntimeState",
    "RuntimeStateLimits",
    "RuntimeTokenConfig",
    "TokenReviewResult",
    "build_runtime",
    "serve",
]
