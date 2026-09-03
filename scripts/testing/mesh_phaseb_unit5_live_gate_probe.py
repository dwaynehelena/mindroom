#!/usr/bin/env python3
"""Phase B Unit 5 gate probe — real coordinator-level sync-token replay.

Registers a throwaway Matrix user against the LIVE homeserver, creates a fresh
room, drives a ``MatrixMeshTransport`` with a real ``nio.AsyncClient`` injected
to deliver a mesh entry, then runs ``MeshReconnectCoordinator.resume`` with a
real sync token (``next_batch`` captured before delivery) as the saved cursor.

Verifies that coordinator-level replay:
  1. resumes from the real Matrix sync token (``cursor.cursor`` as ``since``),
  2. reconstructs the undelivered ``MeshOutboxEntry`` from the live wire envelope,
  3. persists the real sync ``next_batch`` as the advanced resume cursor,
  4. is idempotent (a second resume from the advanced cursor replays nothing).

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

from mindroom.mesh import (  # noqa: E402
    MatrixMeshTransport,
    MeshCursorStore,
    MeshMessage,
    MeshReconnectCoordinator,
)
from mindroom.mesh.cursor import MeshReconnectCursor  # noqa: E402
from mindroom.mesh.models import MeshOutboxEntry  # noqa: E402

HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://localhost:8008").rstrip("/")

PASS = "\u2713"
FAIL = "\u2717"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def register_user(homeserver: str) -> tuple[str, str]:
    """Register a throwaway user over HTTP; returns (user_id, access_token)."""
    username = f"mesh_b5_{secrets.token_hex(4)}"
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
        name="mesh-b5-resume-replay-smoke",
        preset=nio.RoomPreset.public_chat,
    )
    if not isinstance(resp, nio.RoomCreateResponse):
        raise RuntimeError(f"createRoom failed: {resp}")
    return resp.room_id


async def main() -> int:
    started = asyncio.get_event_loop().time()
    log("=" * 72)
    log("  Phase B Unit 5 gate probe — real coordinator-level sync-token replay")
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

        # --- Stage 3: capture a real sync token BEFORE any mesh delivery ---
        pre_sync = await client.sync(timeout=5000)
        pre_delivery_token = getattr(pre_sync, "next_batch", None)
        if not isinstance(pre_delivery_token, str) or not pre_delivery_token:
            log(f"{FAIL} NO-GO: initial sync did not return a real next_batch")
            return 1
        log(f"{PASS} captured real sync token before delivery  [next_batch={pre_delivery_token[:24]}...]")

        # --- Stage 4: drive the injected transport to deliver a mesh entry ---
        cursor_store = MeshCursorStore()
        transport = MatrixMeshTransport(
            cursor_store=cursor_store,
            gateway_room_id="!mesh-gateway:localhost",
            client=client,
        )
        entry = MeshOutboxEntry(
            outbox_id="mesh-outbox-b5-smoke",
            message_id="mesh-msg-b5-smoke",
            source_worker_id="alpha",
            target_worker_id="beta",
            source_room_id=room_id,
            target_room_id=room_id,
            gateway_room_id="!mesh-gateway:localhost",
            target_thread_id=None,
            target_session_id=None,
        )
        message = MeshMessage(
            source_worker_id="alpha",
            target_worker_id="beta",
            content="mesh-b5 smoke resume replay through injected nio client",
            correlation_id="corr-b5",
        )
        status = await transport.deliver(entry, message)
        log(f"{PASS} injected transport deliver status={status}")
        if status != "delivered":
            log(f"{FAIL} NO-GO: injected deliver did not reach 'delivered'")
            return 1

        # --- Stage 5: coordinator-level real sync-token replay ---
        # The cursor is the REAL sync token captured BEFORE delivery: replaying
        # from it must include the undelivered mesh entry.
        cursor_store.save(
            MeshReconnectCursor(
                worker_id="beta",
                cursor=pre_delivery_token,
                cache_generation=pre_delivery_token,
            ),
        )
        coordinator = MeshReconnectCoordinator(
            transport=transport,
            cursor_store=cursor_store,
            lifecycle_sink=[],
            enabled=True,
        )
        result = await coordinator.resume("beta")
        log(f"{PASS} coordinator resume ran  [replayed={result.replayed_outbox_ids} resumed={result.resumed}]")
        if not result.resumed:
            log(f"{FAIL} NO-GO: resume did not replay the undelivered mesh entry")
            return 1
        if "mesh-outbox-b5-smoke" not in result.replayed_outbox_ids:
            log(f"{FAIL} NO-GO: replayed outbox ids do not include the mesh delivery")
            return 1

        # --- Stage 6: advanced cursor is a real sync next_batch ---
        saved = cursor_store.load("beta")
        if saved is None:
            log(f"{FAIL} NO-GO: no advanced cursor saved")
            return 1
        advanced_ok = (
            isinstance(saved.cursor, str)
            and bool(saved.cursor)
            and "mesh-cursor-" not in saved.cursor
        )
        log(f"{PASS} advanced cursor is a real sync token  [ok={advanced_ok} cursor={saved.cursor[:24]}...]")
        if not advanced_ok:
            log(f"{FAIL} NO-GO: advanced cursor is not a real sync token")
            return 1

        # --- Stage 7: idempotency — a second resume from the advanced cursor ---
        second = await coordinator.resume("beta")
        log(f"{PASS} second resume (from advanced token)  [replayed={second.replayed_outbox_ids} resumed={second.resumed}]")
        if second.replayed_outbox_ids or second.resumed:
            log(f"{FAIL} NO-GO: repeated resume re-delivered (not idempotent / duplicate delivery)")
            return 1
        log(f"{PASS} idempotent — no duplicate delivery, no full replay")
    finally:
        await client.close()

    elapsed = asyncio.get_event_loop().time() - started
    log("-" * 72)
    log(f"  elapsed={elapsed:.2f}s")
    log("-" * 72)
    log(f"{PASS} GO: real coordinator-level sync-token replay verified against live homeserver.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))