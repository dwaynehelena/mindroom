"""Tailscale helper must fail the fleet op, not log and continue (FR1.4)."""

from __future__ import annotations

import pytest

from mindroom.edge_tailscale import (
    TailscaleCheckResult,
    TailscaleUnavailableError,
    check_tailscale_connectivity,
    require_tailscale,
)


@pytest.mark.asyncio
async def test_require_tailscale_raises_when_binary_missing(monkeypatch) -> None:
    async def missing_binary(*, timeout_seconds: float = 10.0) -> TailscaleCheckResult:
        return TailscaleCheckResult(status="unknown", error="tailscale binary not found on PATH")

    monkeypatch.setattr("mindroom.edge_tailscale.check_tailscale_connectivity", missing_binary)
    with pytest.raises(TailscaleUnavailableError, match="tailnet check failed"):
        await require_tailscale()


@pytest.mark.asyncio
async def test_require_tailscale_raises_when_disconnected(monkeypatch) -> None:
    async def disconnected(*, timeout_seconds: float = 10.0) -> TailscaleCheckResult:
        return TailscaleCheckResult(status="disconnected", error="exit code 1")

    monkeypatch.setattr("mindroom.edge_tailscale.check_tailscale_connectivity", disconnected)
    with pytest.raises(TailscaleUnavailableError, match="exit code 1"):
        await require_tailscale()


@pytest.mark.asyncio
async def test_require_tailscale_returns_connected_result(monkeypatch) -> None:
    connected = TailscaleCheckResult(status="connected", node_name="node", tailscale_ip="100.64.0.1", online_nodes=1)

    async def ok(*, timeout_seconds: float = 10.0) -> TailscaleCheckResult:
        return connected

    monkeypatch.setattr("mindroom.edge_tailscale.check_tailscale_connectivity", ok)
    assert await require_tailscale() == connected


@pytest.mark.asyncio
async def test_check_tailscale_connectivity_unknown_without_binary(monkeypatch) -> None:
    monkeypatch.setattr("mindroom.edge_tailscale.shutil.which", lambda _name: None)
    result = await check_tailscale_connectivity()
    assert result.status == "unknown"
    assert result.error is not None