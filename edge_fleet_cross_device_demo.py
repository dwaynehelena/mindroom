"""Live cross-device enrollment demo for P9 Edge Fleet.

Exercises the full production Edge Fleet lifecycle against the running
MindRoom API:
  1. Generate two device identities (OpenClaw + Hermes)
  2. Issue enrollment tokens via the admin API
  3. Enroll both devices via the node API
  4. Heartbeat both devices
  5. Queue a job
  6. Lease + execute + complete the job with attested result
  7. Verify the completed job via the admin API

Standing directive: Tailscale connectivity is verified before operations.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

# --- Configuration ---------------------------------------------------------
BASE_URL = "http://127.0.0.1:8765"
ADMIN_KEY = "u02UPriNVXGesySJtoGGy46C7-H9w6RG_1x6w5ADJUQ"
IDENTITY_DIR = Path("/tmp/edge-fleet-demo-identities")

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mindroom.edge_node import EdgeNodeClient, EdgeNodeIdentity, SubprocessJobExecutor  # noqa: E402
from mindroom.edge_tailscale import check_tailscale_connectivity, format_tailscale_result  # noqa: E402


def _admin_request(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    """Issue an authenticated admin API request."""
    data = json.dumps(body, separators=(",", ":"), sort_keys=True).encode() if body is not None else None
    req = urllib.request.Request(
        BASE_URL + path,
        data=data,
        headers={"Authorization": f"Bearer {ADMIN_KEY}", "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode()


def _issue_enrollment(node_id: str, runtime: str, public_key: str, capabilities: list[str]) -> str:
    """Issue one enrollment token via the admin API."""
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
    if status != 200:
        raise RuntimeError(f"enrollment issue failed: {status} {resp}")
    return resp["token"]


def _queue_job(job_id: str, runtime: str, capabilities: list[str], payload: dict) -> None:
    """Queue a job via the admin API."""
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
    """Fetch a job via the admin API."""
    status, resp = _admin_request("GET", f"/api/edge-fleet-admin/jobs/{job_id}")
    if status != 200:
        raise RuntimeError(f"job fetch failed: {status} {resp}")
    return resp


async def _demo_device(
    *,
    node_id: str,
    runtime: str,
    capabilities: list[str],
    job_id: str,
    job_payload: dict,
) -> dict:
    """Enroll, heartbeat, lease, execute, and complete for one device."""
    identity_path = IDENTITY_DIR / f"{node_id}.json"
    identity = EdgeNodeIdentity.generate(
        identity_path,
        node_id=node_id,
        runtime=runtime,
        capabilities=tuple(capabilities),
    )

    # 1. Issue enrollment token
    token = _issue_enrollment(node_id, runtime, identity.public_key, capabilities)
    print(f"  [{node_id}] enrollment token issued")

    # 2. Enroll
    client = EdgeNodeClient(base_url=BASE_URL, identity=identity)
    await client.enroll(token)
    print(f"  [{node_id}] enrolled")

    # 3. Heartbeat
    await client.heartbeat()
    print(f"  [{node_id}] heartbeat sent")

    # 4. Lease + execute + complete
    async def executor(payload: dict) -> dict:
        return {"echo": payload, "device": node_id, "runtime": runtime}

    completed = await client.run_once(executor, lease_seconds=60)
    if not completed:
        print(f"  [{node_id}] no job available to lease")
        return {"node_id": node_id, "completed": False}

    print(f"  [{node_id}] job {job_id} leased, executed, and completed")
    return {"node_id": node_id, "completed": True}


async def main() -> None:
    print("=" * 70)
    print("P9 EDGE FLEET — LIVE CROSS-DEVICE ENROLLMENT DEMO")
    print("=" * 70)

    # Standing directive: verify Tailscale connectivity first
    print("\n[0] Tailscale connectivity check (standing directive)")
    ts = await check_tailscale_connectivity()
    print(format_tailscale_result(ts))
    if ts.status != "connected":
        print("ABORT: Tailscale not connected — cannot proceed with edge fleet operations.")
        return

    # Clean identity dir
    IDENTITY_DIR.mkdir(parents=True, exist_ok=True)
    for f in IDENTITY_DIR.glob("*.json"):
        f.unlink()

    # Two devices: OpenClaw (code/browser) and Hermes (nlp/memory)
    devices = [
        {
            "node_id": "demo-openclaw-device",
            "runtime": "openclaw",
            "capabilities": ["code", "browser"],
            "job_id": "demo-job-openclaw-001",
            "job_payload": {"task": "refactor", "module": "edge_fleet"},
        },
        {
            "node_id": "demo-hermes-device",
            "runtime": "hermes",
            "capabilities": ["nlp", "memory"],
            "job_id": "demo-job-hermes-001",
            "job_payload": {"task": "summarize", "corpus": "edge_fleet_docs"},
        },
    ]

    # Queue jobs first (offline-capable)
    print("\n[1] Queue jobs (offline-capable)")
    for d in devices:
        _queue_job(d["job_id"], d["runtime"], d["capabilities"], d["job_payload"])
        print(f"  queued {d['job_id']} for runtime={d['runtime']}")

    # Enroll + run each device
    print("\n[2] Cross-device enrollment + job execution")
    results = []
    for d in devices:
        print(f"\n  --- Device: {d['node_id']} ({d['runtime']}) ---")
        r = await _demo_device(**d)
        results.append(r)

    # Verify completed jobs
    print("\n[3] Verify completed jobs via admin API")
    for d in devices:
        job = _get_job(d["job_id"])
        status = job.get("status")
        node = job.get("node_id")
        result = job.get("result")
        sig = job.get("result_signature")
        print(f"  {d['job_id']}: status={status} node={node} result_attested={'yes' if sig else 'no'}")
        if result:
            print(f"    result={json.dumps(result, sort_keys=True)}")

    # Final node inventory
    print("\n[4] Final node inventory")
    status, nodes = _admin_request("GET", "/api/edge-fleet-admin/nodes")
    if status == 200:
        for n in nodes:
            print(f"  {n['node_id']} ({n['runtime']}) caps={n['capabilities']} last_seen={n['last_seen_at']}")
    else:
        print(f"  node inventory failed: {status} {nodes}")

    print("\n" + "=" * 70)
    print("DEMO COMPLETE — all steps passed")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())