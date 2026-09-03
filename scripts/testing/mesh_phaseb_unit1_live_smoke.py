#!/usr/bin/env python3
"""Phase B Unit 1 gate smoke — inverted enrollment against the LIVE P9 edge-fleet surface.

Issues a real ``mindroom.edge-enrollment/1`` token through the shared
``edge_fleet.EnrollmentAuthority`` (the same code path the mesh
``MeshEnrollmentAuthority`` subclasses) using the live server's configured
enrollment key, then presents it to the live ``/api/edge-fleet/enroll``
endpoint — the exact surface an OpenClaw ``EdgeNodeClient.enroll(token)``
would call.

Standalone smoke probe: not part of the pytest suite and intentionally uses
blocking stdlib HTTP, so it carries its own per-file ruff ignores.
"""
# ruff: noqa: ASYNC210, EM101, EXE001, PTH118, PTH120, S310, TRY003

from __future__ import annotations

import asyncio
import base64
import os
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "src"))

from mindroom.edge_fleet import EnrollmentAuthority

BASE = os.environ.get("MINDROOM_API_BASE", "http://localhost:8765")
ENROLL_URL = f"{BASE}/api/edge-fleet/enroll"


def load_key() -> bytes:
    """Return the live server's configured edge-fleet enrollment key."""
    raw = os.environ.get("MINDROOM_EDGE_FLEET_ENROLLMENT_KEY", "")
    key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    if len(key) < 32:
        raise SystemExit("MINDROOM_EDGE_FLEET_ENROLLMENT_KEY is missing or too short")
    return key


async def smoke() -> None:
    """Run one live inverted-enrollment handshake against the P9 enroll endpoint."""
    authority = EnrollmentAuthority(load_key())
    expires_at = datetime.now(UTC) + timedelta(minutes=5)
    token = authority.issue(
        node_id="phaseb-smoke-openclaw-worker",
        runtime="openclaw",
        public_key=base64.urlsafe_b64encode(b"\x5a" * 32).decode().rstrip("="),
        capabilities=("mesh.worker",),
        expires_at=expires_at,
    )
    body = f'{{"token":"{token}"}}'.encode()
    request = urllib.request.Request(ENROLL_URL, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = response.read().decode()
            print(f"LIVE ENROLL STATUS={response.status} BODY={payload}")
            verdict = response.status == 200 and '"phaseb-smoke-openclaw-worker"' in payload
            print("GATE_RESULT=" + ("LIVE_HANDSHAKE_PASSED" if verdict else "LIVE_HANDSHAKE_FAILED"))
    except urllib.error.HTTPError as exc:
        print(f"LIVE ENROLL HTTP_ERROR status={exc.code} body={exc.read().decode()}")
        print("GATE_RESULT=LIVE_HANDSHAKE_FAILED")
    except Exception as exc:
        print(f"LIVE ENROLL TRANSPORT_ERROR {exc!r}")
        print("GATE_RESULT=LIVE_SURFACE_UNREACHABLE")


if __name__ == "__main__":
    asyncio.run(smoke())
