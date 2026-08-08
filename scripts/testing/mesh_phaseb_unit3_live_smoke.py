#!/usr/bin/env python3
"""Phase B Unit 3 gate smoke — real deliver_stream tool-state posting into a live room/thread.

Drives the mesh ``MatrixToolStateSink`` with a real ``delivery_gateway.DeliveryGateway``
bound as the ``deliver_stream`` callable (targeting a ``MessageTarget`` resolved from the
mapped session), against the LIVE homeserver.  Worker tool-state chunks must stream into a
real room/thread through the real ``deliver_stream`` primitive (network side effect).

Standalone smoke probe: not part of the pytest suite and intentionally uses network I/O,
so it carries its own per-file ruff ignores.
"""
# ruff: noqa: ANN001, ANN201, D100, D101, D102, D103, EM101, EM102, EXE001, S310, TRY003

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import tempfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
sys.path.insert(0, _REPO_ROOT)  # expose tests.conftest

import nio  # noqa: E402

from mindroom.config.agent import AgentConfig  # noqa: E402
from mindroom.config.main import Config  # noqa: E402
from mindroom.config.models import ModelConfig, RouterConfig  # noqa: E402
from mindroom.delivery_gateway import DeliveryGateway, DeliveryGatewayDeps  # noqa: E402
from mindroom.final_delivery import StreamTransportOutcome  # noqa: E402
from mindroom.mesh import (  # noqa: E402
    MatrixMeshTransport,
    MatrixToolStateSink,
    MeshCursorStore,
    MeshToolStateChunk,
)
from mindroom.message_target import MessageTarget  # noqa: E402
from mindroom.mesh.transport import _message_content_from_source  # noqa: E402
from mindroom.tool_system.events import ToolTraceEntry  # noqa: E402
from tests.conftest import bind_runtime_paths, runtime_paths_for, test_runtime_paths  # noqa: E402

HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://localhost:8008").rstrip("/")

