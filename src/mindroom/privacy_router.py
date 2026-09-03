"""Fail-closed model, tool and worker routing by privacy and capability policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class RoutingError(RuntimeError):
    """No candidate safely satisfies the complete routing policy."""


class Sensitivity(IntEnum):
    """Ordered request-data sensitivity levels."""

    PUBLIC = 0
    INTERNAL = 1
    CONFIDENTIAL = 2
    RESTRICTED = 3


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    """One explicitly attested model/tool worker route."""

    route_id: str
    kind: str
    location: str
    residency: str
    max_sensitivity: Sensitivity
    capabilities: frozenset[str]
    cost_microunits: int
    isolated: bool
    privileges: frozenset[str]
    healthy: bool = True


@dataclass(frozen=True, slots=True)
class RouteRequest:
    """Complete fail-closed constraints for one execution unit."""

    kind: str
    sensitivity: Sensitivity
    capabilities: frozenset[str]
    allowed_residencies: frozenset[str]
    budget_microunits: int
    require_isolation: bool
    required_privileges: frozenset[str] = frozenset()
    local_only: bool = False


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    """Selected least-cost, least-privilege safe route and evidence."""

    candidate: RouteCandidate
    matched_constraints: tuple[str, ...]


class PrivacyRouter:
    """Select only candidates that satisfy every policy dimension."""

    def __init__(self, candidates: tuple[RouteCandidate, ...]) -> None:
        identifiers = [candidate.route_id for candidate in candidates]
        if len(identifiers) != len(set(identifiers)):
            message = "route candidate identifiers must be unique"
            raise ValueError(message)
        self._candidates = candidates

    def route(self, request: RouteRequest) -> RoutingDecision:
        """Return a deterministic safe route or fail without fallback."""
        if request.budget_microunits < 0 or not request.kind or not request.allowed_residencies:
            message = "routing request kind, residency, or budget is invalid"
            raise RoutingError(message)
        local_only = request.local_only or request.sensitivity is Sensitivity.RESTRICTED
        eligible = tuple(candidate for candidate in self._candidates if self._eligible(candidate, request, local_only))
        if not eligible:
            message = "no route satisfies sensitivity, residency, budget, capability, isolation, and privilege policy"
            raise RoutingError(message)
        selected = min(
            eligible,
            key=lambda candidate: (
                candidate.cost_microunits,
                len(candidate.privileges - request.required_privileges),
                candidate.route_id,
            ),
        )
        return RoutingDecision(
            selected,
            (
                f"sensitivity<={selected.max_sensitivity.name.lower()}",
                f"residency={selected.residency}",
                f"budget={selected.cost_microunits}",
                f"isolated={str(selected.isolated).lower()}",
                "capabilities=" + ",".join(sorted(request.capabilities)),
                "privileges=" + ",".join(sorted(request.required_privileges)),
            ),
        )

    @staticmethod
    def _eligible(candidate: RouteCandidate, request: RouteRequest, local_only: bool) -> bool:
        return (
            candidate.healthy
            and candidate.kind == request.kind
            and candidate.max_sensitivity >= request.sensitivity
            and candidate.residency in request.allowed_residencies
            and candidate.cost_microunits <= request.budget_microunits
            and request.capabilities <= candidate.capabilities
            and (not request.require_isolation or candidate.isolated)
            and request.required_privileges <= candidate.privileges
            and (not local_only or candidate.location == "local")
        )
