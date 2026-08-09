#!/usr/bin/env python3
"""P4 LIVE DEMO — live external placement over the real mesh transport.

P1 transport is ENABLED (4 live Phase B gates passed).  This demo places
three external items over the REAL mesh and real transport, producing a
demo receipt that captures each live placement (source, target, transport
status, round-trip latency) plus the 4 Phase B gate results.

Placements
----------
1. External HERMES message  -> runtime=hermes edge-fleet placement over the
   live MindRoom API (:8765) -> /api/edge-fleet-enroll + job lease/complete
   round-trip (this is the exact surface the P9 edge-fleet demo drives).
2. External OPENCLAW task    -> runtime=openclaw edge-fleet placement over the
   live MindRoom API (:8765) -> /api/edge-fleet-enroll + job lease/complete
   round-trip (the surface an OpenClaw ``EdgeNodeClient.enroll`` would call).
3. MINDROOM room placement   -> real Matrix thread delivery over the live
   Synapse homeserver (:8008) via an injected ``nio.AsyncClient`` into a real
   room/thread (Phase B Unit 2/3/5 mechanism), verified by a real sync.

Phase B gate results: re-runs the 4 live gate probes (Units 1, 2, 3, 5) and
captures their verdicts (LIVE_HANDSHAKE_PASSED / GO).

Blocked items are marked BLOCKED with evidence and the demo continues.

Standalone probe: not part of the pytest suite; intentionally uses network I/O.
"""
# ruff: noqa: ANN001, ANN201, D100, D101, D102, D103, EM101, EM102, EXE001, S310, TRY003

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "src"))
sys.path.insert(0, _REPO_ROOT)

import nio  # noqa: E402

from mindroom.edge_fleet import EnrollmentAuthority  # noqa: E402
from mindroom.edge_node import EdgeNodeClient, EdgeNodeIdentity  # noqa: E402

HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://localhost:8008").rstrip("/")
MINDROOM_BASE = os.environ.get("MINDROOM_API_BASE", "http://localhost:8765").rstrip("/")
ADMIN_KEY = os.environ.get(
    "MINDROOM_ADMIN_TOKEN",
    "u02UPriNVXGesySJtoGGy46C7-H9w6RG_1x6w5ADJUQ",
)

PASS = "\u2713"
FAIL = "\u2717"
BLOCKED = "\u26d4"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Phase B gate result collection
# ---------------------------------------------------------------------------

GATE_RESULTS: dict[str, dict] = {}


def record_gate(unit: str, name: str, verdict: str, evidence: str) -> None:
    GATE_RESULTS[unit] = {
        "unit": unit,
        "name": name,
        "verdict": verdict,
        "evidence": evidence,
    }
    mark = PASS if verdict.upper() in {"GO", "LIVE_HANDSHAKE_PASSED"} else FAIL
    log(f"{mark} Phase B {unit} ({name}): {verdict}  [{evidence}]")


async def run_gate(unit: str, name: str, argv: list[str]) -> None:
    """Run one live gate probe and capture its verdict."""
    import subprocess

    started = time.time()
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        out, _ = await proc.communicate()
        stdout = out.decode(errors="replace")
        elapsed = time.time() - started
        verdict = "GO"
        if "LIVE_HANDSHAKE_PASSED" in stdout:
            verdict = "LIVE_HANDSHAKE_PASSED"
        if proc.returncode != 0:
            verdict = "NO-GO"
        # Tail of the log is the evidence; also keep the last meaningful line.
        evidence = "\n".join(stdout.strip().splitlines()[-3:])
        record_gate(unit, name, verdict, f"exit={proc.returncode} elapsed={elapsed:.2f}s | {evidence}")
    except Exception as exc:  # noqa: BLE001
        record_gate(unit, name, "NO-GO", f"probe launch failed: {exc!r}")


# ---------------------------------------------------------------------------
# Admin / edge-fleet helpers (real HTTP against live MindRoom API)
# ---------------------------------------------------------------------------

