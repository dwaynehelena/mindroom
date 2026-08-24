"""Tests for the packaged authenticated OpenClaw/Hermes edge-node client."""

# ruff: noqa: ANN001, ANN202, D103

from __future__ import annotations

import json
from datetime import datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from mindroom.edge_fleet import node_request_attestation_payload, result_attestation_payload
from mindroom.edge_node import EdgeNodeClient, EdgeNodeError, EdgeNodeIdentity, SubprocessJobExecutor, _unb64

pytestmark = pytest.mark.asyncio


def _identity(tmp_path):
    path = tmp_path / "node.json"
    return path, EdgeNodeIdentity.generate(
        path,
        node_id="openclaw-node-1",
        runtime="openclaw",
        capabilities=("notify",),
    )


async def test_identity_is_private_round_trippable_and_exclusive(tmp_path) -> None:
    path, identity = _identity(tmp_path)
    assert path.stat().st_mode & 0o777 == 0o600
    loaded = EdgeNodeIdentity.load(path)
    assert loaded.node_id == identity.node_id
    assert loaded.public_key == identity.public_key
    with pytest.raises(EdgeNodeError, match="exclusively"):
        EdgeNodeIdentity.generate(path, node_id="other", runtime="hermes", capabilities=("research",))
    path.chmod(0o644)
    with pytest.raises(EdgeNodeError, match="0600"):
        EdgeNodeIdentity.load(path)


async def test_client_rejects_cleartext_remote_api(tmp_path) -> None:
    _path, identity = _identity(tmp_path)
    with pytest.raises(EdgeNodeError, match="HTTPS"):
        EdgeNodeClient(base_url="http://192.0.2.1:8765", identity=identity)


async def test_signed_heartbeat_and_fresh_nonces(tmp_path) -> None:
    _path, identity = _identity(tmp_path)
    requests = []

    def transport(request, _timeout):
        requests.append(request)
        return 200, {"node_id": identity.node_id}

    client = EdgeNodeClient(base_url="http://127.0.0.1:8765", identity=identity, transport=transport)
    await client.heartbeat()
    await client.heartbeat()
    assert requests[0].headers["X-edge-nonce"] != requests[1].headers["X-edge-nonce"]
    request = requests[0]
    body = json.loads(request.data)
    timestamp = datetime.fromisoformat(request.headers["X-edge-timestamp"])
    payload = node_request_attestation_payload(
        node_id=identity.node_id,
        method="POST",
        path="/api/edge-fleet/heartbeat",
        body=body,
        timestamp=timestamp,
        nonce=request.headers["X-edge-nonce"],
    )
    Ed25519PublicKey.from_public_bytes(_unb64(identity.public_key)).verify(
        _unb64(request.headers["X-edge-signature"]), payload,
    )


async def test_run_once_attests_exact_result_and_handles_empty_queue(tmp_path) -> None:
    _path, identity = _identity(tmp_path)
    requests = []
    lease = {
        "job_id": "job-1",
        "lease_id": "lease-1",
        "payload": {"message": "hello"},
        "expires_at": "2026-07-18T00:01:00+00:00",
    }

    def transport(request, _timeout):
        requests.append(request)
        if request.full_url.endswith("/lease"):
            return 200, lease
        return 204, None

    async def execute(payload):
        assert payload == {"message": "hello"}
        return {"delivered": True}

    client = EdgeNodeClient(base_url="https://fleet.example.test", identity=identity, transport=transport)
    assert await client.run_once(execute) is True
    completed = json.loads(requests[-1].data)
    Ed25519PublicKey.from_public_bytes(_unb64(identity.public_key)).verify(
        _unb64(completed["result_signature"]),
        result_attestation_payload("job-1", "lease-1", {"delivered": True}),
    )

    empty = EdgeNodeClient(
        base_url="https://fleet.example.test",
        identity=identity,
        transport=lambda _request, _timeout: (200, None),
    )
    assert await empty.run_once(execute) is False


async def test_subprocess_executor_uses_bounded_stdin_stdout_protocol() -> None:
    executor = SubprocessJobExecutor(
        (
            "python3",
            "-c",
            "import json,sys; value=json.load(sys.stdin); print(json.dumps({'seen': value['job']}))",
        ),
    )
    assert await executor({"job": "safe"}) == {"seen": "safe"}

    oversized = SubprocessJobExecutor(("python3", "-c", "print('{}')"), max_output_bytes=2)
    with pytest.raises(EdgeNodeError, match="output bound"):
        await oversized({})
