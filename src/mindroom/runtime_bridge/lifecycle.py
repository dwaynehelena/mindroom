"""Atomic per-bot external-runtime lifecycle, readiness, and recovery."""
# ruff: noqa: ANN401, D102, D204, E701, E702, EM101, TRY003
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from mindroom.flight_recorder import record_flight_event
from mindroom.handled_turns import TurnRecord
from mindroom.matrix.client_delivery import cached_room

from .hermes import HermesAdapter
from .integration import MatrixRuntimeBridge, RoomEncryptionState, RuntimeDelivery
from .openclaw import OpenClawAdapter
from .service import RuntimeBridgeService
from .store import RuntimeBridgeStore

if TYPE_CHECKING:
    from pathlib import Path

    from mindroom.config.external_runtime import ExternalRuntimeInstanceConfig
    from mindroom.config.main import Config
    from mindroom.constants import RuntimePaths
    from mindroom.delivery_gateway import DeliveryGateway
    from mindroom.hooks import MessageEnvelope
    from mindroom.turn_store import TurnStore

@dataclass(slots=True)
class _BoundRuntime:
    adapter: Any
    bridge: MatrixRuntimeBridge
    store: RuntimeBridgeStore

@dataclass(slots=True)
class _Generation:
    bound: dict[str, _BoundRuntime]
    stack: AsyncExitStack

class RuntimeBridgeLifecycle:
    """Own one atomically replaceable runtime generation."""
    def __init__(self, *, agent_name: str, storage_path: Path, gateway: DeliveryGateway,
                 runtime: Any, runtime_paths: RuntimePaths, turn_store: TurnStore | None = None) -> None:
        self._agent_name, self._storage_path = agent_name, storage_path
        self._gateway, self._runtime, self._turn_store = gateway, runtime, turn_store
        self._runtime_paths = runtime_paths
        self._generation: _Generation | None = None
        self._ready = False
        self._initial_sync_authoritative = False
        self._lock = asyncio.Lock()

    @property
    def ready(self) -> bool:
        generation = self._generation
        return bool(self._ready and generation and all(x.bridge.ready for x in generation.bound.values()))

    async def start(self, config: Config) -> None:
        await self.reload(config)

    async def reload(self, config: Config) -> None:
        """Build/preflight a complete candidate, then atomically swap generations."""
        async with self._lock:
            candidate = await self._build(config)
            old = self._generation
            self._generation = candidate
            self._ready = self._initial_sync_authoritative or not candidate.bound
            if old is not None:
                for item in old.bound.values(): item.bridge.disable()
                await old.stack.aclose()

    async def _build(self, config: Config) -> _Generation:
        stack = AsyncExitStack()
        await stack.__aenter__()
        opened: dict[str, _BoundRuntime] = {}
        try:
            candidates = {n: i for n, i in config.external_runtimes.instances.items()
                          if i.enabled and i.agent_name == self._agent_name}
            for name, instance in candidates.items():
                store = RuntimeBridgeStore(self._storage_path / "runtime_bridge" / f"{name}.sqlite3")
                await store.open(); stack.push_async_callback(store.close)
                adapter = _build_adapter(name, instance); stack.push_async_callback(adapter.close)
                await adapter.preflight()
                service = RuntimeBridgeService(store, max_concurrency=instance.max_concurrency,
                    max_waiters=instance.max_waiters, max_sessions=instance.max_sessions,
                    state_sink=self._record_runtime_state)
                bridge = MatrixRuntimeBridge(service, store, self._gateway,
                    owner_user_ids=instance.owner_user_ids, room_ids=instance.room_ids,
                    require_e2ee=instance.require_e2ee, room_encryption_state=self._room_encryption_state)
                opened[name] = _BoundRuntime(adapter, bridge, store)
                if self._initial_sync_authoritative:
                    await bridge.recover()
                    await self._repair_handled_turns(store)
            return _Generation(opened, stack)
        except BaseException:
            await stack.aclose()
            raise

    async def initial_sync_ready(self) -> None:
        """Publish authoritative room state, then recover outboxes and handled turns."""
        async with self._lock:
            self._initial_sync_authoritative = True
            generation = self._generation
            if generation is None: return
            for item in generation.bound.values():
                await item.bridge.recover()
                await self._repair_handled_turns(item.store)
            self._ready = True

    async def _repair_handled_turns(self, store: RuntimeBridgeStore) -> None:
        """Public-lifecycle callback: idempotently close delivery/ledger crash windows."""
        if self._turn_store is None:
            return
        for row in await store.delivered_records():
            self._turn_store.record_turn(TurnRecord.create(
                [row.source_event_id], response_event_id=row.delivery_event_id,
                conversation_target=row.target))

    async def close(self) -> None:
        async with self._lock:
            self._ready = False
            old, self._generation = self._generation, None
            if old is not None:
                for item in old.bound.values(): item.bridge.disable()
                await old.stack.aclose()

    async def maybe_forward(self, *, envelope: MessageEnvelope) -> RuntimeDelivery | None:
        async with self._lock:
            if not self.ready: return None
            generation = self._generation
            assert generation is not None
            matches = [x for x in generation.bound.values() if x.bridge.allows_room(envelope.target.room_id)]
            if not matches: return None
            if len(matches) != 1: raise RuntimeError("runtime bridge room maps to multiple enabled instances")
            return await matches[0].bridge.forward(envelope=envelope, adapter=matches[0].adapter)

    async def _record_runtime_state(self, source_event_id: str, runtime_key: str, phase: str) -> None:
        """Append content-free handoff state to the shared tamper-evident ledger."""
        await record_flight_event(
            self._runtime_paths,
            run_id=source_event_id,
            kind="handoff" if phase == "reserved" else "runtime_state",
            payload={"phase": phase, "runtime_key": runtime_key},
            side_effect=False,
        )

    def _room_encryption_state(self, room_id: str) -> RoomEncryptionState:
        if not self._initial_sync_authoritative: return RoomEncryptionState.UNKNOWN
        client = self._runtime.client
        room = cached_room(client, room_id) if client is not None else None
        if room is None: return RoomEncryptionState.UNKNOWN
        return RoomEncryptionState.ENCRYPTED if room.encrypted else RoomEncryptionState.UNENCRYPTED

def _build_adapter(name: str, instance: ExternalRuntimeInstanceConfig) -> Any:
    if instance.runtime == "openclaw":
        return OpenClawAdapter(instance=name, executable=instance.executable or "",
            executable_allowlist=instance.executable_allowlist, agent_id=instance.agent_id,
            timeout_seconds=instance.timeout_seconds, executable_sha256=instance.executable_sha256,
            expected_version=instance.expected_version, expected_node_version=instance.expected_node_version,
            deny_all_attestation=instance.deny_all_attestation)
    return HermesAdapter(instance=name, endpoint=instance.endpoint,
        endpoint_allowlist=instance.endpoint_allowlist, approved_port=instance.approved_port,
        api_key_env=instance.api_key_env, api_key_file=instance.api_key_file,
        secret_root=instance.secret_root, timeout_seconds=instance.timeout_seconds,
        expected_version=instance.expected_version)
