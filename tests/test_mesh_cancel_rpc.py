"""Phase B Unit 4 — OpenClaw /cancel RPC body + transport logic (against a documented local fake).

Covers, against a documented loopback fake OpenClaw gateway (no real gateway,
no network beyond loopback):
- ``build_cancel_body`` constructs the exact ``/cancel`` request body
  (worker_id, correlation_id, cancel_source, outbox_id).
- ``parse_ack`` maps 2xx ack / 2xx unacknowledged / non-2xx responses.
- The real HTTP transport POSTs to ``{endpoint}/cancel`` and round-trips a live
  ack through the fake server (transport logic fully exercised locally).
- Error/timeout conditions surface as ``MeshCancelPropagationError``.
- Endpoint policy (HTTPS or loopback HTTP) is enforced at construction.
- The real route is only used when ``PHASE_B_CANCEL_RPC_ENABLED`` is open;
  with the gate closed ``request_cancel`` refuses (no network).
- The default local path still uses ``FakeMeshCancelTransport`` (backwards
  compatible, no network).
- Registry integration: the propagator drives the real transport against the
  fake server and records the correlated ack.

The documented fake gateway mirrors the OpenClaw ``/cancel`` route shape used by
``OpenClawMeshCancelTransport`` and is the *primary* path for local verification
while the real OpenClaw gateway lacks the route (see
docs/mesh_cancel_prop_phase_b_gate.md).
"""

# ruff: noqa: ANN001, ANN201, ANN202, D101, D102, D103

from __future__ import annotations

import asyncio

import pytest
from aiohttp import web

from mindroom.mesh import (
    FakeMeshCancelTransport,
    MeshCancelAck,
    MeshCancelCommand,
    MeshCancellationPropagator,
    MeshCancelPropagationError,
    OpenClawMeshCancelTransport,
    cancel_prop,
)
from mindroom.mesh.models import MeshOutboxEntry

ROOM = "!alpha:localhost"


def _cmd(
    *,
    worker_id="beta",
    correlation_id="corr-1",
    outbox_id="o1",
    cancel_source="user_stop",
) -> MeshCancelCommand:
    return MeshCancelCommand(
        worker_id=worker_id,
        correlation_id=correlation_id,
        outbox_id=outbox_id,
        cancel_source=cancel_source,
    )


def _outbox_entry():
    return MeshOutboxEntry(
        outbox_id="o1",
        message_id="m1",
        source_worker_id="alpha",
        target_worker_id="beta",
        source_room_id=ROOM,
        target_room_id="!beta:localhost",
        gateway_room_id="!gw:localhost",
        cancel_source="user_stop",
    )


class FakeOpenClawCancelServer:
    """Documented local fake of the OpenClaw worker ``/cancel`` route.

    A loopback aiohttp server exposing ``POST /cancel`` (the exact route
    ``OpenClawMeshCancelTransport`` targets).  It records every request so the
    transport logic is fully testable without a real OpenClaw gateway.  The
    handler returns a configurable JSON ack:
    - ``ack_payload`` (default ``{"acknowledged": True}``) is returned with
      HTTP 200;
    - ``status`` overrides the HTTP status (e.g. ``404`` to simulate a missing
      route, ``500`` for a server error);
    - ``delay`` simulates a slow/unresponsive worker.
    """

    def __init__(self, *, ack_payload: dict | None = None, status: int = 200, delay: float = 0.0) -> None:
        self.ack_payload = ack_payload if ack_payload is not None else {"acknowledged": True}
        self.status = status
        self.delay = delay
        self.requests: list[dict] = []
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.base_url = ""

    async def _handler(self, request: web.Request) -> web.StreamResponse:
        body = await request.json()
        self.requests.append(body)
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        return web.json_response(self.ack_payload, status=self.status)

    async def start(self) -> None:
        """Bind the fake gateway to a random loopback port and start serving."""
        app = web.Application()
        app.router.add_post("/cancel", self._handler)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        self._site = site
        sockets = [sock for sock in site._server.sockets if sock is not None]
        port = sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


# ── Request body construction ────────────────────────────────────────────


