"""Production-safe MindRoom model and tool handlers for governed privacy dispatch."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import cast

from pydantic import JsonValue

from mindroom.privacy_dispatch import GovernedDispatcher, PrivacyDispatchStore, RouteHandler
from mindroom.privacy_router import PrivacyRouter, RouteCandidate

ModelExecutor = Callable[[str], Awaitable[str]]
ToolExecutor = Callable[[Mapping[str, JsonValue]], Awaitable[JsonValue]]

_MAX_PROMPT_CHARACTERS = 1_000_000
_MAX_ARGUMENT_KEYS = 1_000


class PrivacyHandlerError(RuntimeError):
    """A live route handler registration or bounded payload is invalid."""


@dataclass(frozen=True, slots=True)
class RegisteredPrivacyHandler:
    """One kind-bound live handler for an attested route candidate."""

    route_id: str
    kind: str
    handler: RouteHandler

    def __post_init__(self) -> None:
        """Reject empty identities and unsupported execution kinds."""
        if not self.route_id.strip() or self.kind not in {"model", "tool"}:
            message = "privacy handler route identity and supported kind are required"
            raise PrivacyHandlerError(message)


def model_route_handler(executor: ModelExecutor, *, timeout_seconds: float = 300.0) -> RouteHandler:
    """Adapt a live MindRoom model executor to the strict governed-dispatch envelope."""
    _validate_timeout(timeout_seconds)

    async def handle(payload: JsonValue) -> JsonValue:
        envelope = _exact_object(payload, required=frozenset({"prompt"}), label="model")
        prompt = envelope["prompt"]
        if not isinstance(prompt, str) or not prompt or len(prompt) > _MAX_PROMPT_CHARACTERS:
            message = "privacy model prompt must be a non-empty bounded string"
            raise PrivacyHandlerError(message)
        result = await asyncio.wait_for(executor(prompt), timeout=timeout_seconds)
        if not isinstance(result, str):
            message = "privacy model executor must return text"
            raise PrivacyHandlerError(message)
        return {"text": result}

    return handle


def tool_route_handler(executor: ToolExecutor, *, timeout_seconds: float = 60.0) -> RouteHandler:
    """Adapt a live MindRoom tool executor to the strict governed-dispatch envelope."""
    _validate_timeout(timeout_seconds)

    async def handle(payload: JsonValue) -> JsonValue:
        envelope = _exact_object(payload, required=frozenset({"arguments"}), label="tool")
        arguments = envelope["arguments"]
        if not isinstance(arguments, dict) or len(arguments) > _MAX_ARGUMENT_KEYS:
            message = "privacy tool arguments must be a bounded JSON object"
            raise PrivacyHandlerError(message)
        return await asyncio.wait_for(executor(cast("Mapping[str, JsonValue]", arguments)), timeout=timeout_seconds)

    return handle


def build_live_privacy_dispatcher(
    *,
    candidates: tuple[RouteCandidate, ...],
    registrations: tuple[RegisteredPrivacyHandler, ...],
    store: PrivacyDispatchStore,
) -> GovernedDispatcher:
    """Build a dispatcher only when every candidate has one kind-matching live handler."""
    candidate_by_id = {candidate.route_id: candidate for candidate in candidates}
    handler_by_id: dict[str, RouteHandler] = {}
    for registration in registrations:
        candidate = candidate_by_id.get(registration.route_id)
        if candidate is None:
            message = "privacy handler registration references an unknown route"
            raise PrivacyHandlerError(message)
        if candidate.kind != registration.kind:
            message = "privacy handler kind does not match its route candidate"
            raise PrivacyHandlerError(message)
        if registration.route_id in handler_by_id:
            message = "privacy route handler registrations must be unique"
            raise PrivacyHandlerError(message)
        handler_by_id[registration.route_id] = registration.handler
    if handler_by_id.keys() != candidate_by_id.keys():
        message = "every privacy route candidate requires an explicit live handler"
        raise PrivacyHandlerError(message)
    return GovernedDispatcher(router=PrivacyRouter(candidates), store=store, handlers=handler_by_id)


def _exact_object(payload: JsonValue, *, required: frozenset[str], label: str) -> dict[str, JsonValue]:
    if not isinstance(payload, dict) or set(payload) != required:
        message = f"privacy {label} payload must contain exactly: {','.join(sorted(required))}"
        raise PrivacyHandlerError(message)
    return cast("dict[str, JsonValue]", payload)


def _validate_timeout(timeout_seconds: float) -> None:
    if timeout_seconds <= 0:
        message = "privacy handler timeout must be positive"
        raise PrivacyHandlerError(message)
