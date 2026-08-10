"""P9 exposure remediation test suite.

Covers the three declared change areas:
  1. Bind-address configuration (default localhost, no wildcard).
  2. Node allowlist enforcement for enrollment (allowed vs denied nodes).
  3. Regression: Ed25519 / HMAC / nonce / clock-skew enforcement unchanged.

NOTE: This file encodes the *intended* P9 behavior.  If a change area is not
yet implemented in the code, its tests will FAIL — that is the defect signal.
"""

# ruff: noqa: ANN001, ANN201, ANN202, D103

from __future__ import annotations

import base64
import inspect
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mindroom.api.edge_fleet import create_edge_fleet_router
from mindroom.edge_fleet import (
    EdgeFleet,
    EdgeFleetError,
    EnrollmentAuthority,
    node_request_attestation_payload,
)

NOW = datetime(2026, 8, 10, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


def _keys():
    private = Ed25519PrivateKey.generate()
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    public = base64.urlsafe_b64encode(raw).decode().rstrip("=")
    return private, public


# ---------------------------------------------------------------------------
# Area 1 — Bind-address configuration (default localhost, no wildcard)
# ---------------------------------------------------------------------------

def test_cli_run_api_host_default_is_loopback() -> None:
    """P9-1: the CLI `run` command's `--api-host` default must be 127.0.0.1."""
    from mindroom.cli import main as cli_main

    source = inspect.getsource(cli_main)
    # Isolate the `run` command body (the api_host option default).
    run_body = source.split("def run(", 1)[1].split("def _load_active_config_or_exit", 1)[0]
    assert '"127.0.0.1"' in run_body, "CLI `run` api-host default is not loopback"


def test_orchestrator_api_host_default_is_loopback() -> None:
    """P9-2: the orchestrator `main()` api_host default must be 127.0.0.1."""
    from mindroom import orchestrator

    source = inspect.getsource(orchestrator)
    assert 'api_host: str = "127.0.0.1"' in source, "orchestrator api-host default is not loopback"


def test_no_wildcard_bind_default_anywhere() -> None:
    """P9-3: no default bind to 0.0.0.0 / :: (wildcard) in CLI or orchestrator."""
    from mindroom import orchestrator
    from mindroom.cli import main as cli_main

    for module in (cli_main, orchestrator):
        source = inspect.getsource(module)
        assert '"0.0.0.0"' not in source, f"wildcard bind default present in {module.__name__}"


# ---------------------------------------------------------------------------
# Area 2 — Node allowlist enforcement for enrollment
# ---------------------------------------------------------------------------

def test_enrollment_surface_has_allowlist_parameter() -> None:
    """P9-4: the edge-fleet router factory must accept a node allowlist."""
    from mindroom.api.edge_fleet import create_edge_fleet_router

    sig = inspect.signature(create_edge_fleet_router)
    params = set(sig.parameters)
    assert any("allow" in name for name in params), (
        f"create_edge_fleet_router has no allowlist parameter (got {sorted(params)})"
    )


async def test_denied_node_enrollment_is_rejected(tmp_path) -> None:
    """P9-5: a node NOT on the allowlist must be denied at enrollment."""
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "denied.db", authority)
    await fleet.open()
    try:
        private, public = _keys()
        token = authority.issue(
            node_id="denied-node",
            runtime="openclaw",
            public_key=public,
            capabilities=("notify",),
            expires_at=NOW + timedelta(minutes=5),
        )
        # If an allowlist existed, a denied node would be rejected here.
        with pytest.raises(EdgeFleetError, match="allowlist|denied|not allowed"):
            await fleet.enroll(token, observed_at=NOW)
    finally:
        await fleet.close()


async def test_allowed_node_enrollment_succeeds(tmp_path) -> None:
    """P9-6: a node ON the allowlist must be admitted at enrollment."""
    authority = EnrollmentAuthority(b"e" * 32)
    fleet = EdgeFleet(tmp_path / "allowed.db", authority, node_allowlist=frozenset({"allowed-node"}))
    await fleet.open()
    try:
        private, public = _keys()
        token = authority.issue(
            node_id="allowed-node",
            runtime="openclaw",
            public_key=public,
            capabilities=("notify",),
            expires_at=NOW + timedelta(minutes=5),
        )
        node = await fleet.enroll(token, observed_at=NOW)
        assert node.node_id == "allowed-node"
    finally:
        await fleet.close()


# ---------------------------------------------------------------------------
# Area 3 — Regression: Ed25519 / HMAC / nonce / clock-skew unchanged
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def fleet(tmp_path):
    authority = EnrollmentAuthority(b"e" * 32)
    value = EdgeFleet(tmp_path / "fleet.db", authority, node_allowlist=frozenset({"node-1"}))
    await value.open()
    yield value, authority
    await value.close()


async def test_regression_hmac_enrollment_signature_verified(fleet) -> None:
    """P9-R1: HMAC-SHA256 enrollment signature is still verified."""
    value, authority = fleet
    _private, public = _keys()
    token = authority.issue(
        node_id="node-1", runtime="openclaw", public_key=public,
        capabilities=("notify",), expires_at=NOW + timedelta(minutes=5),
    )
    with pytest.raises(EdgeFleetError, match=r"malformed|signature"):
        await value.enroll(token[:-1] + ("A" if token[-1] != "A" else "B"), observed_at=NOW)


async def test_regression_ed25519_request_attestation_verified(fleet) -> None:
    """P9-R2: Ed25519 request attestation is still verified."""
    value, authority = fleet
    private, public = _keys()
    token = authority.issue(
        node_id="node-1", runtime="openclaw", public_key=public,
        capabilities=("notify",), expires_at=NOW + timedelta(minutes=5),
    )
    await value.enroll(token, observed_at=NOW)
    body = {"capabilities": ["notify"]}
    path = "/api/edge-fleet/heartbeat"
    sig = base64.urlsafe_b64encode(
        private.sign(node_request_attestation_payload(
            node_id="node-1", method="POST", path=path, body=body,
            timestamp=NOW, nonce="nonce-1",
        )),
    ).decode().rstrip("=")
    await value.authenticate_request(
        node_id="node-1", method="POST", path=path, body=body,
        timestamp=NOW, nonce="nonce-1", signature=sig, observed_at=NOW,
    )
    with pytest.raises(EdgeFleetError, match="already consumed"):
        await value.authenticate_request(
            node_id="node-1", method="POST", path=path, body=body,
            timestamp=NOW, nonce="nonce-1", signature=sig, observed_at=NOW,
        )


async def test_regression_clock_skew_still_enforced(fleet) -> None:
    """P9-R3: 5-minute clock-skew window is still enforced."""
    value, authority = fleet
    private, public = _keys()
    token = authority.issue(
        node_id="node-1", runtime="openclaw", public_key=public,
        capabilities=("notify",), expires_at=NOW + timedelta(minutes=5),
    )
    await value.enroll(token, observed_at=NOW)
    body = {}
    sig = base64.urlsafe_b64encode(
        private.sign(node_request_attestation_payload(
            node_id="node-1", method="POST", path="/api/edge-fleet/lease",
            body=body, timestamp=NOW, nonce="nonce-old",
        )),
    ).decode().rstrip("=")
    with pytest.raises(EdgeFleetError, match="clock skew"):
        await value.authenticate_request(
            node_id="node-1", method="POST", path="/api/edge-fleet/lease",
            body=body, timestamp=NOW, nonce="nonce-old", signature=sig,
            observed_at=NOW + timedelta(minutes=6),
        )