def _admin_request(method: str, path: str, body: dict | None = None) -> tuple[int, object | None]:
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode() if body is not None else None
    req = urllib.request.Request(
        MINDROOM_BASE + path,
        data=data,
        headers={"Authorization": f"Bearer {ADMIN_KEY}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode()


def _issue_enrollment(node_id: str, runtime: str, public_key: str, capabilities: list[str]) -> str:
    status, resp = _admin_request(
        "POST",
        "/api/edge-fleet-admin/enrollments",
        {
            "node_id": node_id,
            "runtime": runtime,
            "public_key": public_key,
            "capabilities": capabilities,
            "expires_in_seconds": 600,
        },
    )
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(f"enrollment issue failed: {status} {resp}")
    return resp["token"]


def _queue_job(job_id: str, runtime: str, capabilities: list[str], payload: dict) -> None:
    status, resp = _admin_request(
        "POST",
        "/api/edge-fleet-admin/jobs",
        {
            "job_id": job_id,
            "runtime": runtime,
            "required_capabilities": capabilities,
            "payload": payload,
        },
    )
    if status != 201:
        raise RuntimeError(f"job queue failed: {status} {resp}")


def _get_job(job_id: str) -> dict:
    status, resp = _admin_request("GET", f"/api/edge-fleet-admin/jobs/{job_id}")
    if status != 200 or not isinstance(resp, dict):
        raise RuntimeError(f"job fetch failed: {status} {resp}")
    return resp


# ---------------------------------------------------------------------------
# Placement 1 & 2: external Hermes + OpenClaw edge-fleet placement
# ---------------------------------------------------------------------------

async def place_edge_item(
    *,
    node_id: str,
    runtime: str,
    capabilities: list[str],
    job_id: str,
    job_payload: dict,
    source: str,
    target: str,
) -> dict:
    """Place one external item (Hermes/OpenClaw) over the live edge-fleet mesh."""
    entry: dict = {
        "item": node_id,
        "source": source,
        "target": target,
        "runtime": runtime,
    }
    t0 = time.time()
    try:
        ident_dir = Path(tempfile.mkdtemp())
        identity = EdgeNodeIdentity.generate(
            ident_dir / f"{node_id}.json",
            node_id=node_id,
            runtime=runtime,
            capabilities=tuple(capabilities),
        )
        token = _issue_enrollment(node_id, runtime, identity.public_key, capabilities)
        client = EdgeNodeClient(base_url=MINDROOM_BASE, identity=identity)
        await client.enroll(token)  # real /api/edge-fleet/enroll
        t_enroll = time.time()

        _queue_job(job_id, runtime, capabilities, job_payload)
        client2 = EdgeNodeClient(base_url=MINDROOM_BASE, identity=identity)

        async def executor(payload: dict) -> dict:
            return {"echo": payload, "device": node_id, "runtime": runtime, "status": "attested"}

        completed = await client2.run_once(executor, lease_seconds=60)  # lease + execute + complete
        t_done = time.time()

        job = _get_job(job_id)
        entry.update(
            {
                "transport": "edge-fleet HTTP over real mesh (live MindRoom API)",
                "transport_status": "delivered" if completed else "no_work",
                "node_enrolled": True,
                "job_status": job.get("status"),
                "result_attested": bool(job.get("result_signature")),
                "enroll_latency_ms": round((t_enroll - t0) * 1000, 2),
                "roundtrip_latency_ms": round((t_done - t0) * 1000, 2),
                "blocked": False,
            }
        )
    except Exception as exc:  # noqa: BLE001 - mark BLOCKED with evidence
        entry.update(
            {
                "transport_status": "BLOCKED",
                "blocked": True,
                "blocker_evidence": f"{type(exc).__name__}: {exc}",
                "roundtrip_latency_ms": round((time.time() - t0) * 1000, 2),
            }
        )
    return entry


# ---------------------------------------------------------------------------
# Placement 3: MindRoom room placement over real Matrix transport
# ---------------------------------------------------------------------------

def _register_matrix_user() -> tuple[str, str]:
    username = f"p4_demo_{secrets.token_hex(4)}"
    password = secrets.token_urlsafe(20)
    body = {
        "username": username,
        "password": password,
        "auth": {"type": "m.login.dummy"},
    }
    req = urllib.request.Request(
        f"{HOMESERVER}/_matrix/client/v3/register",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode())
    return payload["user_id"], payload["access_token"]


async def place_mindroom_room() -> dict:
    """Place a MindRoom room delivery over the live Matrix transport."""
    entry: dict = {
        "item": "mindroom-room-mesh-placement",
        "source": "mindroom-gateway",
        "target": "live-matrix-room/thread",
        "transport": "MatrixMeshTransport + injected nio client (real Synapse :8008)",
    }
    client = nio.AsyncClient(HOMESERVER)
    t0 = time.time()
    try:
        user_id, token = _register_matrix_user()
        client.access_token = token
        client.user_id = user_id
        room_resp = await client.room_create(name="p4-demo-mesh-placement", preset=nio.RoomPreset.public_chat)
        if not isinstance(room_resp, nio.RoomCreateResponse):
            raise RuntimeError(f"createRoom failed: {room_resp}")
        room_id = room_resp.room_id
        # Post a thread ROOT (MSC3440 thread anchor).
        root_resp = await client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={"msgtype": "m.text", "body": "P4 demo mesh ROOT"},
            ignore_unverified_devices=True,
        )
        if not isinstance(root_resp, nio.RoomSendResponse):
            raise RuntimeError(f"root send failed: {root_resp}")
        root_id = str(root_resp.event_id)

        # Inject a real nio client into MatrixMeshTransport and deliver a
        # thread-scoped mesh message (Phase B Unit 2 mechanism).
        from mindroom.mesh import MatrixMeshTransport, MeshCursorStore, MeshMessage  # noqa: PLC0415
        from mindroom.mesh.models import MeshOutboxEntry  # noqa: PLC0415

        cursor_store = MeshCursorStore()
        transport = MatrixMeshTransport(
            cursor_store=cursor_store,
            gateway_room_id="!mesh-gateway:localhost",
            client=client,
        )
        outbox_entry = MeshOutboxEntry(
            outbox_id=f"mesh-outbox-p4-{secrets.token_hex(3)}",
            message_id=f"mesh-msg-p4-{secrets.token_hex(3)}",
            source_worker_id="p4-gateway",
            target_worker_id="p4-room-worker",
            source_room_id=room_id,
            target_room_id=room_id,
            gateway_room_id="!mesh-gateway:localhost",
            target_thread_id=root_id,
            target_session_id=f"{room_id}:{root_id}",
        )
        message = MeshMessage(
            source_worker_id="p4-gateway",
            target_worker_id="p4-room-worker",
            content="P4 demo: MindRoom room placement over real mesh transport",
            correlation_id=f"corr-p4-{secrets.token_hex(3)}",
        )
        status = await transport.deliver(outbox_entry, message)
        t_deliver = time.time()

        # Verify via a real sync that the wire envelope landed (Unit 2/5).
        sync = await client.sync(timeout=10000, full_state=True)
        found = False
        if isinstance(sync.rooms, dict):
            joined = {rid: r for rid, r in sync.rooms.items() if r is not None}
        else:
            joined = getattr(sync.rooms, "join", None) or {}
        from mindroom.mesh.transport import _entry_from_wire_content, _message_content_from_source  # noqa: PLC0415

        for _rid, joined_room in joined.items():
            for event in (getattr(getattr(joined_room, "timeline", None), "events", None) or []):
                if event.__class__.__name__ != "RoomMessageText":
                    continue
                content = _message_content_from_source(getattr(event, "source", None))
                parsed = _entry_from_wire_content(content)
                if parsed is not None and parsed.outbox_id == outbox_entry.outbox_id:
                    found = True
        t_sync = time.time()

        entry.update(
            {
                "room_id": room_id,
                "thread_root_id": root_id,
                "outbox_id": outbox_entry.outbox_id,
                "transport_status": status if found else "delivered_unverified",
                "sync_verified": found,
                "deliver_latency_ms": round((t_deliver - t0) * 1000, 2),
                "roundtrip_latency_ms": round((t_sync - t0) * 1000, 2),
                "blocked": False,
            }
        )
    except Exception as exc:  # noqa: BLE001 - mark BLOCKED with evidence
        entry.update(
            {
                "transport_status": "BLOCKED",
                "blocked": True,
                "blocker_evidence": f"{type(exc).__name__}: {exc}",
                "roundtrip_latency_ms": round((time.time() - t0) * 1000, 2),
            }
        )
    finally:
        await client.close()
    return entry


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> int:
    started = time.time()
    log("=" * 74)
    log("  P4 LIVE DEMO — external placement over the REAL mesh transport")
    log("  P1 transport ENABLED (4 live Phase B gates passed)")
    log("=" * 74)

    # ── 0. Preflight: external surface reachability ─────────────────────
    log("\n[0] Live surface preflight")
    surfaces = {
        "MindRoom API (:8765)": "http://localhost:8765/",
        "Matrix homeserver (:8008)": "http://localhost:8008/_matrix/client/versions",
        "OpenClaw gateway (:18789)": "http://localhost:18789/health",
    }
    preflight: dict[str, bool] = {}
    for name, url in surfaces.items():
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                ok = resp.status < 400
        except Exception:
            ok = False
        preflight[name] = ok
        log(f"  {'✓' if ok else '✗'} {name}: {'reachable' if ok else 'UNREACHABLE'}")
    if not any(preflight.values()):
        log(f"{FAIL} No external surface reachable — cannot place anything.")
        return 1

    # ── 1. Place external Hermes message ─────────────────────────────────
    log("\n[1] Place external HERMES message over real transport")
    run_suffix = secrets.token_hex(4)
    hermes = await place_edge_item(
        node_id=f"p4-demo-hermes-{run_suffix}",
        runtime="hermes",
        capabilities=["memory", "nlp"],
        job_id=f"p4-demo-job-hermes-{run_suffix}",
        job_payload={"task": "place_external_hermes_message", "corpus": "mesh"},
        source="p4-gateway",
        target="hermes-edge-node (runtime=hermes)",
    )
    log(f"  hermes: transport={hermes.get('transport_status')} "
        f"rtt={hermes.get('roundtrip_latency_ms')}ms "
        f"job={hermes.get('job_status')} attested={hermes.get('result_attested')}")
    if hermes.get("blocked"):
        log(f"  {BLOCKED} BLOCKED: {hermes.get('blocker_evidence')}")

    # ── 2. Place external OpenClaw task ──────────────────────────────────
    log("\n[2] Place external OPENCLAW task over real transport")
    openclaw = await place_edge_item(
        node_id=f"p4-demo-openclaw-{run_suffix}",
        runtime="openclaw",
        capabilities=["code", "mesh.worker"],
        job_id=f"p4-demo-job-openclaw-{run_suffix}",
        job_payload={"task": "place_openclaw_task", "module": "mesh_gateway"},
        source="p4-gateway",
        target="openclaw-edge-node (runtime=openclaw)",
    )
    log(f"  openclaw: transport={openclaw.get('transport_status')} "
        f"rtt={openclaw.get('roundtrip_latency_ms')}ms "
        f"job={openclaw.get('job_status')} attested={openclaw.get('result_attested')}")
    if openclaw.get("blocked"):
        log(f"  {BLOCKED} BLOCKED: {openclaw.get('blocker_evidence')}")

    # ── 3. Place MindRoom room placement ─────────────────────────────────
    log("\n[3] Place MINDROOM room placement over real Matrix transport")
    mindroom = await place_mindroom_room()
    log(f"  mindroom: transport={mindroom.get('transport_status')} "
        f"sync_verified={mindroom.get('sync_verified')} "
        f"rtt={mindroom.get('roundtrip_latency_ms')}ms")
    if mindroom.get("blocked"):
        log(f"  {BLOCKED} BLOCKED: {mindroom.get('blocker_evidence')}")

    # ── 4. Re-run the 4 Phase B gate probes ──────────────────────────────
    log("\n[4] Phase B gate results (re-run live)")
    st = os.path.join(_REPO_ROOT, "scripts", "testing")
    await run_gate("Unit1", "inverted enrollment handshake",
                   [os.path.join(st, "mesh_phaseb_unit1_live_smoke.py")])
    await run_gate("Unit2", "real Matrix thread delivery",
                   [os.path.join(st, "mesh_phaseb_unit2_live_smoke.py")])
    await run_gate("Unit3", "real deliver_stream tool-state posting",
                   [os.path.join(st, "mesh_phaseb_unit3_live_smoke.py")])
    await run_gate("Unit5", "real coordinator sync-token replay",
                   [os.path.join(st, "mesh_phaseb_unit5_live_gate_probe.py")])

    # ── 5. Compose demo receipt ──────────────────────────────────────────
    elapsed = time.time() - started
    receipt = {
        "schema": "mindroom.p4-live-placement-demo/1",
        "generated_at": datetime.now(UTC).isoformat(),
        "p1_transport_enabled": True,
        "elapsed_seconds": round(elapsed, 2),
        "preflight": preflight,
        "placements": [hermes, openclaw, mindroom],
        "phase_b_gate_results": list(GATE_RESULTS.values()),
        "blocked_count": sum(1 for p in [hermes, openclaw, mindroom] if p.get("blocked")),
    }

    log("\n" + "=" * 74)
    log("  DEMO RECEIPT")
    log("=" * 74)
    log(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    log("=" * 74)

    receipt_path = Path(_REPO_ROOT) / "docs" / "p4_live_placement_demo_receipt.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    log(f"\n{BLOCKED if receipt['blocked_count'] else PASS} receipt written to {receipt_path}")
    log(f"  blocked_items={receipt['blocked_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))