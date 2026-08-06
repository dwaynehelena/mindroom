"""Tailscale connectivity check for edge fleet operations.

Standing directive: all edge fleet operations must verify Tailscale
connectivity before any remote API calls or deployment actions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import sys
from dataclasses import dataclass
from typing import Literal

logger = logging.getLogger("mindroom.edge_tailscale")

TailscaleStatus = Literal["connected", "disconnected", "unknown"]


@dataclass(frozen=True, slots=True)
class TailscaleCheckResult:
    """Result of a Tailscale connectivity check."""

    status: TailscaleStatus
    node_name: str | None = None
    tailscale_ip: str | None = None
    online_nodes: int = 0
    error: str | None = None


async def check_tailscale_connectivity(
    *,
    timeout_seconds: float = 10.0,
) -> TailscaleCheckResult:
    """Check Tailscale connectivity by running `tailscale status`.

    Returns a structured result with the current connection state.
    This is a non-blocking check suitable for health endpoints and
    pre-flight validation.
    """
    tailscale_bin = shutil.which("tailscale")
    if tailscale_bin is None:
        return TailscaleCheckResult(
            status="unknown",
            error="tailscale binary not found on PATH",
        )

    try:
        process = await asyncio.create_subprocess_exec(
            tailscale_bin,
            "status",
            "--json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            return TailscaleCheckResult(
                status="unknown",
                error="tailscale status timed out",
            )

        if process.returncode != 0:
            return TailscaleCheckResult(
                status="disconnected",
                error=stderr.decode().strip() or f"exit code {process.returncode}",
            )

        data = json.loads(stdout)
        self_info = data.get("Self", {})
        online = sum(
            1
            for peer in data.get("Peer", {}).values()
            if peer.get("Online", False)
        )

        return TailscaleCheckResult(
            status="connected",
            node_name=self_info.get("DNSName", "").rstrip(".") or None,
            tailscale_ip=next(iter(self_info.get("TailscaleIPs", [])), None),
            online_nodes=online,
        )

    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return TailscaleCheckResult(
            status="unknown",
            error=str(exc),
        )


async def require_tailscale(
    *,
    timeout_seconds: float = 10.0,
) -> TailscaleCheckResult:
    """Check Tailscale connectivity and raise if not connected.

    Use this as a pre-flight guard before any edge fleet operation
    that requires remote node access.
    """
    result = await check_tailscale_connectivity(timeout_seconds=timeout_seconds)
    if result.status != "connected":
        logger.error(
            "Tailscale connectivity check failed",
            extra={"status": result.status, "error": result.error},
        )
    return result


def format_tailscale_result(result: TailscaleCheckResult) -> str:
    """Format a Tailscale check result for human-readable output."""
    if result.status == "connected":
        parts = [
            f"✅ Tailscale connected",
            f"   Node: {result.node_name or 'unknown'}",
            f"   IP: {result.tailscale_ip or 'unknown'}",
            f"   Online peers: {result.online_nodes}",
        ]
        return "\n".join(parts)
    elif result.status == "disconnected":
        return f"❌ Tailscale disconnected: {result.error or 'no error'}"
    else:
        return f"⚠️  Tailscale status unknown: {result.error or 'no error'}"