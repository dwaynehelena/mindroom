"""U3 learning-promotion slice tests for C7 (LearningCapture caller)."""

# ruff: noqa: ANN001, D103

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from structlog import get_logger

from mindroom.constants import RuntimePaths, tracking_dir
from mindroom.final_delivery import FinalDeliveryOutcome
from mindroom.flight_recorder import FlightRecorder
from mindroom.learning_capture import FlightRecorderLearningCapture, LearningCandidate
from mindroom.learning_loop import LearningLoopError, LearningLoopStore
from mindroom.post_response_effects import PostResponseEffectsDeps, ResponseOutcome, apply_post_response_effects

NOW = datetime(2026, 8, 20, tzinfo=UTC)


def _paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(tmp_path / "config.yaml", tmp_path, tmp_path / ".env", tmp_path / "data")


def _visible_outcome(event_id: str = "$response") -> FinalDeliveryOutcome:
    return FinalDeliveryOutcome(
        terminal_status="completed",
        event_id=event_id,
        is_visible_response=True,
        final_visible_body="ok",
        delivery_kind="sent",
    )


async def _record_delivery(recorder: FlightRecorder, run_id: str, *, origin: str | None = None) -> None:
    payload: dict[str, object] = {
        "direction": "outbound",
        "is_visible_response": True,
        "status": "completed",
        "suppressed": False,
    }
    if origin is not None:
        payload["origin"] = origin
    await recorder.append(
        run_id=run_id,
        kind="message",
        payload=payload,
        side_effect=True,
        occurred_at=NOW,
    )


@pytest.mark.asyncio
async def test_c7_visible_reply_records_evidence_and_learning_runtime_proposes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    deps = PostResponseEffectsDeps(
        logger=get_logger("tests.u3.learning"),
        runtime_paths=paths,
        agent_name="research",
    )
    await apply_post_response_effects(
        _visible_outcome(),
        ResponseOutcome(response_run_id="run-live-1", run_succeeded=True),
        deps,
    )
    recorder = FlightRecorder(tracking_dir(paths) / "flight_recorder.db")
    await recorder.open()
    try:
        records = await recorder.records("run-live-1")
    finally:
        await recorder.close()
    assert records
    payload = records[-1].payload
    assert isinstance(payload, dict)
    assert payload["origin"] == "visible_reply"
    assert payload["is_visible_response"] is True
    assert payload["status"] == "completed"
    store = LearningLoopStore(tracking_dir(paths) / "learning_loop.db")
    await store.open()
    try:
        proposals = await store.list_by_source_run("run-live-1")
    finally:
        await store.close()
    assert len(proposals) == 1
    loaded = proposals[0]
    assert loaded.stage == "proposed"
    assert loaded.source_run_id == "run-live-1"
    assert loaded.artifact["origin"] == "visible_reply"
    assert loaded.artifact["event_id"] == "$response"


@pytest.mark.asyncio
async def test_c7_demo_script_only_origin_yields_no_candidate(tmp_path: Path) -> None:
    recorder = FlightRecorder(tmp_path / "flight.db")
    store = LearningLoopStore(tmp_path / "learning.db")
    await recorder.open()
    await store.open()
    try:
        await _record_delivery(recorder, "learning-stable-demo-run", origin="demo-script")
        service = FlightRecorderLearningCapture(recorder=recorder, store=store)
        with pytest.raises(LearningLoopError, match="demo-script-only"):
            await service.capture(
                LearningCandidate(
                    "learning-stable-demo-run",
                    "skill",
                    {"name": "demo-only", "origin": "demo_script"},
                ),
                captured_at=NOW,
            )
    finally:
        await store.close()
        await recorder.close()


@pytest.mark.asyncio
async def test_c7_no_flight_record_yields_no_candidate(tmp_path: Path) -> None:
    recorder = FlightRecorder(tmp_path / "flight.db")
    store = LearningLoopStore(tmp_path / "learning.db")
    await recorder.open()
    await store.open()
    try:
        service = FlightRecorderLearningCapture(recorder=recorder, store=store)
        with pytest.raises(LearningLoopError, match="no Flight Recorder evidence"):
            await service.capture(
                LearningCandidate("run-missing", "memory", {"origin": "visible_reply", "fact": "none"}),
                captured_at=NOW,
            )
    finally:
        await store.close()
        await recorder.close()


@pytest.mark.asyncio
async def test_c7_failed_visible_delivery_does_not_write_or_propose(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    deps = PostResponseEffectsDeps(
        logger=get_logger("tests.u3.learning"),
        runtime_paths=paths,
        agent_name="research",
    )
    await apply_post_response_effects(
        FinalDeliveryOutcome(terminal_status="error", event_id=None, failure_reason="delivery_failed"),
        ResponseOutcome(response_run_id="run-fail-1", run_succeeded=False),
        deps,
    )
    tracking = tracking_dir(paths)
    assert not (tracking / "flight_recorder.db").exists()
    assert not (tracking / "learning_loop.db").exists()


def test_fr35_openclaw_agno_learning_stays_off() -> None:
    config = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    assert config["agents"]["openclaw"]["learning"] is False