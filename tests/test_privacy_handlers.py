"""Tests for production MindRoom handlers behind governed privacy routing."""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import pytest_asyncio

from mindroom.privacy_dispatch import PrivacyDispatchStore
from mindroom.privacy_handlers import (
    PrivacyHandlerError,
    RegisteredPrivacyHandler,
    build_live_privacy_dispatcher,
    model_route_handler,
    tool_route_handler,
)
from mindroom.privacy_router import RouteCandidate, RouteRequest, Sensitivity

pytestmark = pytest.mark.asyncio


def _candidate(route_id: str, kind: str) -> RouteCandidate:
    return RouteCandidate(
        route_id=route_id,
        kind=kind,
        location="local",
        residency="AU",
        max_sensitivity=Sensitivity.RESTRICTED,
        capabilities=frozenset({"reasoning"}),
        cost_microunits=1,
        isolated=True,
        privileges=frozenset({"read"}),
    )


def _policy(kind: str) -> RouteRequest:
    return RouteRequest(
        kind=kind,
        sensitivity=Sensitivity.RESTRICTED,
        capabilities=frozenset({"reasoning"}),
        allowed_residencies=frozenset({"AU"}),
        budget_microunits=10,
        require_isolation=True,
        required_privileges=frozenset({"read"}),
    )


@pytest_asyncio.fixture
async def store(tmp_path):
    value = PrivacyDispatchStore(tmp_path / "privacy.db")
    await value.open()
    yield value
    await value.close()


async def test_live_model_handler_dispatches_exact_bounded_envelope(store) -> None:
    prompts: list[str] = []

    async def execute(prompt: str) -> str:
        prompts.append(prompt)
        return "local answer"

    candidate = _candidate("local-model", "model")
    dispatcher = build_live_privacy_dispatcher(
        candidates=(candidate,),
        registrations=(RegisteredPrivacyHandler(candidate.route_id, "model", model_route_handler(execute)),),
        store=store,
    )
    result, receipt = await dispatcher.dispatch(
        request_id="model-1",
        policy=_policy("model"),
        payload={"prompt": "private question"},
    )
    assert result == {"text": "local answer"}
    assert receipt.route_id == "local-model"
    assert prompts == ["private question"]


async def test_live_tool_handler_receives_only_arguments(store) -> None:
    calls = []

    async def execute(arguments):
        calls.append(dict(arguments))
        return {"ok": True}

    candidate = _candidate("isolated-tool", "tool")
    dispatcher = build_live_privacy_dispatcher(
        candidates=(candidate,),
        registrations=(RegisteredPrivacyHandler(candidate.route_id, "tool", tool_route_handler(execute)),),
        store=store,
    )
    result, _receipt = await dispatcher.dispatch(
        request_id="tool-1",
        policy=_policy("tool"),
        payload={"arguments": {"query": "value"}},
    )
    assert result == {"ok": True}
    assert calls == [{"query": "value"}]


async def test_handlers_reject_extra_fields_before_executor(store) -> None:
    called = False

    async def execute(_prompt: str) -> str:
        nonlocal called
        called = True
        return "unsafe"

    candidate = _candidate("local-model", "model")
    dispatcher = build_live_privacy_dispatcher(
        candidates=(candidate,),
        registrations=(RegisteredPrivacyHandler(candidate.route_id, "model", model_route_handler(execute)),),
        store=store,
    )
    with pytest.raises(PrivacyHandlerError, match="exactly"):
        await dispatcher.dispatch(
            request_id="model-1",
            policy=_policy("model"),
            payload={"prompt": "x", "route_override": "cloud"},
        )
    assert called is False


async def test_handler_timeout_is_failed_without_fallback(store) -> None:
    fallback_called = False

    async def slow(_prompt: str) -> str:
        await asyncio.sleep(1)
        return "late"

    async def fallback(_prompt: str) -> str:
        nonlocal fallback_called
        fallback_called = True
        return "unsafe"

    selected = _candidate("selected", "model")
    other = replace(_candidate("fallback", "model"), cost_microunits=2)
    dispatcher = build_live_privacy_dispatcher(
        candidates=(selected, other),
        registrations=(
            RegisteredPrivacyHandler("selected", "model", model_route_handler(slow, timeout_seconds=0.001)),
            RegisteredPrivacyHandler("fallback", "model", model_route_handler(fallback)),
        ),
        store=store,
    )
    with pytest.raises(TimeoutError):
        await dispatcher.dispatch(request_id="model-1", policy=_policy("model"), payload={"prompt": "x"})
    assert fallback_called is False


@pytest.mark.parametrize("missing_or_wrong", ["missing", "wrong-kind", "unknown", "duplicate"])
async def test_registration_fails_closed(missing_or_wrong, store) -> None:
    async def execute(_value):
        return "ok"

    candidate = _candidate("model", "model")
    handler = model_route_handler(execute)
    registrations = {
        "missing": (),
        "wrong-kind": (RegisteredPrivacyHandler("model", "tool", handler),),
        "unknown": (RegisteredPrivacyHandler("unknown", "model", handler),),
        "duplicate": (
            RegisteredPrivacyHandler("model", "model", handler),
            RegisteredPrivacyHandler("model", "model", handler),
        ),
    }[missing_or_wrong]
    with pytest.raises(PrivacyHandlerError):
        build_live_privacy_dispatcher(candidates=(candidate,), registrations=registrations, store=store)