class TestCancelRpcBody:
    def test_build_cancel_body_fields(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
        body = transport.build_cancel_body(_cmd())
        assert body == {
            "worker_id": "beta",
            "correlation_id": "corr-1",
            "cancel_source": "user_stop",
            "outbox_id": "o1",
        }

    def test_build_cancel_body_sync_restart_source(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
        body = transport.build_cancel_body(_cmd(cancel_source="sync_restart"))
        assert body["cancel_source"] == "sync_restart"


# ── Response parsing ─────────────────────────────────────────────────────


class TestCancelRpcParseAck:
    def test_parse_ack_2xx(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
        ack = transport.parse_ack(_cmd(), 200, {"acknowledged": True})
        assert isinstance(ack, MeshCancelAck)
        assert ack.acknowledged is True
        assert ack.reason is None

    def test_parse_ack_2xx_defaults_to_acknowledged(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
        ack = transport.parse_ack(_cmd(), 200, None)
        assert ack.acknowledged is True

    def test_parse_ack_unacknowledged_with_reason(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
        ack = transport.parse_ack(_cmd(), 200, {"acknowledged": False, "reason": "task_done"})
        assert ack.acknowledged is False
        assert ack.reason == "task_done"

    def test_parse_ack_non_2xx_raises(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
        with pytest.raises(MeshCancelPropagationError, match="HTTP 404"):
            transport.parse_ack(_cmd(), 404, None)


# ── Transport logic against the documented local fake server ─────────────


@pytest.mark.asyncio
async def test_rpc_round_trip_against_local_fake(monkeypatch):
    """The real transport POSTs /cancel to the fake gateway and returns an ack."""
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)
    server = FakeOpenClawCancelServer()
    await server.start()
    try:
        transport = OpenClawMeshCancelTransport(endpoint=server.base_url, auth_token="tok")  # noqa: S106 - test credential
        ack = await transport.request_cancel(_cmd())
        assert ack.acknowledged is True
        assert ack.worker_id == "beta"
        assert ack.correlation_id == "corr-1"
        assert len(server.requests) == 1
        assert server.requests[0]["outbox_id"] == "o1"
        assert server.requests[0]["cancel_source"] == "user_stop"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_rpc_unacknowledged_from_local_fake(monkeypatch):
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)
    server = FakeOpenClawCancelServer(ack_payload={"acknowledged": False, "reason": "task_done"})
    await server.start()
    try:
        transport = OpenClawMeshCancelTransport(endpoint=server.base_url)
        ack = await transport.request_cancel(_cmd())
        assert ack.acknowledged is False
        assert ack.reason == "task_done"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_rpc_http_error_from_local_fake(monkeypatch):
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)
    server = FakeOpenClawCancelServer(status=500)
    await server.start()
    try:
        transport = OpenClawMeshCancelTransport(endpoint=server.base_url)
        with pytest.raises(MeshCancelPropagationError, match="HTTP 500"):
            await transport.request_cancel(_cmd())
    finally:
        await server.stop()


# ── Error / timeout handling ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transport_oserror_wraps(monkeypatch):
    """A connection-refused transport error surfaces as MeshCancelPropagationError."""
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)

    def boom(_url, _body, _token, _timeout):
        msg = "connection refused"
        raise OSError(msg)

    transport = OpenClawMeshCancelTransport(
        endpoint="http://127.0.0.1:9",
        transport=boom,
    )
    with pytest.raises(MeshCancelPropagationError, match="connection refused"):
        await transport.request_cancel(_cmd())


@pytest.mark.asyncio
async def test_transport_timeout_wraps(monkeypatch):
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)

    def slow(_url, _body, _token, _timeout):
        msg = "timed out"
        raise TimeoutError(msg)

    transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9", transport=slow)
    with pytest.raises(MeshCancelPropagationError, match="timed out"):
        await transport.request_cancel(_cmd())


@pytest.mark.asyncio
async def test_transport_timeout_against_delayed_fake(monkeypatch):
    """A slow worker that exceeds the transport timeout yields an error."""
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)
    server = FakeOpenClawCancelServer(delay=1.0)
    await server.start()
    try:
        transport = OpenClawMeshCancelTransport(endpoint=server.base_url, timeout_seconds=0.05)
        with pytest.raises(MeshCancelPropagationError):
            await transport.request_cancel(_cmd())
    finally:
        await server.stop()


# ── Endpoint policy ──────────────────────────────────────────────────────


class TestCancelRpcEndpointPolicy:
    def test_non_loopback_http_rejected(self):
        with pytest.raises(MeshCancelPropagationError, match="loopback"):
            OpenClawMeshCancelTransport(endpoint="http://worker.example/cancel")

    def test_bad_scheme_rejected(self):
        with pytest.raises(MeshCancelPropagationError):
            OpenClawMeshCancelTransport(endpoint="ftp://127.0.0.1/cancel")

    def test_https_allowed(self):
        transport = OpenClawMeshCancelTransport(endpoint="https://worker.example")
        assert transport.endpoint == "https://worker.example"

    def test_loopback_allowed(self):
        transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9/")
        assert transport.endpoint == "http://127.0.0.1:9"


# ── Gate / default-local path ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_real_route_refused_when_flag_closed():
    """With the gate closed, request_cancel refuses (no network, no side effect)."""
    transport = OpenClawMeshCancelTransport(endpoint="http://127.0.0.1:9")
    with pytest.raises(MeshCancelPropagationError, match="not approved"):
        await transport.request_cancel(_cmd())


def test_phase_b_cancel_rpc_enabled_is_false():
    assert cancel_prop.PHASE_B_CANCEL_RPC_ENABLED is False


def test_default_propagator_still_uses_fake_transport():
    """The default local path is still the in-memory fake — no network."""
    propagator = MeshCancellationPropagator()
    assert isinstance(propagator.transport, FakeMeshCancelTransport)


# ── Registry integration via the real transport ──────────────────────────


@pytest.mark.asyncio
async def test_propagator_registry_integration_with_real_transport(monkeypatch):
    """Propagator drives the real transport against the fake gateway and records ack."""
    monkeypatch.setattr(cancel_prop, "PHASE_B_CANCEL_RPC_ENABLED", True)
    server = FakeOpenClawCancelServer()
    await server.start()
    try:
        transport = OpenClawMeshCancelTransport(endpoint=server.base_url)
        propagator = MeshCancellationPropagator(transport=transport)
        result = await propagator.propagate(_outbox_entry(), "corr-1")
        assert result.propagated is True
        assert result.acknowledged is True
        assert propagator.registry.outbox_id_for("beta", "corr-1") == "o1"
        assert propagator.registry.is_acked("beta", "corr-1") is True
        assert len(server.requests) == 1
        types = [e.event_type for e in propagator.lifecycle_sink]
        assert "worker_cancel_acked" in types
    finally:
        await server.stop()
