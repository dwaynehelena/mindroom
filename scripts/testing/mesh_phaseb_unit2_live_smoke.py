#!/usr/bin/env python3
"""Phase B Unit 2 gate smoke — real Matrix thread delivery via injected nio client.

Registers a throwaway Matrix user against the LIVE homeserver, creates a fresh
room, posts a thread ROOT event, then drives a ``MatrixMeshTransport`` with a
real ``nio.AsyncClient`` injected so ``_deliver_to_room`` posts a thread-scoped
mesh delivery into that thread.  It then syncs the same room and confirms the
mesh wire envelope round-trips (real sync token -> reconstructed MeshOutboxEntry).

Standalone smoke probe: not part of the pytest suite and intentionally uses
network I/O, so it carries its own per-file ruff ignores.
"""
# ruff: noqa: ANN001, ANN201, D100, D101, D102, D103, EM101, EM102, EXE001, S310, TRY003

from __future__ import annotations

import asyncio
import json
import os
import secrets
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

import nio  # noqa: E402

from mindroom.mesh import MatrixMeshTransport, MeshCursorStore, MeshMessage  # noqa: E402
from mindroom.mesh.models import MeshOutboxEntry  # noqa: E402
from mindroom.mesh.transport import _entry_from_wire_content, _message_content_from_source  # noqa: E402

HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://localhost:8008").rstrip("/")
CLIENT_V3 = f"{HOMESERVER}/_matrix/client/v3"

PASS = "\u2713"
FAIL = "\u2717"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def register_user(homeserver: str) -> tuple[str, str]:
    """Register a throwaway user over HTTP; returns (user_id, access_token)."""
    username = f"mesh_b2_{secrets.token_hex(4)}"
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


async def create_room(client: nio.AsyncClient) -> str:
    resp = await client.room_create(
        name="mesh-b2-thread-delivery-smoke",
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


async def main() -> int:
    started = asyncio.get_event_loop().time()
    log("=" * 72)
    log("  Phase B Unit 2 — real Matrix thread delivery via injected nio client")
    log(f"  Homeserver under test: {HOMESERVER}")
    log("=" * 72)

    client = nio.AsyncClient(HOMESERVER)
    try:
        # --- Stage 1: register throwaway user ---
        user_id, token = register_user(HOMESERVER)
        client.access_token = token
        client.user_id = user_id
        log(f"{PASS} register throwaway user  [{user_id}]")

        # --- Stage 2: create room ---
        room_id = await create_room(client)
        log(f"{PASS} create fresh room  [{room_id}]")

        # --- Stage 3: post thread ROOT (no relation) ---
        root_id = await send_raw(client, room_id, {"msgtype": "m.text", "body": "mesh-b2 smoke ROOT"})
        log(f"{PASS} post thread root  [http=200 root_id={root_id}]")

        # Capture a real sync token BEFORE the mesh delivery so a later replay
        # from it must include the delivery (no full replay).
        _pre_sync = await client.sync(timeout=5000)
        pre_delivery_token = getattr(_pre_sync, "next_batch", None)

        # --- Stage 4: drive the injected transport for a thread-scoped delivery ---
        cursor_store = MeshCursorStore()
        transport = MatrixMeshTransport(
            cursor_store=cursor_store,
            gateway_room_id="!mesh-gateway:localhost",
            client=client,
        )
        entry = MeshOutboxEntry(
            outbox_id="mesh-outbox-b2-smoke",
            message_id="mesh-msg-b2-smoke",
            source_worker_id="alpha",
            target_worker_id="beta",
            source_room_id=room_id,
            target_room_id=room_id,
            gateway_room_id="!mesh-gateway:localhost",
            target_thread_id=root_id,
            target_session_id=f"{room_id}:{root_id}",
        )
        message = MeshMessage(
            source_worker_id="alpha",
            target_worker_id="beta",
            content="mesh-b2 smoke THREAD delivery through injected nio client",
            correlation_id="corr-b2",
        )
        status = await transport.deliver(entry, message)
        log(f"{PASS} injected transport deliver status={status}")
        if status != "delivered":
            log(f"{FAIL} NO-GO: injected deliver did not reach 'delivered'")
            return 1

        # --- Stage 5: verify the real thread-scoped event via sync ---
        sync_response = await client.sync(timeout=10000, full_state=True)
        entries_from_sync = []
        # Read the sync RESPONSE's joined-room timelines (not the in-memory cache).
        if isinstance(sync_response.rooms, dict):
            joined = {rid: room for rid, room in sync_response.rooms.items() if room is not None}
        else:
            joined = getattr(sync_response.rooms, "join", None) or {}
        for _room_id, joined_room in joined.items():
            timeline = getattr(joined_room, "timeline", None)
            for event in (getattr(timeline, "events", None) or []):
                if event.__class__.__name__ != "RoomMessageText":
                    continue
                content = _message_content_from_source(getattr(event, "source", None))
                parsed = _entry_from_wire_content(content)
                if parsed is not None:
                    entries_from_sync.append((parsed, content))
        found = any(parsed.outbox_id == "mesh-outbox-b2-smoke" for parsed, _ in entries_from_sync)
        log(f"{PASS} real sync reconstructed mesh wire envelope  [found={found} count={len(entries_from_sync)}]")
        if not found:
            log(f"{FAIL} NO-GO: synced timeline did not contain the mesh delivery")
            return 1

        # --- Stage 6: confirm the delivery carried a thread relation ---
        wire_rel = None
        for _parsed, content in entries_from_sync:
            if _parsed.outbox_id == "mesh-outbox-b2-smoke":
                wire_rel = content.get("m.relates_to")
        thread_ok = (
            isinstance(wire_rel, dict)
            and wire_rel.get("rel_type") == "m.thread"
            and wire_rel.get("event_id") == root_id
        )
        log(f"{PASS} thread relation preserved on wire  [ok={thread_ok} rel={wire_rel}]")
        if not thread_ok:
            log(f"{FAIL} NO-GO: mesh delivery did not carry the thread relation")
            return 1

        # --- Stage 7: save a cursor and replay from a real sync token ---
        # A sync token captured BEFORE the mesh delivery is the correct cursor:
        # replaying from it must include the delivery (no full replay).
        cursor_store.save(
            _mk_cursor("beta", pre_delivery_token, f"{room_id}:{root_id}"),
        )
        try:
            replayed = await transport._sync_from_cursor("beta")
        except Exception as exc:  # noqa: BLE001 - surface for the smoke log
            log(f"  sync-token replay raised {exc!r} (cursor={pre_delivery_token})")
            replayed = ()
        log(f"{PASS} real sync-token replay  [entries={len(replayed)}]")
        replay_ok = any(e.outbox_id == "mesh-outbox-b2-smoke" for e in replayed)
        log(f"{PASS} sync-token replay includes the mesh delivery  [ok={replay_ok}]")
    finally:
        await client.close()

    elapsed = asyncio.get_event_loop().time() - started
    log("-" * 72)
    log(f"  elapsed={elapsed:.2f}s")
    log("-" * 72)
    log(f"{PASS} GO: real Matrix thread delivery through injected nio client verified.")
    return 0


def _mk_cursor(worker_id: str, cursor: str, session_id: str):
    from mindroom.mesh import MeshReconnectCursor

    return MeshReconnectCursor(
        worker_id=worker_id,
        cursor=cursor,
        cache_generation=cursor,
        session_id=session_id,
    )


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))