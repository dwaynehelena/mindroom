"""Phase A — streaming worker tool-state forwarding into MindRoom threads.

This module is the fully additive, local-only Phase A of mesh tool-state
streaming (Item 3).  It provides:

- ``MeshToolStateForwarder`` — consumes a worker's tool start / completed
  events and produces ``StructuredStreamChunk``-compatible tool traces
  (reusing ``ToolTraceEntry`` / ``StructuredStreamChunk`` shapes from
  ``mindroom.tool_system.events``).  It applies per-session sequence
  numbering so a reconnecting observer can skip already-seen state, and
  redacts tool results unless ``include_results`` is explicitly enabled.

- ``MeshToolStateSink`` Protocol — the injectable sink surface.  Two
  implementations: ``MatrixToolStateSink`` (posts tool-state into the worker's
  thread via an injected ``MeshTransport`` / fake — depends only on
  ``MeshTransport``, never ``nio``) and ``NullToolStateSink`` (default no-op
  in gateway-only mode).

- ``MeshToolStateCoordinator`` — owns per-session forwarders so tool-state
  flows into the correct mapped thread (integrating Item 2 thread/session
  mapping) and can be resumed across reconnects (integrating Item 5
  session-aware resume via per-session sequencing).

- ``MeshToolStateObserver`` — a reconnecting observer that skips tool-state
  deltas already seen for a session.

Phase A is purely local: the forwarder normalizes, sequences, redacts, and
forwards into an injectable sink; the ``MatrixToolStateSink`` records into the
thread through the injected transport/fake with **no network calls**.
Actually posting/editing *streaming edits into a live Matrix room* via
``delivery_gateway`` is an external, human-gated Phase B side effect
(``PHASE_B_TOOL_STREAM_POSTING_ENABLED``) that is NOT performed here (see
``docs/mesh_tool_state_phase_b_gate.md``).

PRIVACY INVARIANT: streaming tool-state must NOT leak message-content bodies
into the content-free lifecycle.  Only tool-trace metadata (``io.mindroom.tool_trace``)
may flow; lifecycle events stay content-free (``tool_state_streamed`` carries
a count and no payload).  Tool results are kept out unless explicitly
configured (``include_results=False`` default) AND human-approved
(``INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED``).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from agno.models.response import ToolExecution  # noqa: TC002  # type annotation + forwarded to tool_system

from mindroom.mesh.lifecycle import MeshLifecycleEvent
from mindroom.tool_system.events import (
    StructuredStreamChunk,
    ToolTraceEntry,
    build_tool_trace_content,
    format_tool_completed_event,
    format_tool_started_event,
)

if TYPE_CHECKING:
    from mindroom.mesh.session_map import MeshSessionResolution
    from mindroom.mesh.transport import MeshTransport

logger = logging.getLogger(__name__)

__all__ = [
    "INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED",
    "MESH_TOOL_STREAM_ENV",
    "PHASE_B_TOOL_STREAM_POSTING_ENABLED",
    "MatrixToolStateSink",
    "MeshToolStateChunk",
    "MeshToolStateCoordinator",
    "MeshToolStateError",
    "MeshToolStateForwarder",
    "MeshToolStateObserver",
    "MeshToolStateSink",
    "NullToolStateSink",
    "tool_state_flag_enabled",
]

#: Env var that enables worker tool-state streaming.  Default OFF keeps the
#: gateway's behavior identical to today (no tool-state forwarding).
MESH_TOOL_STREAM_ENV = "MINDROOM_MESH_TOOL_STREAM"

#: Phase B real live-room streaming edits are hard-gated off.  Actually posting
#: or editing *streaming edits* into a live Matrix room via ``delivery_gateway``
#: (a real network side effect) may not occur unless an operator explicitly
#: enables it after human review (see docs/mesh_tool_state_phase_b_gate.md).
#: Phase A forwarding is local-only.
PHASE_B_TOOL_STREAM_POSTING_ENABLED = False

#: Phase B gating for tool-result inclusion.  Forwarding tool *results* is a
#: privacy-relevant leak that may not occur unless an operator explicitly
#: approves it (``include_results=True``) after human review.  Default OFF keeps
#: tool results out of forwarded tool-state.
INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED = False


def tool_state_flag_enabled(env: dict[str, str] | None = None) -> bool:
    """Return whether tool-state streaming is enabled by env/flag (default OFF)."""
    source = env if env is not None else os.environ
    return (source.get(MESH_TOOL_STREAM_ENV) or "").strip().lower() in {"1", "true", "yes", "on"}


class MeshToolStateError(RuntimeError):
    """Raised when the tool-state streaming path cannot satisfy a request."""


@dataclass(frozen=True, slots=True)
class MeshToolStateChunk:
    """One sequenced, normalized tool-state delta forwarded to a sink.

    Carries the canonical session/thread target (from Item 2 mapping) so the
    sink knows which thread to post into, the per-session ``sequence`` (so a
    reconnecting observer can skip already-seen state), and the normalized,
    redacted ``ToolTraceEntry``.
    """

    session_id: str
    worker_id: str
    sequence: int
    trace: ToolTraceEntry
    room_id: str
    thread_id: str | None

    def as_structured_chunk(self) -> object:
        """Return a ``StructuredStreamChunk``-compatible shape carrying this trace."""
        return StructuredStreamChunk(content="", tool_trace=[self.trace])


@runtime_checkable
class MeshToolStateSink(Protocol):
    """Injectable sink that receives one sequenced tool-state delta.

    Phase A supplies ``MatrixToolStateSink`` (posts into the worker's thread
    via an injected ``MeshTransport`` / fake — local, no network) and
    ``NullToolStateSink`` (default no-op).  Real live-room posting is Phase B.
    """

    def forward(self, chunk: MeshToolStateChunk) -> None:
        """Accept one tool-state delta for posting into its target thread."""
        ...


class NullToolStateSink:
    """Default no-op sink used in gateway-only mode when no sink is configured."""

    def forward(self, chunk: MeshToolStateChunk) -> None:
        """Discard the delta (no-op)."""
        # No-op: nothing is forwarded in gateway-only mode by default.


class MatrixToolStateSink:
    """Local Phase A sink that posts tool-state into a worker's thread.

    Depends only on ``MeshTransport`` (never ``nio``): in Phase A the injected
    transport is the in-memory fake, so posting is fully local with no network
    calls.  Each forwarded chunk is recorded into a thread-scoped log so tests
    can assert thread-target correctness.  Real live-room streaming edits
    (via ``delivery_gateway``) are the external, human-gated Phase B.
    """

    def __init__(
        self,
        transport: MeshTransport,
        *,
        lifecycle_sink: list[MeshLifecycleEvent] | None = None,
    ) -> None:
        self.transport = transport
        self.lifecycle_sink = lifecycle_sink if lifecycle_sink is not None else []
        self._posted: dict[str, list[MeshToolStateChunk]] = {}

    def forward(self, chunk: MeshToolStateChunk) -> None:
        """Record one tool-state delta into its thread-scoped log."""
        key = chunk.thread_id if chunk.thread_id is not None else chunk.room_id
        self._posted.setdefault(key, []).append(chunk)

    def posted_chunks(self, thread_id: str) -> list[MeshToolStateChunk]:
        """Return the deltas posted to one thread (``thread_id=None`` => room mode)."""
        return list(self._posted.get(thread_id, []))

    def all_posted(self) -> list[MeshToolStateChunk]:
        """Return every delta posted through this sink, in forwarding order."""
        ordered: list[MeshToolStateChunk] = []
        for chunks in self._posted.values():
            ordered.extend(chunks)
        return ordered


class MeshToolStateObserver:
    """A reconnecting observer that skips already-seen per-session tool-state.

    Tracks the last seen sequence per the forwarder's per-session counter so a
    reconnecting observer does not re-forward (or re-post) deltas it already saw.
    """

    def __init__(self, sink: MeshToolStateSink, *, last_seen_sequence: int = 0) -> None:
        self.sink = sink
        self.last_seen_sequence = last_seen_sequence

    def forward(self, chunk: MeshToolStateChunk) -> bool:
        """Forward one delta only if it is newer than the last seen sequence."""
        if chunk.sequence <= self.last_seen_sequence:
            return False
        self.sink.forward(chunk)
        self.last_seen_sequence = chunk.sequence
        return True


def _redact_trace(trace: ToolTraceEntry, *, include_results: bool) -> ToolTraceEntry:
    """Return a tool trace with tool results stripped unless ``include_results``.

    PRIVACY INVARIANT: tool results never flow unless explicitly configured
    (``include_results=False`` default) AND human-approved.  Args previews are
    kept (they are already part of normal tool-trace metadata).
    """
    if include_results or trace.result_preview is None:
        return trace
    return ToolTraceEntry(
        type=trace.type,
        tool_name=trace.tool_name,
        args_preview=trace.args_preview,
        result_preview=None,
        truncated=trace.truncated,
    )


def _tool_trace_bytes(trace: ToolTraceEntry) -> int:
    """Return the serialized byte size of one tool-trace metadata payload."""
    payload = build_tool_trace_content([trace])
    if payload is None:
        return 0
    return len(json.dumps(payload, separators=(",", ":")).encode("utf-8"))


@dataclass(slots=True)
class MeshToolStateForwarder:
    """Consumes worker tool start/completed events and forwards sequenced deltas.

    Per-session: scoped to one canonical session (``session_id``) and thread
    (``room_id`` + ``thread_id``), so tool-state is forwarded into the correct
    mapped thread.  Each forwarded delta carries a monotonically increasing
    per-session ``sequence`` so a reconnecting observer can skip already-seen
    state.  Emits content-free ``tool_state_streamed`` lifecycle events with a
    count and no payload.
    """

    worker_id: str
    session_id: str
    room_id: str
    thread_id: str | None
    sink: MeshToolStateSink
    include_results: bool = False
    max_chunk_bytes: int | None = None
    lifecycle_sink: list[MeshLifecycleEvent] | None = None
    _sequence: int = field(default=0, init=False, repr=False)

    @property
    def current_sequence(self) -> int:
        """Return the number of deltas already forwarded for this session."""
        return self._sequence

    def _emit_streamed(self) -> None:
        """Emit one content-free ``tool_state_streamed`` event (count, no payload)."""
        if self.lifecycle_sink is None:
            return
        self.lifecycle_sink.append(
            MeshLifecycleEvent(
                event_type="tool_state_streamed",
                worker_id=self.worker_id,
                count=self._sequence,
            ),
        )

    def _forward(self, trace: ToolTraceEntry) -> MeshToolStateChunk | None:
        """Normalize, redact, sequence, and forward one tool-state delta."""
        redacted = _redact_trace(trace, include_results=self.include_results)
        if self.max_chunk_bytes is not None and _tool_trace_bytes(redacted) > self.max_chunk_bytes:
            return None
        self._sequence += 1
        chunk = MeshToolStateChunk(
            session_id=self.session_id,
            worker_id=self.worker_id,
            sequence=self._sequence,
            trace=redacted,
            room_id=self.room_id,
            thread_id=self.thread_id,
        )
        self.sink.forward(chunk)
        self._emit_streamed()
        return chunk

    def on_tool_start(self, tool: ToolExecution | None) -> MeshToolStateChunk | None:
        """Consume one worker tool-start event and forward its trace delta."""
        if tool is None:
            return None
        _, trace = format_tool_started_event(tool)
        if trace is None:
            return None
        return self._forward(trace)

    def on_tool_complete(self, tool: ToolExecution | None) -> MeshToolStateChunk | None:
        """Consume one worker tool-completed event and forward its trace delta."""
        if tool is None:
            return None
        _, trace = format_tool_completed_event(tool)
        if trace is None:
            return None
        return self._forward(trace)


class MeshToolStateCoordinator:
    """Orchestrate per-session tool-state forwarding for one gateway.

    Fully additive: the gateway only consults this coordinator when it is
    present and ``enabled`` (default-OFF).  It owns one ``MeshToolStateForwarder``
    per canonical session (keyed on ``session_id``) so tool-state flows into the
    correct mapped thread and per-session sequencing survives reconnect.  No
    real Matrix client / network call is ever made (Phase B posting is gated).
    """

    def __init__(
        self,
        *,
        sink_factory: object | None = None,
        include_results: bool = False,
        max_chunk_bytes: int | None = None,
        lifecycle_sink: list[MeshLifecycleEvent] | None = None,
        enabled: bool = True,
    ) -> None:
        if include_results and not INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED:
            msg = (
                "include_results=True requires human approval "
                "(INCLUDE_TOOL_RESULTS_PHASE_B_ENABLED); refusing to leak tool results"
            )
            raise MeshToolStateError(msg)
        self.enabled = enabled
        self.include_results = include_results
        self.max_chunk_bytes = max_chunk_bytes
        self.lifecycle_sink = lifecycle_sink if lifecycle_sink is not None else []
        #: Callable returning a ``MeshToolStateSink``, or ``None`` for the null
        #: no-op sink (gateway-only default).
        self.sink_factory = sink_factory
        self._forwarders: dict[str, MeshToolStateForwarder] = {}

    def _ensure_forwarder(self, resolution: MeshSessionResolution) -> MeshToolStateForwarder:
        key = resolution.session_id
        forwarder = self._forwarders.get(key)
        if forwarder is not None:
            return forwarder
        sink = self.sink_factory() if self.sink_factory is not None else NullToolStateSink()
        forwarder = MeshToolStateForwarder(
            worker_id=resolution.worker_id,
            session_id=resolution.session_id,
            room_id=resolution.room_id,
            thread_id=resolution.thread_id,
            sink=sink,
            include_results=self.include_results,
            max_chunk_bytes=self.max_chunk_bytes,
            lifecycle_sink=self.lifecycle_sink,
        )
        self._forwarders[key] = forwarder
        return forwarder

    def forward_start(
        self,
        resolution: MeshSessionResolution,
        tool: ToolExecution | None,
    ) -> MeshToolStateChunk | None:
        """Forward one tool-start delta for a worker's resolved session."""
        if not self.enabled or tool is None:
            return None
        return self._ensure_forwarder(resolution).on_tool_start(tool)

    def forward_complete(
        self,
        resolution: MeshSessionResolution,
        tool: ToolExecution | None,
    ) -> MeshToolStateChunk | None:
        """Forward one tool-completed delta for a worker's resolved session."""
        if not self.enabled or tool is None:
            return None
        return self._ensure_forwarder(resolution).on_tool_complete(tool)

    def forwarder_for(self, session_id: str) -> MeshToolStateForwarder | None:
        """Return the forwarder for one canonical session, or ``None``."""
        return self._forwarders.get(session_id)
