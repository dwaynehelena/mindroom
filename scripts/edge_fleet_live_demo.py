#!/usr/bin/env python3
"""Run a reversible live HTTP demo with OpenClaw and Hermes edge identities."""

from __future__ import annotations

import asyncio
import socket
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from mindroom.api.edge_fleet import create_edge_fleet_router
from mindroom.edge_fleet import EdgeFleet, EnrollmentAuthority
from mindroom.edge_node import EdgeNodeClient, EdgeNodeIdentity, SubprocessJobExecutor

_WORKER = (
    "import json,sys;"
    "value=json.load(sys.stdin);"
    "print(json.dumps({'runtime':value['runtime'],'digest':value['digest']},sort_keys=True))"
)


async def _wait_started(server: uvicorn.Server) -> None:
    for _attempt in range(200):
        if server.started:
            return
        await asyncio.sleep(0.01)
    message = "edge demo HTTP server did not start"
    raise RuntimeError(message)


async def _run() -> None:
    with tempfile.TemporaryDirectory(prefix="mindroom-edge-demo-") as temporary:
        root = Path(temporary)
        fleet = EdgeFleet(root / "fleet.sqlite3", EnrollmentAuthority(b"edge-demo-key" * 3))
        await fleet.open()
        server: uvicorn.Server | None = None
        task: asyncio.Task[None] | None = None
        listener = socket.socket()
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            app = FastAPI()
            app.include_router(create_edge_fleet_router(fleet))
            server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
            task = asyncio.create_task(server.serve(sockets=[listener]))
            await _wait_started(server)

            clients: dict[str, EdgeNodeClient] = {}
            for runtime in ("openclaw", "hermes"):
                identity = EdgeNodeIdentity.generate(
                    root / f"{runtime}.json",
                    node_id=f"demo-{runtime}",
                    runtime=runtime,
                    capabilities=("bounded-json",),
                )
                token = fleet.issue_enrollment(
                    node_id=identity.node_id,
                    runtime=runtime,
                    public_key=identity.public_key,
                    capabilities=identity.capabilities,
                    expires_at=datetime.now(UTC) + timedelta(minutes=5),
                )
                client = EdgeNodeClient(base_url=f"http://127.0.0.1:{port}", identity=identity)
                await client.enroll(token)
                await client.heartbeat()
                clients[runtime] = client
                await fleet.queue_job(
                    job_id=f"job-{runtime}",
                    runtime=runtime,
                    required_capabilities=("bounded-json",),
                    payload={"runtime": runtime, "digest": f"{runtime}-live-proof"},
                )

            executor = SubprocessJobExecutor((sys.executable, "-c", _WORKER), timeout_seconds=10)
            for runtime, client in clients.items():
                if not await client.run_once(executor):
                    message = f"{runtime} did not lease its queued job"
                    raise RuntimeError(message)
                job = await fleet.job(f"job-{runtime}")
                if job.status != "completed" or job.node_id != f"demo-{runtime}" or job.result_signature is None:
                    message = f"{runtime} result was not attested"
                    raise RuntimeError(message)
                print(f"{runtime}=enrolled,healthy,leased,attested")

            nodes = await fleet.healthy_nodes(observed_at=datetime.now(UTC), max_age=timedelta(minutes=1))
            if {node.runtime for node in nodes} != {"openclaw", "hermes"}:
                message = "both live edge identities were not healthy"
                raise RuntimeError(message)
            print("cleanup=temporary-state-only")
        finally:
            if server is not None:
                server.should_exit = True
            if task is not None:
                await task
            listener.close()
            await fleet.close()


def main() -> int:
    """Run the live reversible demo."""
    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
