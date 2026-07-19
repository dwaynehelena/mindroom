"""Safe orchestration of deduplication, bounded invocation, and lifecycle recording."""
# ruff: noqa: EM101, TC001, TRY003

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any

from mindroom.message_target import MessageTarget

from .adapter import RuntimeAdapter
from .models import ConversationScope, EventOrigin, RuntimeRequest, RuntimeResult, normalized_json_bytes
from .store import RuntimeBridgeStore, RuntimeStateEvent

RuntimeStateSink = Callable[[str, str, str], Awaitable[None]]

_TERMINAL_PHASES = frozenset({"response_ready", "delivered", "delivery_failed", "failed"})


class DuplicateSourceEventError(RuntimeError):
    """The source event has a durable reservation and must not be invoked again."""


class RuntimeBridgeService:
    """Isolated bridge service with explicit process concurrency bounds."""

    def __init__(
        self,
        store: RuntimeBridgeStore,
        *,
        max_concurrency: int = 4,
        max_waiters: int = 16,
        max_sessions: int = 1000,
        state_sink: RuntimeStateSink | None = None,
    ) -> None:
        if not 1 <= max_concurrency <= 64:
            raise ValueError("max_concurrency must be between 1 and 64")
        if not 0 <= max_waiters <= 4096:
            raise ValueError("max_waiters must be between 0 and 4096")
        self._store = store
        self._admission_slots = asyncio.BoundedSemaphore(max_concurrency + max_waiters)
        self._invocation_slots = asyncio.Semaphore(max_concurrency)
        self._accepting = True
        self._max_sessions = max_sessions
        self._state_sink = state_sink

    @property
    def ready(self) -> bool:
        """Return whether the operator kill switch permits new admissions."""
        return self._accepting

    def disable(self) -> None:
        """Fail closed for new work while allowing admitted work to settle."""
        self._accepting = False

    async def observe(
        self,
        source_event_id: str,
        *,
        after_sequence: int = 0,
        poll_interval: float = 0.1,
    ) -> AsyncIterator[RuntimeStateEvent]:
        """Replay then follow content-free lifecycle state from a reconnect cursor."""
        if poll_interval <= 0 or poll_interval > 5:
            raise ValueError("runtime observation poll interval must be between zero and five seconds")
        cursor = after_sequence
        while True:
            events = await self._store.states_since(source_event_id, after_sequence=cursor)
            for event in events:
                cursor = event.sequence
                yield event
                if event.phase in _TERMINAL_PHASES:
                    return
            lifecycle = await self._store.lifecycle(source_event_id)
            if lifecycle is None:
                raise RuntimeError("runtime source event not found")
            if lifecycle[0] in _TERMINAL_PHASES and not events:
                return
            await asyncio.sleep(poll_interval)

    async def forward(
        self,
        *,
        adapter: RuntimeAdapter,
        source_event_id: str,
        origin: EventOrigin,
        scope: ConversationScope,
        text: str,
        state: Mapping[str, Any] | None = None,
        target: MessageTarget | None = None,
    ) -> RuntimeResult:
        """Invoke once; duplicates, overload, and uncertain outcomes fail closed."""
        if not self._accepting:
            raise RuntimeError("runtime bridge admission is disabled")
        if origin is not EventOrigin.HUMAN:
            raise ValueError("runtime bridge accepts only strict human-origin events")
        try:
            await asyncio.wait_for(self._admission_slots.acquire(), timeout=0.01)
        except TimeoutError as exc:
            raise RuntimeError("runtime bridge admission capacity is exhausted") from exc
        try:
            return await self._forward_admitted(
                adapter=adapter,
                source_event_id=source_event_id,
                origin=origin,
                scope=scope,
                text=text,
                state=state,
                target=target,
            )
        finally:
            self._admission_slots.release()

    async def _forward_admitted(
        self,
        *,
        adapter: RuntimeAdapter,
        source_event_id: str,
        origin: EventOrigin,
        scope: ConversationScope,
        text: str,
        state: Mapping[str, Any] | None,
        target: MessageTarget | None,
    ) -> RuntimeResult:
        session_id, reserved = await self._store.reserve_source_event(
            identity=adapter.identity,
            scope=scope,
            source_event_id=source_event_id,
            max_sessions=self._max_sessions,
        )
        if not reserved:
            raise DuplicateSourceEventError("source event was already reserved; automatic replay is forbidden")
        await self._append_state(source_event_id, adapter.identity.key, "reserved")
        request = RuntimeRequest(
            source_event_id=source_event_id,
            origin=origin,
            scope=scope,
            session_id=session_id,
            text=text,
            state=state or {},
        )
        request_digest = _digest(
            {
                "source_event_id": request.source_event_id,
                "runtime_key": adapter.identity.key,
                "session_id": request.session_id,
                "text": request.text,
                "state": dict(request.state),
            },
        )
        await self._store.mark_invoking(source_event_id, request_digest)
        await self._append_state(source_event_id, adapter.identity.key, "invoking")
        try:
            async with self._invocation_slots:
                result = await adapter.invoke(request)
        except BaseException as exc:
            await asyncio.shield(self._store.mark_failed(source_event_id, _sanitized_failure(exc)))
            await asyncio.shield(self._append_state(source_event_id, adapter.identity.key, "failed"))
            raise
        response_digest = _digest({"text": result.text, "state": dict(result.state)})
        delivery_key = f"mrb-delivery-{_digest({'source_event_id': source_event_id, 'runtime_key': adapter.identity.key})}"
        await self._store.mark_response_ready(source_event_id, response_digest, result.text, delivery_key, target)
        await self._append_state(source_event_id, adapter.identity.key, "response_ready")
        return result

    async def _append_state(self, source_event_id: str, runtime_key: str, phase: str) -> None:
        await self._store.append_state(source_event_id, phase)
        if self._state_sink is not None:
            await self._state_sink(source_event_id, runtime_key, phase)


def _digest(value: object) -> str:
    return hashlib.sha256(normalized_json_bytes(value)).hexdigest()


def _sanitized_failure(exc: BaseException) -> str:
    """Retain only a stable exception class, never external stderr/message content."""
    return f"{type(exc).__module__}.{type(exc).__qualname__}"
