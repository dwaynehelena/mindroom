"""Tests for Flight-Recorder-gated automatic learning capture."""

# ruff: noqa: ANN001, D103

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from mindroom.flight_recorder import FlightRecorder
from mindroom.learning_candidates import candidate_from_signed_skill
from mindroom.learning_capture import FlightRecorderLearningCapture, LearningCandidate
from mindroom.learning_loop import LearningLoopError, LearningLoopStore
from mindroom.skill_registry import (
    SandboxEvidence,
    ScanPolicy,
    SkillTrustRegistry,
    manifest_digest,
    translate_openclaw_skill,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.asyncio
NOW = datetime(2026, 7, 18, tzinfo=UTC)


@pytest_asyncio.fixture
async def capture(tmp_path: Path) -> AsyncIterator[tuple[FlightRecorder, FlightRecorderLearningCapture]]:
    recorder = FlightRecorder(tmp_path / "flight.db")
    store = LearningLoopStore(tmp_path / "learning.db")
    await recorder.open()
    await store.open()
    yield recorder, FlightRecorderLearningCapture(recorder=recorder, store=store)
    await store.close()
    await recorder.close()


async def _record_delivery(recorder: FlightRecorder, run_id: str, *, status: str = "completed") -> None:
    await recorder.append(
        run_id=run_id,
        kind="message",
        payload={
            "direction": "outbound",
            "is_visible_response": True,
            "status": status,
            "suppressed": False,
        },
        side_effect=True,
        occurred_at=NOW,
    )


async def test_capture_derives_idempotent_proposal_from_verified_success(capture) -> None:
    recorder, service = capture
    await recorder.append(
        run_id="run-1",
        kind="model_call",
        payload={"status": "completed"},
        side_effect=False,
        occurred_at=NOW,
    )
    await _record_delivery(recorder, "run-1")
    candidate = LearningCandidate("run-1", "skill", {"name": "better-summary", "instructions": ["Be concise"]})
    first = await service.capture(candidate, captured_at=NOW)
    second = await service.capture(candidate, captured_at=NOW)
    assert first == second
    assert first.proposal_id.startswith("flight:")
    assert first.source_run_id == "run-1"
    assert first.stage == "proposed"


async def test_verified_registry_candidate_flows_into_governed_capture(capture) -> None:
    recorder, service = capture
    await _record_delivery(recorder, "run-1")
    registry = SkillTrustRegistry(b"s" * 32)
    manifest = translate_openclaw_skill({"id": "safe.read", "version": "1", "command": "bin/read"})
    registry.register(manifest)
    registry.scan(manifest.skill_id, manifest.version, ScanPolicy(frozenset(), frozenset(), frozenset()))
    registry.record_sandbox(
        manifest.skill_id,
        manifest.version,
        SandboxEvidence("runner", manifest_digest(manifest), True, True, True, 2),
    )
    entry = registry.sign(manifest.skill_id, manifest.version)
    candidate = candidate_from_signed_skill(source_run_id="run-1", entry=entry, registry=registry)
    proposal = await service.capture(candidate, captured_at=NOW)
    assert proposal.kind == "skill"
    assert proposal.artifact["signature"] == entry.signature


@pytest.mark.parametrize("terminal_status", ["failed", "cancelled", "suppressed"])
async def test_capture_denies_run_without_successful_visible_delivery(capture, terminal_status: str) -> None:
    recorder, service = capture
    await _record_delivery(recorder, "run-1", status=terminal_status)
    with pytest.raises(LearningLoopError, match="terminal delivery"):
        await service.capture(LearningCandidate("run-1", "memory", {"fact": "safe"}), captured_at=NOW)


async def test_capture_denies_failed_tool_even_after_delivery(capture) -> None:
    recorder, service = capture
    await recorder.append(
        run_id="run-1",
        kind="tool_call",
        payload={"success": False, "tool_name": "example"},
        side_effect=True,
        occurred_at=NOW,
    )
    await _record_delivery(recorder, "run-1")
    with pytest.raises(LearningLoopError, match="failed or interrupted"):
        await service.capture(LearningCandidate("run-1", "skill", {"name": "unsafe"}), captured_at=NOW)


async def test_capture_denies_missing_or_nonfinite_artifact(capture) -> None:
    _recorder, service = capture
    with pytest.raises(LearningLoopError, match="source run and artifact"):
        await service.capture(LearningCandidate("run-1", "memory", {}), captured_at=NOW)
    with pytest.raises(LearningLoopError, match="finite JSON"):
        LearningCandidate("run-1", "memory", {"score": float("nan")})
