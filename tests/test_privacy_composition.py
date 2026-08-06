"""Tests for site-configured governed privacy dispatcher composition."""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import pytest
import pytest_asyncio
from pydantic import ValidationError

from mindroom.config.main import Config
from mindroom.config.privacy import PrivacyRouteConfig, PrivacyRoutingConfig
from mindroom.privacy_composition import ConfiguredPrivacyExecutors, build_configured_privacy_dispatcher
from mindroom.privacy_dispatch import PrivacyDispatchStore
from mindroom.privacy_handlers import PrivacyHandlerError
from mindroom.privacy_router import RouteRequest, Sensitivity

pytestmark = pytest.mark.asyncio


def _route(kind: str, executor: str) -> PrivacyRouteConfig:
    return PrivacyRouteConfig(
        kind=kind,
        executor=executor,
        location="local",
        residency="AU",
        max_sensitivity="restricted",
        capabilities=frozenset({"reasoning"}),
        cost_microunits=1,
        isolated=True,
        privileges=frozenset({"read"}),
    )


def _request(kind: str) -> RouteRequest:
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


async def test_root_config_is_disabled_by_default_and_loads_strict_routes() -> None:
    assert Config().privacy_routing.enabled is False
    config = Config.model_validate(
        {
            "privacy_routing": {
                "enabled": True,
                "routes": {
                    "local-model": {
                        "kind": "model",
                        "executor": "private-model",
                        "location": "local",
                        "residency": "AU",
                        "max_sensitivity": "restricted",
                        "capabilities": ["reasoning"],
                        "cost_microunits": 1,
                        "isolated": True,
                    },
                },
            },
        },
    )
    assert config.privacy_routing.routes["local-model"].executor == "private-model"


@pytest.mark.parametrize(
    "value",
    [
        {"enabled": True, "routes": {}},
        {"routes": {"bad route": {}}},
        {"routes": {"route": {"kind": "model", "executor": "x", "unexpected": True}}},
    ],
)
async def test_route_config_rejects_empty_invalid_and_extra_fields(value) -> None:
    with pytest.raises(ValidationError):
        PrivacyRoutingConfig.model_validate(value)


async def test_configured_model_and_tool_routes_dispatch_exact_executors(store) -> None:
    calls = []

    async def model(prompt: str) -> str:
        calls.append(("model", prompt))
        return "private answer"

    async def tool(arguments):
        calls.append(("tool", dict(arguments)))
        return {"ok": True}

    config = PrivacyRoutingConfig(
        enabled=True,
        routes={"model": _route("model", "local-model"), "tool": _route("tool", "isolated-tool")},
    )
    dispatcher = build_configured_privacy_dispatcher(
        config=config,
        executors=ConfiguredPrivacyExecutors(models={"local-model": model}, tools={"isolated-tool": tool}),
        store=store,
    )
    model_result, _ = await dispatcher.dispatch(
        request_id="model-1",
        policy=_request("model"),
        payload={"prompt": "private"},
    )
    tool_result, _ = await dispatcher.dispatch(
        request_id="tool-1",
        policy=_request("tool"),
        payload={"arguments": {"query": "value"}},
    )
    assert model_result == {"text": "private answer"}
    assert tool_result == {"ok": True}
    assert calls == [("model", "private"), ("tool", {"query": "value"})]


@pytest.mark.parametrize("failure", ["disabled", "missing", "wrong-kind", "ambiguous"])
async def test_composition_fails_closed_for_unusable_executor_binding(store, failure: str) -> None:
    async def model(_prompt: str) -> str:
        return "answer"

    async def tool(_arguments):
        return {"ok": True}

    config = PrivacyRoutingConfig(enabled=failure != "disabled", routes={"route": _route("model", "executor")})
    executors = {
        "disabled": ConfiguredPrivacyExecutors(models={"executor": model}, tools={}),
        "missing": ConfiguredPrivacyExecutors(models={}, tools={}),
        "wrong-kind": ConfiguredPrivacyExecutors(models={}, tools={"executor": tool}),
        "ambiguous": ConfiguredPrivacyExecutors(models={"executor": model}, tools={"executor": tool}),
    }[failure]
    with pytest.raises(PrivacyHandlerError):
        build_configured_privacy_dispatcher(config=config, executors=executors, store=store)
