"""Flight-Recorder-gated capture for governed learning proposals."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from mindroom.constants import tracking_dir
from mindroom.flight_recorder import FlightRecorder
from mindroom.learning_loop import ArtifactKind, LearningLoopError, LearningLoopStore, LearningProposal

if TYPE_CHECKING:
    from mindroom.constants import RuntimePaths
    from mindroom.flight_recorder import FlightRecord


@dataclass(frozen=True, slots=True)
class LearningCandidate:
    """One bounded artifact proposed by an explicit learning candidate producer."""

    source_run_id: str
    kind: ArtifactKind
    artifact: dict[str, object]

    def __post_init__(self) -> None:
        """Reject blank and unbounded candidates before reading trace state."""
        if not self.source_run_id or not self.artifact:
            message = "learning candidate requires a source run and artifact"
            raise LearningLoopError(message)
        encoded = _json_bytes(self.artifact)
        if len(encoded) > 1_000_000:
            message = "learning candidate artifact exceeds the capture limit"
            raise LearningLoopError(message)


class FlightRecorderLearningCapture:
    """Capture candidates only from verified, successfully delivered source runs."""

    def __init__(self, *, recorder: FlightRecorder, store: LearningLoopStore) -> None:
        self._recorder = recorder
        self._store = store

    async def capture(
        self,
        candidate: LearningCandidate,
        *,
        captured_at: datetime | None = None,
    ) -> LearningProposal:
        """Verify source evidence and idempotently persist one immutable proposal."""
        records = await self._recorder.records(candidate.source_run_id)
        if not records:
            message = "learning candidate source run has no Flight Recorder evidence"
            raise LearningLoopError(message)
        if not _successfully_delivered(records):
            message = "learning candidate source run lacks successful terminal delivery evidence"
            raise LearningLoopError(message)
        if _has_failed_execution(records):
            message = "learning candidate source run contains failed or interrupted execution"
            raise LearningLoopError(message)
        proposal_id = _proposal_id(candidate)
        return await self._store.propose(
            proposal_id=proposal_id,
            source_run_id=candidate.source_run_id,
            kind=candidate.kind,
            artifact=candidate.artifact,
            proposed_at=captured_at or datetime.now(UTC),
        )


async def capture_learning_candidate(
    runtime_paths: RuntimePaths,
    candidate: LearningCandidate,
    *,
    captured_at: datetime | None = None,
) -> LearningProposal:
    """Capture through production tracking stores with bounded connection lifetimes."""
    tracking = tracking_dir(runtime_paths)
    recorder = FlightRecorder(tracking / "flight_recorder.db")
    store = LearningLoopStore(tracking / "learning_loop.db")
    await recorder.open()
    try:
        await store.open()
        try:
            service = FlightRecorderLearningCapture(recorder=recorder, store=store)
            return await service.capture(candidate, captured_at=captured_at)
        finally:
            await store.close()
    finally:
        await recorder.close()


def _successfully_delivered(records: tuple[FlightRecord, ...]) -> bool:
    for record in records:
        payload = record.payload
        if (
            record.kind == "message"
            and isinstance(payload, dict)
            and payload.get("direction") == "outbound"
            and payload.get("status") == "completed"
            and payload.get("is_visible_response") is True
            and payload.get("suppressed") is False
        ):
            return True
    return False


def _has_failed_execution(records: tuple[FlightRecord, ...]) -> bool:
    terminal_failures = {"abandoned", "cancelled", "failed"}
    for record in records:
        payload = record.payload
        if not isinstance(payload, dict):
            continue
        if record.kind in {"model_call", "runtime_state"} and payload.get("status") in terminal_failures:
            return True
        if record.kind == "tool_call" and payload.get("success") is False:
            return True
    return False


def _proposal_id(candidate: LearningCandidate) -> str:
    digest = hashlib.sha256(
        _json_bytes(
            {
                "artifact": candidate.artifact,
                "kind": candidate.kind,
                "source_run_id": candidate.source_run_id,
            },
        ),
    ).hexdigest()
    return f"flight:{digest}"


def _json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as exc:
        message = "learning candidate artifact must be finite JSON"
        raise LearningLoopError(message) from exc
