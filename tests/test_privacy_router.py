"""Tests for privacy, residency, budget, capability and privilege routing."""

# ruff: noqa: ANN003, D103

from __future__ import annotations

import pytest

from mindroom.privacy_router import PrivacyRouter, RouteCandidate, RouteRequest, RoutingError, Sensitivity


def _candidate(
    route_id: str,
    *,
    location: str,
    residency: str = "AU",
    sensitivity: Sensitivity = Sensitivity.RESTRICTED,
    cost: int = 10,
    isolated: bool = True,
    privileges: frozenset[str] = frozenset({"read"}),
    healthy: bool = True,
) -> RouteCandidate:
    return RouteCandidate(
        route_id=route_id,
        kind="model",
        location=location,
        residency=residency,
        max_sensitivity=sensitivity,
        capabilities=frozenset({"text", "reasoning"}),
        cost_microunits=cost,
        isolated=isolated,
        privileges=privileges,
        healthy=healthy,
    )


def _request(**overrides) -> RouteRequest:
    values = {
        "kind": "model",
        "sensitivity": Sensitivity.CONFIDENTIAL,
        "capabilities": frozenset({"reasoning"}),
        "allowed_residencies": frozenset({"AU"}),
        "budget_microunits": 100,
        "require_isolation": True,
        "required_privileges": frozenset({"read"}),
    }
    values.update(overrides)
    return RouteRequest(**values)


def test_selects_lowest_cost_then_least_privilege_candidate() -> None:
    router = PrivacyRouter(
        (
            _candidate("broad", location="cloud", cost=5, privileges=frozenset({"read", "write"})),
            _candidate("least", location="cloud", cost=5),
            _candidate("expensive", location="local", cost=20),
        ),
    )
    assert router.route(_request()).candidate.route_id == "least"


def test_restricted_data_is_implicitly_local_only() -> None:
    router = PrivacyRouter((_candidate("cloud", location="cloud", cost=1), _candidate("local", location="local")))
    assert router.route(_request(sensitivity=Sensitivity.RESTRICTED)).candidate.route_id == "local"


@pytest.mark.parametrize(
    ("candidate", "route_request"),
    [
        (_candidate("wrong-residency", location="local", residency="US"), _request()),
        (_candidate("over-budget", location="local", cost=101), _request()),
        (_candidate("not-isolated", location="local", isolated=False), _request()),
        (_candidate("unhealthy", location="local", healthy=False), _request()),
        (_candidate("under-classified", location="local", sensitivity=Sensitivity.INTERNAL), _request()),
        (_candidate("overprivileged-only", location="local", privileges=frozenset()), _request()),
    ],
)
def test_each_unsatisfied_policy_dimension_fails_closed(
    candidate: RouteCandidate,
    route_request: RouteRequest,
) -> None:
    with pytest.raises(RoutingError, match="no route satisfies"):
        PrivacyRouter((candidate,)).route(route_request)


def test_no_unsafe_fallback_to_ineligible_candidate() -> None:
    cloud = _candidate("cloud", location="cloud", cost=1)
    with pytest.raises(RoutingError, match="no route satisfies"):
        PrivacyRouter((cloud,)).route(_request(local_only=True))
