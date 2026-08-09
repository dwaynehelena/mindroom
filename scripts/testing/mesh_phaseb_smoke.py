#!/usr/bin/env python3
"""Phase B Gate 2 — thread-scoped reply smoke probe.

Exercises the in-thread, thread-scoped Matrix delivery path (``m.relates_to``
with ``rel_type == "m.thread"``) against the local Synapse homeserver. This is
the DISTINCT thread-reply leg of Gate 2 — it is not the generic root send.

Stages
------
1. Register a throwaway Matrix user (open registration).
2. Create a fresh room.
3. Post a thread ROOT event (no relation).
4. Post a thread-scoped REPLY event whose ``m.relates_to`` points at the root
   with ``rel_type == "m.thread"`` and ``event_id`` = root event ID.
5. Confirm the homeserver returns HTTP 200 and returns an event ID for the reply.
6. Verify thread membership via the stable MSC3440 relations API:
   ``/rooms/{roomId}/relations/{rootId}/m.thread`` must contain the reply event.
7. Emit GO / NO-GO for the thread-reply leg.

Environment
-----------
MATRIX_HOMESERVER  (default http://localhost:8008)
No pre-existing account or state required; everything is disposable.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import time

import httpx

HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://localhost:8008").rstrip("/")
CLIENT_V3 = f"{HOMESERVER}/_matrix/client/v3"

PASS = "\u2713"
FAIL = "\u2717"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def _check(name: str, ok: bool, detail: str = "") -> None:
    mark = PASS if ok else FAIL
    suffix = f"  [{detail}]" if detail else ""
    log(f"{mark} {name}{suffix}")


async def register(http: httpx.AsyncClient) -> tuple[str, str]:
    """Register a throwaway user; returns (user_id, access_token)."""
    username = f"gate2_thread_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(20)
    body = {
        "username": username,
        "password": password,
        "auth": {"type": "m.login.dummy"},
    }
    resp = await http.post(f"{CLIENT_V3}/register", json=body)
    if resp.status_code != 200:
        raise RuntimeError(f"register failed status={resp.status_code} body={resp.text}")
    data = resp.json()
    return data["user_id"], data["access_token"]


async def create_room(http: httpx.AsyncClient, token: str) -> str:
    resp = await http.post(
        f"{CLIENT_V3}/createRoom",
        headers={"Authorization": f"Bearer {token}"},
        json={"name": "gate2-thread-reply-smoke", "preset": "public_chat"},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"createRoom failed status={resp.status_code} body={resp.text}")
    return resp.json()["room_id"]


async def send_message(http: httpx.AsyncClient, token: str, room_id: str, body: str, relates_to=None):
    """Send one m.room.message event; returns (status_code, event_id, content_sent)."""
    content: dict = {"msgtype": "m.text", "body": body}
    if relates_to is not None:
        content["m.relates_to"] = relates_to
    txn = secrets.token_hex(8)
    url = f"{CLIENT_V3}/rooms/{room_id}/send/m.room.message/{txn}"
    resp = await http.put(url, headers={"Authorization": f"Bearer {token}"}, json=content)
    event_id = None
    if resp.status_code == 200:
        event_id = resp.json().get("event_id")
    return resp.status_code, event_id, content


async def get_event(http: httpx.AsyncClient, token: str, room_id: str, event_id: str):
    resp = await http.get(
        f"{CLIENT_V3}/rooms/{room_id}/event/{event_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    if resp.status_code != 200:
        return None, resp.status_code, resp.text
    return resp.json(), resp.status_code, ""


async def get_thread_relations(http: httpx.AsyncClient, token: str, room_id: str, root_id: str):
    """Query stable MSC3440 thread relations aggregation endpoint.

    Synapse exposes the relations aggregation under ``/_matrix/client/v1``
    (the ``/v3`` alias returns ``M_UNRECOGNIZED``). Use ``/v1`` which is the
    canonical path for MSC3440 / MSC2674 relations.
    """
    url = f"{HOMESERVER}/_matrix/client/v1/rooms/{room_id}/relations/{root_id}/m.thread"
    resp = await http.get(url, headers={"Authorization": f"Bearer {token}"})
    if resp.status_code != 200:
        return None, resp.status_code, resp.text
    return resp.json(), resp.status_code, ""


async def main() -> int:
    started = time.time()
    log("=" * 72)
    log("  Phase B Gate 2 — THREAD-REPLY smoke probe")
    log(f"  Homeserver under test: {HOMESERVER}")
    log("=" * 72)

    async with httpx.AsyncClient(timeout=30.0) as http:
        # --- Stage 1: register disposable user ---
        user_id, token = await register(http)
        _check("register throwaway user", True, user_id)

        # --- Stage 2: create room ---
        room_id = await create_room(http, token)
        _check("create fresh room", True, room_id)

        # --- Stage 3: post thread root (no relation) ---
        root_status, root_id, root_content = await send_message(
            http, token, room_id, "gate2 thread-reply smoke ROOT event"
        )
        root_ok = root_status == 200 and bool(root_id)
        _check("post thread root event", root_ok, f"http={root_status} root_id={root_id}")
        if not root_ok:
            log(f"{FAIL} NO-GO: cannot establish thread root; thread-reply leg aborted")
            return 1

        # --- Stage 4: post thread-scoped REPLY with correct m.relates_to ---
        relates_to = {"rel_type": "m.thread", "event_id": root_id}
        reply_status, reply_id, reply_content = await send_message(
            http, token, room_id, "gate2 thread-scoped REPLY event", relates_to=relates_to
        )
        reply_ok = reply_status == 200 and bool(reply_id)
        _check(
            "post thread-scoped reply (HTTP 200 + event_id)",
            reply_ok,
            f"http={reply_status} reply_id={reply_id}",
        )
        if not reply_ok:
            log(f"{FAIL} NO-GO: thread reply rejected by homeserver (status={reply_status})")
            return 1

        # --- Stage 5: confirm the reply event exists and carries the thread relation ---
        reply_event, st, _ = await get_event(http, token, room_id, reply_id)
        event_ok = reply_event is not None and reply_event.get("event_id") == reply_id
        content_ok = event_ok
        wire_rel = None
        if reply_event is not None:
            wire_rel = reply_event.get("content", {}).get("m.relates_to")
            content_ok = (
                isinstance(wire_rel, dict)
                and wire_rel.get("rel_type") == "m.thread"
                and wire_rel.get("event_id") == root_id
            )
        _check("reply event readable from homeserver", event_ok, f"st={st}")
        _check(
            "reply carries correct m.relates_to (m.thread -> root)",
            content_ok,
            f"wire_rel={wire_rel}",
        )

        # --- Stage 6: verify thread membership via relations API ---
        relations, rst, _ = await get_thread_relations(http, token, room_id, root_id)
        members = []
        if relations is not None:
            chunk = relations.get("chunk") or []
            members = [ev.get("event_id") for ev in chunk if ev.get("event_id")]
        membership_ok = reply_id in members
        _check(
            "reply associated with thread root via relations API",
            membership_ok,
            f"http={rst} members={members}",
        )
        # Root must NOT appear as its own thread child.
        root_not_child = root_id not in members
        _check("thread root is not a child of itself", root_not_child, f"root_in_members={root_id in members}")

    elapsed = time.time() - started
    passed = (
        root_ok
        and reply_ok
        and event_ok
        and content_ok
        and membership_ok
        and root_not_child
    )
    log("-" * 72)
    log(f"  elapsed={elapsed:.2f}s  thread-root={root_id}")
    log(f"  thread-reply event_id={reply_id}")
    log("-" * 72)
    if passed:
        log(f"{PASS} GO: thread-reply leg of Gate 2 passed (HTTP 200, event created, "
            f"thread membership verified).")
        return 0
    log(f"{FAIL} NO-GO: thread-reply leg of Gate 2 failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))