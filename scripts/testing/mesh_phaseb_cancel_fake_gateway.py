#!/usr/bin/env python3
"""Documented local fake of the OpenClaw worker ``/cancel`` route (Phase B Unit 4).

This is the **primary** verification path for the OpenClaw ``/cancel`` RPC body
and transport logic while the real OpenClaw gateway lacks the route (see
``docs/mesh_cancel_prop_phase_b_gate.md``).  It runs a loopback-only aiohttp
server that exposes exactly the ``POST /cancel`` route that
``OpenClawMeshCancelTransport`` targets, records every request, and returns a
configurable ack — so the RPC body construction, response parsing, error and
timeout handling, and registry integration are fully testable locally with **no
real OpenClaw gateway and no non-loopback network**.

Usage
-----
Start the fake gateway (default loopback, ephemeral port):

    python -m scripts.testing.mesh_phaseb_cancel_fake_gateway

Then point an ``OpenClawMeshCancelTransport`` at ``http://127.0.0.1:<port>``
(after opening ``PHASE_B_CANCEL_RPC_ENABLED``) to drive a live local round-trip.

Environment
-----------
OPENCLAW_FAKE_CANCEL_HOST (default 127.0.0.1)
OPENCLAW_FAKE_CANCEL_PORT (default 0 = ephemeral)
"""
# ruff: noqa: ANN001, ANN201, ASYNC110, D100, D101, D102, D103, EM101, EM102, EXE001, RUF100, S105, SLF001

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from aiohttp import web

PASS = "\u2713"
FAIL = "\u2717"

#: Documented canonical request body keys for the OpenClaw ``/cancel`` RPC.
CANCEL_BODY_KEYS = ("worker_id", "correlation_id", "cancel_source", "outbox_id")


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def build_app(*, records: list[dict], ack_payload: dict | None = None, status: int = 200) -> web.Application:
    """Return the fake OpenClaw ``/cancel`` aiohttp application."""
    payload = ack_payload if ack_payload is not None else {"acknowledged": True}

    async def handler(request: web.Request) -> web.StreamResponse:
        body = await request.json()
        records.append(body)
        # Validate the request carries the documented RPC body shape.
        missing = [k for k in CANCEL_BODY_KEYS if k not in body]
        if missing:
            return web.json_response(
                {"acknowledged": False, "reason": f"missing fields: {', '.join(missing)}"},
                status=400,
            )
        return web.json_response(payload, status=status)

    app = web.Application()
    app.router.add_post("/cancel", handler)
    return app


async def run(args: argparse.Namespace) -> int:
    records: list[dict] = []
    app = build_app(records=records)
    runner = web.AppRunner(app)
    await runner.setup()
    host = os.environ.get("OPENCLAW_FAKE_CANCEL_HOST", args.host)
    port = int(os.environ.get("OPENCLAW_FAKE_CANCEL_PORT", args.port))
    site = web.TCPSite(runner, host, port)
    await site.start()
    actual_port = port
    if port == 0:
        sockets = [sock for sock in site._server.sockets if sock is not None]  # noqa: SLF001
        actual_port = sockets[0].getsockname()[1]

    base_url = f"http://{host}:{actual_port}"
    log("=" * 72)
    log("  Documented local fake OpenClaw /cancel gateway")
    log(f"  Listening: {base_url}/cancel")
    log("  (loopback only — no real OpenClaw gateway required)")
    log("  Cancel body keys: " + ", ".join(CANCEL_BODY_KEYS))
    log("=" * 72)
    log(f"  Request log URL: {base_url}/cancel")
    log("  (press Ctrl+C to stop)")
    try:
        while True:
            await asyncio.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        await runner.cleanup()
    log(f"{PASS} Fake gateway stopped; {len(records)} /cancel request(s) recorded.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", help="loopback host to bind (default 127.0.0.1)")
    parser.add_argument("--port", type=int, default=0, help="port to bind (0 = ephemeral, default)")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
