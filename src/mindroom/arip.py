"""Fixture-only Agent Runtime Interchange Protocol (ARIP) v1 primitives.

This module has no transport or execution consumer.  Its redacted digests are
preview-integrity checks only and MUST NOT be treated as execution authorization.
"""

# Relationship failures intentionally carry specific local validation messages.
# ruff: noqa: C901, D102, EM101, EM102, TRY003, TRY004

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import Enum
from threading import Lock
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from mindroom.redaction import redact_sensitive_data

ARIP_SCHEMA_VERSION = "arip/1"
type Sha256Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
type EventId = Annotated[str, Field(min_length=1, max_length=256)]
type ActorId = Annotated[str, Field(min_length=1, max_length=512)]
_JSON_INT_MIN = -(2**63)
_JSON_INT_MAX = 2**63 - 1


class AripValidationError(ValueError):
    """An ARIP relationship, time, decision, or replay invariant was violated."""


class AripMode(str, Enum):
    """Fixture settings only; neither value activates a consumer."""

    OFF = "off"
    SHADOW = "shadow"


class AripSettings(BaseModel):
    """Fixture-construction settings, not runtime configuration."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    mode: AripMode = AripMode.OFF
    execution_enabled: Literal[False] = False


class _AripModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _FrozenDict(dict[str, JsonValue]):
    """Serialization-compatible immutable JSON object snapshot."""

    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ARIP JSON snapshots are immutable")

    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _immutable


class _FrozenList(list[JsonValue]):
    """Serialization-compatible immutable JSON array snapshot."""

    def _immutable(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ARIP JSON snapshots are immutable")

    __setitem__ = __delitem__ = __iadd__ = __imul__ = append = clear = extend = insert = pop = remove = reverse = sort = _immutable


def _validate_json_domain(value: object, *, path: str = "$") -> JsonValue:
    """Copy strict JSON into an immutable snapshot; floats are excluded by design."""
    if value is None or isinstance(value, bool):
        return value
    if type(value) is int:
        if not _JSON_INT_MIN <= value <= _JSON_INT_MAX:
            raise ValueError(f"{path} integer is outside signed 64-bit JSON domain")
        return value
    if isinstance(value, str):
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError(f"{path} contains an unpaired Unicode surrogate")
        return value
    if type(value) in (list, _FrozenList):
        return _FrozenList(_validate_json_domain(item, path=f"{path}[{index}]") for index, item in enumerate(value))
    if type(value) in (dict, _FrozenDict):
        snapshot: dict[str, JsonValue] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError(f"{path} contains a non-string object key")
            snapshot[key] = _validate_json_domain(item, path=f"{path}.{key}")
        return _FrozenDict(snapshot)
    if isinstance(value, float):
        raise ValueError(f"{path} contains a float; ARIP v1 hash inputs forbid all floats")
    raise ValueError(f"{path} is not a JSON value (sets, tuples, and custom containers are forbidden)")


def _plain_json(value: JsonValue) -> JsonValue:
    """Convert immutable snapshots to exact built-in JSON containers."""
    if isinstance(value, dict):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_plain_json(item) for item in value]
    return value


class ToolCall(_AripModel):
    """A redacted display preview; its digest never authorizes execution."""

    event_type: Literal["tool.call.requested"] = "tool.call.requested"
    tool_name: Annotated[str, Field(min_length=1, max_length=256)]
    arguments: JsonValue
    redacted_preview_digest: Sha256Digest

    @field_validator("arguments", mode="before")
    @classmethod
    def snapshot_redacted_arguments(cls, value: Any) -> JsonValue:  # noqa: ANN401
        """Reject non-JSON before redaction, redact, then retain an immutable snapshot."""
        validated = _validate_json_domain(value)
        redacted = redact_sensitive_data(_plain_json(validated))
        return _validate_json_domain(redacted)

    @model_validator(mode="after")
    def validate_preview_digest(self) -> ToolCall:
        expected = redacted_preview_digest(self.tool_name, self.arguments, redact=False)
        if self.redacted_preview_digest != expected:
            raise ValueError("tool-call redacted_preview_digest does not match the redacted preview")
        # Pydantic normalizes the pre-validator's dict/list subclasses while
        # constructing JsonValue. Replace that private copy with the immutable
        # authoritative snapshot only after all field validation has completed.
        object.__setattr__(self, "arguments", _validate_json_domain(self.arguments))
        return self


class ApprovalRequest(_AripModel):
    """A time-bounded fixture request referring to one tool-event preview."""

    event_type: Literal["approval.requested"] = "approval.requested"
    approval_id: EventId
    tool_call_event_id: EventId
    redacted_preview_digest: Sha256Digest
    eligible_actors: Annotated[tuple[ActorId, ...], Field(min_length=1, max_length=1000)]
    expires_at: datetime

    @field_validator("eligible_actors")
    @classmethod
    def actors_must_be_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) != len(set(value)):
            raise ValueError("eligible_actors must not contain duplicates")
        return value

    @field_validator("expires_at")
    @classmethod
    def normalize_expiry(cls, value: datetime) -> datetime:
        return _utc(value, "expires_at")


class ApprovalDecision(_AripModel):
    """An authored fixture decision referring to one request preview."""

    event_type: Literal["approval.decided"] = "approval.decided"
    approval_id: EventId
    redacted_preview_digest: Sha256Digest
    actor_id: ActorId
    decision: Literal["approved", "denied"]
    decided_at: datetime
    reason: Annotated[str, Field(max_length=2000)] | None = None

    @field_validator("decided_at")
    @classmethod
    def normalize_decided_at(cls, value: datetime) -> datetime:
        return _utc(value, "decided_at")


type AripPayload = Annotated[ToolCall | ApprovalRequest | ApprovalDecision, Field(discriminator="event_type")]


class AripEventEnvelope(_AripModel):
    """Strict, versioned fixture envelope."""

    schema_version: Literal["arip/1"] = ARIP_SCHEMA_VERSION
    event_id: EventId
    occurred_at: datetime
    source: Annotated[str, Field(min_length=1, max_length=256)]
    payload: AripPayload

    @field_validator("occurred_at")
    @classmethod
    def normalize_occurred_at(cls, value: datetime) -> datetime:
        return _utc(value, "occurred_at")

    @model_validator(mode="after")
    def validate_internal_time(self) -> AripEventEnvelope:
        if isinstance(self.payload, ApprovalDecision) and self.payload.decided_at != self.occurred_at:
            raise ValueError("decision decided_at must equal its envelope occurred_at")
        return self


def _utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")
    return value.astimezone(UTC)


def canonical_json(value: BaseModel | JsonValue) -> bytes:
    """Encode the constrained ARIP JSON hash domain deterministically.

    This is deliberately not advertised as RFC 8785/JCS: ARIP v1 permits only
    null, booleans, signed 64-bit integers, Unicode scalar strings, arrays, and
    string-keyed objects.  Floats and non-JSON Python containers are forbidden.
    """
    normalized: object = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    snapshot = _validate_json_domain(normalized)
    return json.dumps(_plain_json(snapshot), ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def sha256_digest(value: BaseModel | JsonValue) -> str:
    """Return lowercase SHA-256 for the constrained canonical JSON bytes."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def redacted_preview_digest(tool_name: str, arguments: JsonValue, *, redact: bool = True) -> str:
    """Hash a redacted display preview; NEVER an executable-operation authorization."""
    validated = _validate_json_domain(arguments)
    safe = redact_sensitive_data(_plain_json(validated)) if redact else _plain_json(validated)
    return sha256_digest({"arguments": safe, "tool_name": tool_name})


