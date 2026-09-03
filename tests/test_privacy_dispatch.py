"""Tests for policy-bound privacy dispatch without unsafe fallback."""

# ruff: noqa: ANN001, ANN201, ANN202, D103, EM101, TRY003

from __future__ import annotations

import pytest
import pytest_asyncio

from mindroom.privacy_dispatch import GovernedDispatcher, PrivacyDispatchError, PrivacyDispatchStore
from mindroom.privacy_router import PrivacyRouter, RouteCandidate, RouteRequest, Sensitivity

pytestmark = pytest.mark.asyncio


def _candidate(route_id, *, location, cost):
    return RouteCandidate(
        route_id,
        "model",
        location,
        "AU",
        Sensitivity.RESTRICTED,
        frozenset({"reasoning"}),
        cost,
        True,
        frozenset({"read"}),
    )


def _policy(sensitivity=Sensitivity.CONFIDENTIAL):
    return RouteRequest(
        "model",
        sensitivity,
        frozenset({"reasoning"}),
        frozenset({"AU"}),
        100,
        True,
        frozenset({"read"}),
    )


@pytest_asyncio.fixture
async def store(tmp_path):
    value = PrivacyDispatchStore(tmp_path / "dispatch.db")
    await value.open()
    yield value
    await value.close()


async def test_dispatch_binds_exact_request_to_selected_route(store) -> None:
    calls = []

    async def local(payload):
        calls.append(payload)
        return {"answer": 42}

    dispatcher = GovernedDispatcher(
        router=PrivacyRouter((_candidate("local", location="local", cost=10),)),
        store=store,
        handlers={"local": local},
    )
    result, receipt = await dispatcher.dispatch(request_id="request-1", policy=_policy(), payload={"question": "x"})
    assert result == {"answer": 42}
    assert receipt.route_id == "local"
    assert calls == [{"question": "x"}]
    assert (await store.status("request-1"))[0] == "completed"
    with pytest.raises(PrivacyDispatchError, match="equivocation"):
        await dispatcher.dispatch(request_id="request-1", policy=_policy(), payload={"question": "changed"})


async def test_selected_handler_failure_never_falls_back(store) -> None:
    calls = []

    async def selected(_payload):
        calls.append("selected")
        raise RuntimeError("secret handler failure")

    async def fallback(_payload):
        calls.append("fallback")
        return {"unsafe": True}

    dispatcher = GovernedDispatcher(
        router=PrivacyRouter(
            (
                _candidate("selected", location="local", cost=1),
                _candidate("fallback", location="local", cost=2),
            ),
        ),
        store=store,
        handlers={"selected": selected, "fallback": fallback},
    )
    with pytest.raises(RuntimeError, match="secret"):
        await dispatcher.dispatch(request_id="request-1", policy=_policy(), payload={"private": True})
    assert calls == ["selected"]
    assert await store.status("request-1") == ("failed", None, "builtins.RuntimeError")


async def test_restricted_request_executes_only_local_handler(store) -> None:
    async def execute(_payload):
        return {"ok": True}

    dispatcher = GovernedDispatcher(
        router=PrivacyRouter(
            (
                _candidate("cloud", location="cloud", cost=1),
                _candidate("local", location="local", cost=10),
            ),
        ),
        store=store,
        handlers={"cloud": execute, "local": execute},
    )
    _result, receipt = await dispatcher.dispatch(
        request_id="request-1",
        policy=_policy(Sensitivity.RESTRICTED),
        payload={"private": True},
    )
    assert receipt.route_id == "local"


async def test_interrupted_execution_is_quarantined_not_replayed(store) -> None:
    await store.reserve(request_id="request-1", request_digest="digest", route_id="local", constraints=())
    await store.begin("request-1")
    assert await store.recover_uncertain() == 1
    assert (await store.status("request-1"))[0] == "uncertain"
    with pytest.raises(PrivacyDispatchError, match="not replayable"):
        await store.reserve(request_id="request-1", request_digest="digest", route_id="local", constraints=())
