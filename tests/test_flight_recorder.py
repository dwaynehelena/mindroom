"""Tests for tamper-evident redacted flight recording and safe replay."""

# ruff: noqa: D103

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from mindroom.flight_recorder import FlightRecorder, FlightRecorderError
from mindroom.redaction import REDACTED

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def recorder(tmp_path: Path) -> AsyncIterator[FlightRecorder]:
    value = FlightRecorder(tmp_path / "flight.db")
    await value.open()
    yield value
    await value.close()


async def test_records_are_redacted_hash_chained_and_pure_runs_replay(recorder: FlightRecorder) -> None:
    first = await recorder.append(
        run_id="run-1",
        kind="model_call",
        payload={"prompt": "hello", "api_key": "secret"},
        side_effect=False,
        occurred_at=datetime(2026, 7, 18, tzinfo=UTC),
        duration_ms=12,
        cost_microunits=42,
    )
    second = await recorder.append(
        run_id="run-1",
        kind="runtime_state",
        payload={"status": "done"},
        side_effect=False,
        occurred_at=datetime(2026, 7, 18, 0, 0, 1, tzinfo=UTC),
    )
    assert first.payload["api_key"] == REDACTED
    assert second.previous_hash == first.record_hash
    assert await recorder.replayable("run-1") == (first, second)


async def test_side_effecting_run_is_never_replayable(recorder: FlightRecorder) -> None:
    await recorder.append(
        run_id="run-write",
        kind="tool_call",
        payload={"tool": "send_message"},
        side_effect=True,
    )
    with pytest.raises(FlightRecorderError, match="not replayable"):
        await recorder.replayable("run-write")


async def test_database_tampering_is_detected(recorder: FlightRecorder) -> None:
    record = await recorder.append(
        run_id="run-1",
        kind="message",
        payload={"body": "hello"},
        side_effect=False,
    )
    assert recorder._db is not None
    await recorder._db.execute("DROP TRIGGER flight_record_no_update")
    await recorder._db.execute("UPDATE flight_record SET record_hash=? WHERE sequence=?", ("0" * 64, record.sequence))
    await recorder._db.commit()
    with pytest.raises(FlightRecorderError, match="integrity failed"):
        await recorder.verify_chain()
