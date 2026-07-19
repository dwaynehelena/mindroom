"""Security-focused tests for the fixture-only ARIP v1 foundation."""

# Test names describe intent; deliberate invalid naive timestamps exercise rejection.
# ruff: noqa: D103, DTZ001

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from mindroom.arip import (
    ApprovalRequest,
    AripEventEnvelope,
    AripMode,
    AripSettings,
    AripValidationError,
    InMemoryReplayGuard,
    ToolCall,
    canonical_json,
    redacted_preview_digest,
    sha256_digest,
    validate_authored_chain,
    validate_live_approval,
)
from mindroom.redaction import REDACTED

_FIXTURES = Path(__file__).parent / "fixtures" / "arip" / "v1"
_PREVIEW_DIGEST = "553ea14bdbe00760ae379ac921ede32b0507bc2443de8eb0a1c9329d817cba23"


def _load(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def _raw_valid() -> dict[str, dict[str, object]]:
    return {path.stem: _load(path) for path in sorted((_FIXTURES / "valid").glob("*.json"))}


def _valid_events() -> dict[str, AripEventEnvelope]:
    return {name: AripEventEnvelope.model_validate(value) for name, value in _raw_valid().items()}


def _patched_event(name: str, patch: dict[str, object]) -> AripEventEnvelope:
    raw = _raw_valid()[name]
    for key, value in patch.items():
        if key == "payload":
            assert isinstance(value, dict)
            assert isinstance(raw["payload"], dict)
            raw["payload"].update(value)
        else:
            raw[key] = value
    return AripEventEnvelope.model_validate(raw)


def test_fixture_inventory_has_explicit_outcomes() -> None:
    events = _valid_events()
    assert set(events) == {"approval-decision", "approval-request", "tool-call"}
    for path in sorted((_FIXTURES / "valid").glob("*.json")):
        assert AripEventEnvelope.model_validate_json(path.read_text(encoding="utf-8")) == events[path.stem]
    assert {path.stem for path in (_FIXTURES / "invalid").glob("*.json")} == {
        "malformed-digest", "unbound-tool-call", "unknown-field", "wrong-version",
    }
    for path in sorted((_FIXTURES / "invalid").glob("*.json")):
        with pytest.raises(ValidationError):
            AripEventEnvelope.model_validate(_load(path))


def test_hash_domain_and_redaction_are_normative() -> None:
    first = {"z": [3, 2, 1], "a": {"β": "value"}}
    second = {"a": {"β": "value"}, "z": [3, 2, 1]}
    tool = _valid_events()["tool-call"].payload
    assert isinstance(tool, ToolCall)
    assert canonical_json(first) == canonical_json(second) == b'{"a":{"\xce\xb2":"value"},"z":[3,2,1]}'
    assert sha256_digest(first) == sha256_digest(second)
    assert tool.arguments == {"password": REDACTED, "recipient": "fixture@example.test"}
    assert tool.redacted_preview_digest == _PREVIEW_DIGEST
    assert redacted_preview_digest("send_message", {"recipient": "fixture@example.test", "password": "different"}) == _PREVIEW_DIGEST
    for invalid in ({1: "value"}, {"x": 1.0}, {"x": (1, 2)}, {"x": {1, 2}}, 2**63):
        with pytest.raises((TypeError, ValueError)):
            canonical_json(invalid)  # type: ignore[arg-type]


def test_inputs_are_deeply_snapshotted_and_immutable() -> None:
    raw = _raw_valid()["tool-call"]
    arguments = raw["payload"]["arguments"]  # type: ignore[index]
    event = AripEventEnvelope.model_validate(raw)
    assert isinstance(event.payload, ToolCall)
    assert isinstance(arguments, dict)
    arguments["recipient"] = "mutated@example.test"
    assert event.payload.arguments["recipient"] == "fixture@example.test"  # type: ignore[index]
    with pytest.raises(TypeError, match="immutable"):
        event.payload.arguments["recipient"] = "mutated"  # type: ignore[index]

    actors = ["@fixture-approver:example.test"]
    request_raw = _raw_valid()["approval-request"]
    request_raw["payload"]["eligible_actors"] = actors  # type: ignore[index]
    request = AripEventEnvelope.model_validate(request_raw).payload
    assert isinstance(request, ApprovalRequest)
    actors.append("@attacker:example.test")
    assert request.eligible_actors == ("@fixture-approver:example.test",)


def test_top_level_dict_union_mutation_is_rejected_without_changing_event_digest() -> None:
    event = _valid_events()["tool-call"]
    assert isinstance(event.payload, ToolCall)
    digest = sha256_digest(event)
    arguments = event.payload.arguments

    with pytest.raises(TypeError, match="immutable"):
        arguments |= {"recipient": "mutated@example.test"}

    assert sha256_digest(event) == digest


def test_nested_dict_union_mutation_is_rejected_without_changing_event_digest() -> None:
    raw = _raw_valid()["tool-call"]
    payload = raw["payload"]
    assert isinstance(payload, dict)
    arguments = payload["arguments"]
    assert isinstance(arguments, dict)
    arguments["metadata"] = {"label": "original"}
    payload["redacted_preview_digest"] = redacted_preview_digest("send_message", arguments)
    event = AripEventEnvelope.model_validate(raw)
    assert isinstance(event.payload, ToolCall)
    digest = sha256_digest(event)
    nested = event.payload.arguments["metadata"]  # type: ignore[index]

    with pytest.raises(TypeError, match="immutable"):
        nested |= {"label": "mutated"}  # type: ignore[operator]

    assert sha256_digest(event) == digest


def test_pre_redaction_rejects_non_json_python_inputs() -> None:
    raw = _raw_valid()["tool-call"]
    for arguments in ({"secret": {"set"}}, {"secret": ("tuple",)}, {1: "non-string"}):
        raw["payload"]["arguments"] = arguments  # type: ignore[index]
        with pytest.raises(ValidationError, match=r"not a JSON|non-string"):
            AripEventEnvelope.model_validate(raw)


def test_authored_chain_relationships_and_fixture_inventory() -> None:
    events = _valid_events()
    validate_authored_chain(events["tool-call"], events["approval-request"], events["approval-decision"])
    inventory = sorted((_FIXTURES / "invalid-chains").glob("*.json"))
    assert {path.stem for path in inventory} == {"denied", "out-of-order", "request-tool-mismatch", "wrong-approval-id"}
    for path in inventory:
        case = _load(path)
        changed = _patched_event(case["event"], case["patch"])  # type: ignore[arg-type]
        chain = dict(events)
        chain[case["event"]] = changed  # type: ignore[index]
        if path.stem == "denied":
            with pytest.raises(AripValidationError, match=case["error"]):  # type: ignore[arg-type]
                validate_live_approval(*_chain(chain), observed_at=datetime(2026, 7, 15, 12, 0, 3, tzinfo=UTC))
        else:
            with pytest.raises(AripValidationError, match=case["error"]):  # type: ignore[arg-type]
                validate_authored_chain(*_chain(chain))

    for path in sorted((_FIXTURES / "invalid-decisions").glob("*.json")):
        chain = dict(events)
        chain["approval-decision"] = AripEventEnvelope.model_validate(_load(path))
        with pytest.raises(AripValidationError):
            validate_authored_chain(*_chain(chain))


def _chain(events: dict[str, AripEventEnvelope]) -> tuple[AripEventEnvelope, AripEventEnvelope, AripEventEnvelope]:
    return events["tool-call"], events["approval-request"], events["approval-decision"]


def test_historical_and_live_time_validation_are_separate() -> None:
    chain = _chain(_valid_events())
    validate_authored_chain(*chain)
    validate_live_approval(*chain, observed_at=datetime(2026, 7, 15, 12, 0, 3, tzinfo=UTC))
    with pytest.raises(TypeError, match="observed_at"):
        validate_live_approval(*chain)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="timezone"):
        validate_live_approval(*chain, observed_at=datetime(2026, 7, 15, 12, 0, 3))
    with pytest.raises(AripValidationError, match="precedes"):
        validate_live_approval(*chain, observed_at=datetime(2026, 7, 15, 12, 0, 1, tzinfo=UTC))
    with pytest.raises(AripValidationError, match="expired"):
        validate_live_approval(*chain, observed_at=datetime(2026, 7, 15, 12, 5, 1, tzinfo=UTC))


def test_replay_guard_is_atomic_under_concurrency_and_rejects_equivocation() -> None:
    event = _valid_events()["approval-decision"]
    guard = InMemoryReplayGuard()
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda _: guard.check_and_record(event), range(64)))
    assert results.count(True) == 1
    assert results.count(False) == 63
    with pytest.raises(AripValidationError, match="different content"):
        guard.check_and_record(event.model_copy(update={"source": "different"}))


def test_schema_and_settings_are_strictly_fixture_only() -> None:
    schema = AripEventEnvelope.model_json_schema()
    assert len(schema["$defs"]["AripPayload"]["oneOf"]) == 3
    assert AripSettings() == AripSettings(mode=AripMode.OFF, execution_enabled=False)
    assert AripSettings(mode=AripMode.SHADOW).execution_enabled is False
    with pytest.raises(ValidationError):
        AripSettings.model_validate({"mode": "shadow", "execution_enabled": True})