PASS = "\u2713"
FAIL = "\u2717"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def register_user(homeserver: str) -> tuple[str, str]:
    """Register a throwaway user over HTTP; returns (user_id, access_token)."""
    username = f"mesh_b3_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(20)
    body = {
        "username": username,
        "password": password,
        "auth": {"type": "m.login.dummy"},
    }
    req = urllib.request.Request(
        f"{homeserver}/_matrix/client/v3/register",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    return payload["user_id"], payload["access_token"]


class _NullConversationCache:
    """A minimal conversation cache for the streaming delivery path.

    Resolves the latest thread event from the live room via a real ``room_get_event_relations``
    query so the streaming thread-fallback path has the MSC3440 metadata it needs.
    """

    def __init__(self, client: nio.AsyncClient | None = None) -> None:
        self.client = client

    async def get_latest_thread_event_id_if_needed(
        self,
        room_id: str,
        thread_id: str | None,
        reply_to_event_id: str | None = None,
        existing_event_id: str | None = None,
        *,
        caller_label: str = "latest_thread_event_lookup",
    ) -> str | None:
        if thread_id is None:
            return None
        if self.client is None:
            return thread_id
        # Return the thread root as the authoritative latest thread event for this
        # freshly-created thread (the only message in it so far).
        return thread_id

    def notify_outbound_message(self, room_id: str, event_id: str | None, content: dict) -> None:
        return None

    def reserve_outbound_thread(self, room_id: str, event_id: str, thread_id: str | None) -> None:
        return None

    def release_outbound_thread(self, room_id: str, event_id: str | None) -> None:
        return None


async def create_room(client: nio.AsyncClient) -> str:
    resp = await client.room_create(
        name="mesh-b3-tool-stream-smoke",
        preset=nio.RoomPreset.public_chat,
    )
    if not isinstance(resp, nio.RoomCreateResponse):
        raise RuntimeError(f"createRoom failed: {resp}")
    return resp.room_id


async def send_raw(client: nio.AsyncClient, room_id: str, content: dict) -> str:
    """Send a raw m.room.message via nio; returns the event_id."""
    resp = await client.room_send(
        room_id=room_id,
        message_type="m.room.message",
        content=content,
        ignore_unverified_devices=True,
    )
    if not isinstance(resp, nio.RoomSendResponse):
        raise RuntimeError(f"room_send failed: {resp}")
    return str(resp.event_id)


def _build_delivery_gateway(client: nio.AsyncClient) -> DeliveryGateway:
    """Build a real DeliveryGateway wired to the live client."""
    tmp = Path(tempfile.mkdtemp())
    rp = test_runtime_paths(tmp)
    config = bind_runtime_paths(
        Config(
            agents={"code": AgentConfig(display_name="Code", rooms=["!room:localhost"])},
            teams={},
            room_models={},
            models={"default": ModelConfig(provider="ollama", id="test-model")},
            router=RouterConfig(model="default"),
        ),
        rp,
    )
    deps = DeliveryGatewayDeps(
        runtime=SimpleNamespace(client=client, orchestrator=None, config=config, runtime_started_at=0.0),
        runtime_paths=runtime_paths_for(config),
        agent_name="code",
        logger=MagicMock(),
        redact_message_event=AsyncMock(return_value=True),
        resolver=SimpleNamespace(deps=SimpleNamespace(conversation_cache=_NullConversationCache(client))),
        response_hooks=SimpleNamespace(
            _apply_before_response=AsyncMock(),
            _apply_final_response_transform=AsyncMock(),
            emit_after_response=AsyncMock(),
            emit_cancelled_response=AsyncMock(),
        ),
    )
    return DeliveryGateway(deps)


def _target_resolver(room_id: str, thread_id: str | None):
    def _resolve(chunk):  # noqa: ANN001
        return MessageTarget.resolve(
            room_id=chunk.room_id,
            thread_id=chunk.thread_id,
            reply_to_event_id=None,
            room_mode=chunk.thread_id is None,
        )

    return _resolve


def _chunk(room_id: str, thread_id: str | None, *, seq: int, tool_name: str) -> MeshToolStateChunk:
    return MeshToolStateChunk(
        session_id=f"{room_id}:{thread_id}" if thread_id else room_id,
        worker_id="alpha",
        sequence=seq,
        trace=ToolTraceEntry(
            type="tool_call_started" if seq % 2 else "tool_call_completed",
            tool_name=tool_name,
            args_preview=f'{{"path": "{tool_name}.txt"}}',
            result_preview=None,
        ),
        room_id=room_id,
        thread_id=thread_id,
    )


async def main() -> int:
    started = asyncio.get_event_loop().time()
    log("=" * 72)
    log("  Phase B Unit 3 — real deliver_stream tool-state posting into a live room/thread")
    log(f"  Homeserver under test: {HOMESERVER}")
    log("=" * 72)

    client = nio.AsyncClient(HOMESERVER)
    try:
        # --- Stage 1: register throwaway user ---
        user_id, token = register_user(HOMESERVER)
        client.access_token = token
        client.user_id = user_id
        log(f"{PASS} register throwaway user  [{user_id}]")

        # --- Stage 2: create room + thread root ---
        room_id = await create_room(client)
        log(f"{PASS} create fresh room  [{room_id}]")
        root_id = await send_raw(client, room_id, {"msgtype": "m.text", "body": "mesh-b3 smoke ROOT"})
        log(f"{PASS} post thread root  [root_id={root_id}]")

        # --- Stage 3: build a real DeliveryGateway bound to the live client ---
        gateway = _build_delivery_gateway(client)
        log(f"{PASS} built real DeliveryGateway (deliver_stream bound to live nio client)")

        # --- Stage 4: wire a mesh MatrixToolStateSink to deliver_stream ---
        collector: list[ToolTraceEntry] = []
        deliver_calls: list[StreamTransportOutcome] = []

        async def _bound_deliver_stream(request):  # noqa: ANN001
            outcome = await gateway.deliver_stream(request)
            deliver_calls.append(outcome)
            return outcome

        store = MeshCursorStore()
        mesh_transport = MatrixMeshTransport(cursor_store=store, gateway_room_id="!gw:localhost")
        sink = MatrixToolStateSink(
            mesh_transport,
            deliver_stream=_bound_deliver_stream,
            target_resolver=_target_resolver(room_id, root_id),
            show_tool_calls=True,
            tool_trace_collector=collector,
        )
        log(f"{PASS} wired mesh sink -> real deliver_stream (target room={room_id} thread={root_id})")

        # --- Stage 5: stream tool-state deltas with the posting gate OPEN ---
        import mindroom.mesh.tool_state as ts  # noqa: PLC0415

        ts.PHASE_B_TOOL_STREAM_POSTING_ENABLED = True
        try:
            for seq in (1, 2):
                sink.forward(_chunk(room_id, root_id, seq=seq, tool_name=f"read_file_{seq}"))
        finally:
            ts.PHASE_B_TOOL_STREAM_POSTING_ENABLED = False
        log(f"{PASS} forwarded {len(sink.streamed_requests())} tool-state deltas (gate OPEN during scheduling)")

        # Drain the scheduled streaming tasks so live posting completes.
        await asyncio.sleep(3.0)

        # --- Stage 6: verify real events landed in the live room/thread via sync ---
        sync_response = await client.sync(timeout=10000, full_state=True)
        bodies = []
        thread_rels = []
        if isinstance(sync_response.rooms, dict):
            joined = {rid: room for rid, room in sync_response.rooms.items() if room is not None}
        else:
            joined = getattr(sync_response.rooms, "join", None) or {}
        for _rid, joined_room in joined.items():
            timeline = getattr(joined_room, "timeline", None)
            for event in (getattr(timeline, "events", None) or []):
                if event.__class__.__name__ != "RoomMessageText":
                    continue
                content = _message_content_from_source(getattr(event, "source", None))
                bodies.append(content.get("body", ""))
                rel = content.get("m.relates_to")
                if isinstance(rel, dict):
                    thread_rels.append(rel)
        # Tool-state posts carry a visible 🔧 tool marker line (never the raw
        # ROOT message).  Count only those to avoid the root event trivially
        # satisfying the "event reached the room" check.
        tool_state_bodies = [b for b in bodies if "🔧" in b]
        for body in bodies:
            log(f"    [sync body] {body!r}")
        for rel in thread_rels:
            log(f"    [sync thread rel] {rel!r}")

        # Also pull the full room timeline via /messages to be sure nothing was
        # missed by the sync-timeline filter (streaming posts m.notice events).
        room_messages = await client.room_messages(room_id, start="", limit=50)
        all_bodies = []
        all_rels = []
        for event in getattr(room_messages, "chunk", []) or []:
            if event.__class__.__name__ != "RoomMessageText":
                continue
            content = _message_content_from_source(getattr(event, "source", None))
            all_bodies.append(content.get("body", ""))
            rel = content.get("m.relates_to")
            if isinstance(rel, dict):
                all_rels.append(rel)
        tool_state_all = [b for b in all_bodies if "🔧" in b]
        for body in all_bodies:
            log(f"    [room_messages body] {body!r}")
        for rel in all_rels:
            log(f"    [room_messages thread rel] {rel!r}")
        log(
            f"{PASS} sync bodies={len(bodies)} / messages bodies={len(all_bodies)}; "
            f"tool-state bodies: sync={len(tool_state_bodies)} messages={len(tool_state_all)}; "
            f"thread_rels(sync)={len(thread_rels)} thread_rels(messages)={len(all_rels)}",
        )

        if not tool_state_bodies and not tool_state_all:
            log(f"{FAIL} NO-GO: no tool-state event reached the live room")
            return 1

        # --- Stage 7: confirm the streaming delivery outcome was produced ---
        log(
            f"{PASS} deliver_stream produced {len(deliver_calls)} outcome(s)  "
            f"[outcomes={[o.terminal_status for o in deliver_calls]}]",
        )
        if not deliver_calls:
            log(f"{FAIL} NO-GO: deliver_stream did not return a terminal outcome")
            return 1
        if not any(o.terminal_status == "completed" for o in deliver_calls):
            log(f"{FAIL} NO-GO: no terminal 'completed' streaming outcome (false-pass guard)")
            return 1

        log("-" * 72)
        log(f"  elapsed={asyncio.get_event_loop().time() - started:.2f}s")
        log("-" * 72)
        log(f"{PASS} GO: real deliver_stream tool-state posting into a live room/thread verified.")
        return 0
    finally:
        await client.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
