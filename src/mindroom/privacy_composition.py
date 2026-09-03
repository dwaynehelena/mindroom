"""Compose site-configured governed privacy routes with explicit live executors."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from mindroom.privacy_handlers import (
    ModelExecutor,
    PrivacyHandlerError,
    RegisteredPrivacyHandler,
    ToolExecutor,
    build_live_privacy_dispatcher,
    model_route_handler,
    tool_route_handler,
)
from mindroom.privacy_router import RouteCandidate, Sensitivity

if TYPE_CHECKING:
    from collections.abc import Mapping

    from mindroom.config.privacy import PrivacyRoutingConfig
    from mindroom.privacy_dispatch import GovernedDispatcher, PrivacyDispatchStore

_SENSITIVITY = {
    "public": Sensitivity.PUBLIC,
    "internal": Sensitivity.INTERNAL,
    "confidential": Sensitivity.CONFIDENTIAL,
    "restricted": Sensitivity.RESTRICTED,
}


@dataclass(frozen=True, slots=True)
class ConfiguredPrivacyExecutors:
    """Kind-separated live executor registry supplied by production composition."""

    models: Mapping[str, ModelExecutor]
    tools: Mapping[str, ToolExecutor]


def build_configured_privacy_dispatcher(
    *,
    config: PrivacyRoutingConfig,
    executors: ConfiguredPrivacyExecutors,
    store: PrivacyDispatchStore,
) -> GovernedDispatcher:
    """Bind every enabled declared route to one exact kind-matching executor."""
    if not config.enabled:
        message = "privacy routing is disabled"
        raise PrivacyHandlerError(message)
    candidates: list[RouteCandidate] = []
    registrations: list[RegisteredPrivacyHandler] = []
    for route_id, route in config.routes.items():
        candidates.append(
            RouteCandidate(
                route_id=route_id,
                kind=route.kind,
                location=route.location,
                residency=route.residency,
                max_sensitivity=_SENSITIVITY[route.max_sensitivity],
                capabilities=route.capabilities,
                cost_microunits=route.cost_microunits,
                isolated=route.isolated,
                privileges=route.privileges,
                healthy=route.healthy,
            ),
        )
        if route.kind == "model":
            executor = executors.models.get(route.executor)
            if executor is None or route.executor in executors.tools:
                message = "configured privacy model route has no unique kind-matching executor"
                raise PrivacyHandlerError(message)
            handler = model_route_handler(executor, timeout_seconds=route.timeout_seconds)
        else:
            executor = executors.tools.get(route.executor)
            if executor is None or route.executor in executors.models:
                message = "configured privacy tool route has no unique kind-matching executor"
                raise PrivacyHandlerError(message)
            handler = tool_route_handler(executor, timeout_seconds=route.timeout_seconds)
        registrations.append(RegisteredPrivacyHandler(route_id, route.kind, handler))
    return build_live_privacy_dispatcher(
        candidates=tuple(candidates),
        registrations=tuple(registrations),
        store=store,
    )