class InMemoryReplayGuard:
    """Atomic process-local fixture replay guard; not durable or distributed."""

    def __init__(self) -> None:
        self._digests: dict[str, str] = {}
        self._lock = Lock()

    def check_and_record(self, event: AripEventEnvelope) -> bool:
        """Atomically record a new event, or return false for identical replay."""
        digest = sha256_digest(event)
        with self._lock:
            previous = self._digests.get(event.event_id)
            if previous is None:
                self._digests[event.event_id] = digest
                return True
            if previous != digest:
                raise AripValidationError(f"event_id {event.event_id!r} was replayed with different content")
            return False


def validate_authored_chain(
    tool_event: AripEventEnvelope,
    request_event: AripEventEnvelope,
    decision_event: AripEventEnvelope,
) -> None:
    """Validate historical authored relationships without asserting current validity."""
    tool, request, decision = tool_event.payload, request_event.payload, decision_event.payload
    if not isinstance(tool, ToolCall) or not isinstance(request, ApprovalRequest) or not isinstance(decision, ApprovalDecision):
        raise AripValidationError("events must be ordered tool call, approval request, approval decision")
    if len({tool_event.event_id, request_event.event_id, decision_event.event_id}) != 3:
        raise AripValidationError("chain event IDs must be distinct")
    if request.tool_call_event_id != tool_event.event_id:
        raise AripValidationError("approval request references a different tool_call_event_id")
    if decision.approval_id != request.approval_id:
        raise AripValidationError("approval decision references a different approval_id")
    if not (tool.redacted_preview_digest == request.redacted_preview_digest == decision.redacted_preview_digest):
        raise AripValidationError("redacted preview digest does not bind the complete chain")
    if not (tool_event.occurred_at <= request_event.occurred_at <= decision_event.occurred_at):
        raise AripValidationError("chain occurred_at timestamps are out of order")
    if request.expires_at < request_event.occurred_at:
        raise AripValidationError("approval request expires before it was authored")
    if decision.decided_at > request.expires_at:
        raise AripValidationError("approval decision was authored after expiry")
    if decision.actor_id not in request.eligible_actors:
        raise AripValidationError("approval decision actor is not eligible")


def validate_live_approval(
    tool_event: AripEventEnvelope,
    request_event: AripEventEnvelope,
    decision_event: AripEventEnvelope,
    *,
    observed_at: datetime,
) -> None:
    """Validate a currently usable fixture approval at a trusted observation time."""
    validate_authored_chain(tool_event, request_event, decision_event)
    observed = _utc(observed_at, "observed_at")
    request, decision = request_event.payload, decision_event.payload
    assert isinstance(request, ApprovalRequest)
    assert isinstance(decision, ApprovalDecision)
    if observed < decision_event.occurred_at:
        raise AripValidationError("observed_at precedes the approval decision")
    if observed > request.expires_at:
        raise AripValidationError("approval is expired at observed_at")
    if decision.decision != "approved":
        raise AripValidationError("denied decision cannot authorize an operation")
