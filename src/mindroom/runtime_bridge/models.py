"""Stable, transport-neutral models for external runtime bridging."""
# ruff: noqa: ANN401, D105, EM101, EM102, PLR0911, TC003, TRY003

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

CONTRACT_VERSION = "mindroom.runtime-bridge.ndjson.v1"
MAX_ID_BYTES = 512
MAX_TEXT_BYTES = 256 * 1024
MAX_STATE_BYTES = 256 * 1024
_INSTANCE_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,61}[a-z0-9])?$")


class RuntimeName(StrEnum):
    """Named runtimes supported by the bridge contract."""

    OPENCLAW = "openclaw"
    HERMES = "hermes"


class EventOrigin(StrEnum):
    """Origin asserted at MindRoom's future trusted ingress boundary."""

    HUMAN = "human"
    RUNTIME = "runtime"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Stable internal identity of one configured external runtime instance."""

    runtime: RuntimeName
    instance: str

    def __post_init__(self) -> None:
        if not _INSTANCE_SLUG.fullmatch(self.instance):
            raise ValueError("runtime instance must be a 1-63 character lowercase identity slug")

    @property
    def key(self) -> str:
        """Return the persistent internal identity key (not a Matrix identity)."""
        return f"{self.runtime.value}:{self.instance}"


@dataclass(frozen=True, slots=True)
class ConversationScope:
    """Canonical future Matrix room/thread scope; currently an internal value only."""

    room_id: str
    thread_id: str | None = None

    def __post_init__(self) -> None:
        _validate_identifier("room_id", self.room_id)
        if self.thread_id is not None:
            _validate_identifier("thread_id", self.thread_id)


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    """One trusted human-origin request for an external runtime."""

    source_event_id: str
    origin: EventOrigin
    scope: ConversationScope
    session_id: str
    text: str
    state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_identifier("source_event_id", self.source_event_id)
        _validate_identifier("session_id", self.session_id)
        if self.origin is not EventOrigin.HUMAN:
            raise ValueError("runtime bridge accepts only strict human-origin events")
        if not isinstance(self.text, str) or len(self.text.encode()) > MAX_TEXT_BYTES:
            raise ValueError("runtime request text exceeds the byte limit")
        state = dict(self.state)
        _validate_json_object("runtime request state", state, MAX_STATE_BYTES)
        object.__setattr__(self, "state", MappingProxyType(state))


@dataclass(frozen=True, slots=True)
class RuntimeResult:
    """Final runtime text and normalized state; not a delivered Matrix response."""

    text: str
    state: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or len(self.text.encode()) > MAX_TEXT_BYTES:
            raise ValueError("runtime response text exceeds the byte limit")
        state = dict(self.state)
        _validate_json_object("runtime response state", state, MAX_STATE_BYTES)
        object.__setattr__(self, "state", MappingProxyType(state))


def normalized_json_bytes(value: object) -> bytes:
    """Serialize strict JSON deterministically, rejecting nonfinite floats."""
    try:
        return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode()
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError("value must be finite, normalized JSON") from exc


def _validate_json_object(label: str, value: dict[str, Any], byte_limit: int) -> None:
    if not _is_normalized_json(value):
        raise ValueError(f"{label} must be normalized JSON with finite numbers")
    if len(normalized_json_bytes(value)) > byte_limit:
        raise ValueError(f"{label} exceeds the byte limit")


def _is_normalized_json(value: Any, *, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or isinstance(value, bool | str):
        return True
    if isinstance(value, int) and not isinstance(value, bool):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_normalized_json(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_normalized_json(item, depth=depth + 1) for key, item in value.items())
    return False


def _validate_identifier(label: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    if len(value.encode()) > MAX_ID_BYTES:
        raise ValueError(f"{label} exceeds the byte limit")
