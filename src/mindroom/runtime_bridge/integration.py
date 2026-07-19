"""Production composition after TurnPolicy selected a concrete response."""
# ruff: noqa: D101, D102, EM101, EM102, TC001, TRY003

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from mindroom.delivery_gateway import SendTextRequest
from mindroom.turn_origin import SenderKind, TurnIntent, TurnTrust

from .adapter import RuntimeAdapter
from .models import ConversationScope, EventOrigin
from .service import RuntimeBridgeService
from .store import DeliveryRecord, RuntimeBridgeStore

if TYPE_CHECKING:
    from collections.abc import Callable

    from mindroom.hooks import MessageEnvelope


class RoomEncryptionState(StrEnum):
    UNKNOWN = "unknown"
    ENCRYPTED = "encrypted"
    UNENCRYPTED = "unencrypted"


class _Gateway(Protocol):
    async def send_text(self, request: SendTextRequest) -> str | None: ...


@dataclass(frozen=True, slots=True)
class RuntimeDelivery:
    """Content-free evidence for one settled runtime response."""

    source_event_id: str
    delivery_key: str
    matrix_event_id: str


class MatrixRuntimeBridge:
    """Owner/room-authorized, E2EE-required runtime composition and recovery."""

    def __init__(
        self,
        service: RuntimeBridgeService,
        store: RuntimeBridgeStore,
        gateway: _Gateway,
        *,
        owner_user_ids: tuple[str, ...],
        room_ids: tuple[str, ...],
        require_e2ee: bool = True,
        room_encryption_state: Callable[[str], RoomEncryptionState] | None = None,
    ) -> None:
        if not owner_user_ids or not room_ids:
            raise ValueError("runtime bridge requires non-empty owner and room allowlists")
        self._service = service
        self._store = store
        self._gateway = gateway
        self._owners = frozenset(owner_user_ids)
        self._rooms = frozenset(room_ids)
        self._require_e2ee = require_e2ee
        self._room_encryption_state = room_encryption_state

    @property
    def ready(self) -> bool:
        """Readiness/kill switch; E2EE state is also checked per selected turn."""
        return self._service.ready

    def disable(self) -> None:
        self._service.disable()

    def allows_room(self, room_id: str) -> bool:
        """Return whether this configured bridge owns the canonical room ID."""
        return room_id in self._rooms

    async def forward(self, *, envelope: MessageEnvelope, adapter: RuntimeAdapter) -> RuntimeDelivery:
        """Forward only after normal policy selected this response seam."""
        _assert_human_ingress(envelope)
        requester = envelope.origin.requester_id
        if requester not in self._owners or envelope.target.room_id not in self._rooms:
            raise PermissionError("runtime bridge owner/room authorization denied")
        self._assert_e2ee(envelope.target.room_id)
        await self._service.forward(
            adapter=adapter,
            source_event_id=envelope.source_event_id,
            origin=EventOrigin.HUMAN,
            scope=ConversationScope(envelope.target.room_id, envelope.target.resolved_thread_id),
            text=envelope.body,
            target=envelope.target,
        )
        rows = {row.source_event_id: row for row in await self._store.recover_delivery_queue()}
        record = rows.get(envelope.source_event_id)
        if record is None:
            raise RuntimeError("runtime response is not delivery-ready")
        return await self._deliver(record)

    async def recover(self) -> tuple[RuntimeDelivery, ...]:
        """Safely reconcile ready/uncertain sends without invoking a runtime."""
        delivered: list[RuntimeDelivery] = []
        for record in await self._store.recover_delivery_queue():
            self._assert_e2ee(record.target.room_id)
            delivered.append(await self._deliver(record))
        return tuple(delivered)

    async def _deliver(self, record: DeliveryRecord) -> RuntimeDelivery:
        if record.status == "response_ready":
            await self._store.mark_delivering(record.source_event_id)
        event_id = await self._gateway.send_text(
            SendTextRequest(
                target=record.target,
                response_text=record.response_text,
                skip_mentions=True,
                transaction_id=record.transaction_id,
                extra_content={
                    "org.mindroom.runtime_bridge": True,
                    "org.mindroom.delivery_id": record.transaction_id,
                },
            ),
        )
        if event_id is None:
            # Keep uncertain sends in delivering. A stable Matrix txn retry is
            # idempotent; converting this to failed would lose reconciliation.
            raise RuntimeError("native Matrix delivery returned no event ID")
        await self._store.mark_delivered(record.source_event_id, event_id)
        return RuntimeDelivery(record.source_event_id, record.transaction_id, event_id)

    def _assert_e2ee(self, room_id: str) -> None:
        if not self._require_e2ee:
            return
        state = self._room_encryption_state(room_id) if self._room_encryption_state else RoomEncryptionState.UNKNOWN
        if state is not RoomEncryptionState.ENCRYPTED:
            raise RuntimeError(f"runtime delivery requires confirmed native Matrix E2EE (state={state})")


def _assert_human_ingress(envelope: MessageEnvelope) -> None:
    """Reject bots, runtimes, systems, and relays before owner authorization."""
    origin = envelope.origin
    if origin.sender_kind is not SenderKind.USER or origin.requester_kind is not SenderKind.USER:
        raise ValueError("external runtime ingress rejects managed bot/runtime origins")
    if origin.trust is not TurnTrust.EXTERNAL or origin.intent is not TurnIntent.USER_MESSAGE:
        raise ValueError("external runtime ingress rejects relayed or system origins")
