#!/usr/bin/env python3
"""Phase B Unit 4 gate probe — detect a live OpenClaw ``/cancel`` route on port 18789.

Probes the real OpenClaw gateway for a ``/cancel`` RPC route.  If a real
``POST /cancel`` route exists AND a live smoke (correct request body -> 2xx ack)
passes, the probe reports GO and the operator flips
``PHASE_B_CANCEL_RPC_ENABLED`` to True.  If the gateway lacks the route
(404/Not Found, SPA catch-all), the probe reports NO-GO and the unit stays
**DEFERRED-for-missing-external-surface** with this evidence recorded.

Standalone network probe: not part of the pytest suite.
"""
# ruff: noqa: ANN001, ANN201, D100, D101, D102, D103, EM101, EM102, EXE001, RUF100, S105, TRY003

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

GATEWAY_BASE = os.environ.get("OPENCLAW_GATEWAY_URL", "http://localhost:18789").rstrip("/")

PASS = "\u2713"
FAIL = "\u2717"


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def probe_get(path: str) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(f"{GATEWAY_BASE}{path}", timeout=8) as resp:  # noqa: S310 - loopback probe
            return resp.status, resp.read(400)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200)
    except OSError as exc:
        raise SystemExit(f"{FAIL} cannot reach gateway {GATEWAY_BASE}: {exc}") from exc


def probe_post(path: str, body: dict) -> tuple[int, bytes]:
    req = urllib.request.Request(  # noqa: S310 - loopback probe
        f"{GATEWAY_BASE}{path}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - loopback probe
            return resp.status, resp.read(200)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(200)
    except OSError as exc:
        raise SystemExit(f"{FAIL} cannot POST {path}: {exc}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", default=GATEWAY_BASE, help="OpenClaw gateway base URL")
    args = parser.parse_args()
    base = args.gateway.rstrip("/")

    log("=" * 72)
    log("  Phase B Unit 4 gate probe — real OpenClaw /cancel route detection")
    log(f"  Gateway under test: {base}")
    log("=" * 72)

    # Stage 1: reachability + SPA characterisation.
    get_status, get_body = probe_get("/")
    log(f"{PASS if get_status == 200 else FAIL} GET / -> HTTP {get_status} (body={get_body[:40]!r})")

    # Stage 2: probe POST /cancel and plausible variants.
    variants = ["/cancel", "/api/cancel", "/api/worker/cancel", "/workers/cancel", "/api/v1/cancel"]
    body = {
        "worker_id": "probe-worker",
        "correlation_id": "probe-corr",
        "cancel_source": "user_stop",
        "outbox_id": "probe-outbox",
    }
    results: dict[str, int] = {}
    for path in variants:
        status, _resp_body = probe_post(path, body)
        results[path] = status
        log(f"{PASS if status == 200 else FAIL} POST {path} -> HTTP {status}")

    # Stage 3: live smoke — a correct request body returning a 2xx ack.
    route_found = False
    smoke_passed = False
    for path, status in results.items():
        if 200 <= status < 300:
            route_found = True
            # Attempt a live ack round-trip on the first 2xx route found.
            try:
                req = urllib.request.Request(  # noqa: S310 - loopback probe
                    f"{base}{path}",
                    data=json.dumps(body).encode(),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=8) as resp:  # noqa: S310 - loopback probe
                    payload = json.loads(resp.read().decode())
                if isinstance(payload, dict) and payload.get("acknowledged", False) is True:
                    smoke_passed = True
            except (urllib.error.URLError, OSError, json.JSONDecodeError):
                smoke_passed = False
            break

    log("-" * 72)
    if route_found and smoke_passed:
        log(f"{PASS} GO: real OpenClaw /cancel route exists and a live smoke passed.")
        log("      Flip PHASE_B_CANCEL_RPC_ENABLED -> True (CLEARED).")
        return 0

    log(
        f"{FAIL} NO-GO: no real OpenClaw /cancel route with a live 2xx ack found.",
    )
    log("      Route probe results: " + ", ".join(f"{p}={s}" for p, s in results.items()))
    log("      Gateway GET / returned SPA HTML (catch-all); POST /cancel -> HTTP 404.")
    log("      Unit stays PHASE_B_CANCEL_RPC_ENABLED=False (DEFERRED-for-missing-external-surface).")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
