"""Packaged authenticated HTTP client for OpenClaw and Hermes edge nodes."""

# Identity parsing validates inside one shared sanitized-error boundary.
# ruff: noqa: TRY301

from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets
import stat
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from mindroom.edge_fleet import node_request_attestation_payload, result_attestation_payload

if TYPE_CHECKING:
    from pathlib import Path

Runtime = Literal["openclaw", "hermes"]
JobExecutor = Callable[[dict[str, object]], Awaitable[dict[str, object]]]
HttpTransport = Callable[[urllib.request.Request, float], tuple[int, object | None]]
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})


class EdgeNodeError(RuntimeError):
    """A node identity, transport, protocol, or execution invariant failed."""


@dataclass(frozen=True, slots=True)
class EdgeNodeIdentity:
    """One private node identity stored in a mode-0600 file."""

    node_id: str
    runtime: Runtime
    capabilities: tuple[str, ...]
    private_key: Ed25519PrivateKey

    @classmethod
    def generate(
        cls,
        path: Path,
        *,
        node_id: str,
        runtime: Runtime,
        capabilities: tuple[str, ...],
    ) -> EdgeNodeIdentity:
        """Generate and exclusively persist a new node identity."""
        if not node_id or runtime not in {"openclaw", "hermes"} or any(not value for value in capabilities):
            message = "edge node identity, runtime, and capabilities are invalid"
            raise EdgeNodeError(message)
        if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
            message = "edge node identity path must use an existing absolute real directory"
            raise EdgeNodeError(message)
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        payload = json.dumps(
            {
                "capabilities": list(dict.fromkeys(capabilities)),
                "node_id": node_id,
                "private_key": _b64(raw),
                "runtime": runtime,
                "schema": "mindroom.edge-node/1",
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            message = "edge node identity file could not be created exclusively"
            raise EdgeNodeError(message) from exc
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        return cls(node_id, runtime, tuple(dict.fromkeys(capabilities)), private_key)

    @classmethod
    def load(cls, path: Path) -> EdgeNodeIdentity:
        """Load an exact private identity only when filesystem permissions are safe."""
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
                message = "edge node identity file must be regular and mode 0600"
                raise EdgeNodeError(message)
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict) or set(value) != {
                "capabilities",
                "node_id",
                "private_key",
                "runtime",
                "schema",
            }:
                message = "edge node identity shape is invalid"
                raise EdgeNodeError(message)
            if (
                not isinstance(value["node_id"], str)
                or value["runtime"] not in {"openclaw", "hermes"}
                or not isinstance(value["capabilities"], list)
                or any(not isinstance(item, str) or not item for item in value["capabilities"])
            ):
                message = "edge node identity content is invalid"
                raise EdgeNodeError(message)
            raw = _unb64(value["private_key"])
            identity = cls(
                str(value["node_id"]),
                value["runtime"],
                tuple(value["capabilities"]),
                Ed25519PrivateKey.from_private_bytes(raw),
            )
        except EdgeNodeError:
            raise
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            message = "edge node identity could not be loaded"
            raise EdgeNodeError(message) from exc
        if (
            value["schema"] != "mindroom.edge-node/1"
            or not identity.node_id
            or len(raw) != 32
        ):
            message = "edge node identity content is invalid"
            raise EdgeNodeError(message)
        return identity

    @property
    def public_key(self) -> str:
        """Return the URL-safe raw Ed25519 public key for coordinator enrollment."""
        raw = self.private_key.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        return _b64(raw)


class EdgeNodeClient:
    """Enroll, heartbeat, lease and attest results over the Edge Fleet API."""

    def __init__(
        self,
        *,
        base_url: str,
        identity: EdgeNodeIdentity,
        timeout_seconds: float = 15.0,
        transport: HttpTransport | None = None,
    ) -> None:
        parsed = urllib.parse.urlsplit(base_url)
        secure_remote = parsed.scheme == "https"
        loopback = parsed.scheme == "http" and parsed.hostname in _LOOPBACK
        if not (secure_remote or loopback) or parsed.query or parsed.fragment or timeout_seconds <= 0:
            message = "edge node API must use HTTPS or loopback HTTP with a positive timeout"
            raise EdgeNodeError(message)
        self._base_url = base_url.rstrip("/")
        self._identity = identity
        self._timeout = timeout_seconds
        self._transport = transport or _urllib_transport

    async def enroll(self, token: str) -> None:
        """Consume a coordinator-issued enrollment token."""
        status, response = await self._post("/enroll", {"token": token}, authenticated=False)
        if status != 200 or not isinstance(response, dict) or response.get("node_id") != self._identity.node_id:
            message = "edge node enrollment was rejected"
            raise EdgeNodeError(message)

    async def heartbeat(self) -> None:
        """Advertise the identity's configured capabilities with a fresh attestation."""
        status, response = await self._post(
            "/heartbeat",
            {"capabilities": list(self._identity.capabilities)},
            authenticated=True,
        )
        if status != 200 or not isinstance(response, dict) or response.get("node_id") != self._identity.node_id:
            message = "edge node heartbeat was rejected"
            raise EdgeNodeError(message)

    async def run_once(self, executor: JobExecutor, *, lease_seconds: int = 60) -> bool:
        """Lease at most one job, execute it once, and submit an attested result."""
        if lease_seconds < 1 or lease_seconds > 3600:
            message = "edge node lease duration must be between 1 and 3600 seconds"
            raise EdgeNodeError(message)
        status, lease = await self._post("/lease", {"lease_seconds": lease_seconds}, authenticated=True)
        if status != 200:
            message = "edge node lease request was rejected"
            raise EdgeNodeError(message)
        if lease is None:
            return False
        if not isinstance(lease, dict) or set(lease) != {"job_id", "lease_id", "payload", "expires_at"}:
            message = "edge node lease response shape is invalid"
            raise EdgeNodeError(message)
        if (
            not isinstance(lease["job_id"], str)
            or not isinstance(lease["lease_id"], str)
            or not isinstance(lease["payload"], dict)
            or not isinstance(lease["expires_at"], str)
        ):
            message = "edge node lease response content is invalid"
            raise EdgeNodeError(message)
        result = await executor(lease["payload"])
        if not isinstance(result, dict):
            message = "edge node executor must return a JSON object"
            raise EdgeNodeError(message)
        result_signature = _b64(
            self._identity.private_key.sign(result_attestation_payload(lease["job_id"], lease["lease_id"], result)),
        )
        body = {
            "job_id": lease["job_id"],
            "lease_id": lease["lease_id"],
            "lease_expires_at": lease["expires_at"],
            "result": result,
            "result_signature": result_signature,
        }
        complete_status, _response = await self._post("/complete", body, authenticated=True)
        if complete_status != 204:
            message = "edge node result completion was rejected"
            raise EdgeNodeError(message)
        return True

    async def _post(
        self,
        endpoint: str,
        body: dict[str, object],
        *,
        authenticated: bool,
    ) -> tuple[int, object | None]:
        path = f"/api/edge-fleet{endpoint}"
        headers = {"Content-Type": "application/json"}
        if authenticated:
            timestamp = datetime.now(UTC)
            nonce = secrets.token_hex(16)
            signature = self._identity.private_key.sign(
                node_request_attestation_payload(
                    node_id=self._identity.node_id,
                    method="POST",
                    path=path,
                    body=body,
                    timestamp=timestamp,
                    nonce=nonce,
                ),
            )
            headers.update(
                {
                    "X-Edge-Node-ID": self._identity.node_id,
                    "X-Edge-Timestamp": timestamp.isoformat(),
                    "X-Edge-Nonce": nonce,
                    "X-Edge-Signature": _b64(signature),
                },
            )
        request = urllib.request.Request(  # noqa: S310 - constructor enforces HTTPS or loopback HTTP
            self._base_url + path,
            data=json.dumps(body, separators=(",", ":"), sort_keys=True).encode(),
            headers=headers,
            method="POST",
        )
        try:
            return await asyncio.to_thread(self._transport, request, self._timeout)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            message = "edge node API transport failed"
            raise EdgeNodeError(message) from exc


class SubprocessJobExecutor:
    """Execute one lease through an explicit bounded NDJSON worker command."""

    def __init__(
        self,
        argv: tuple[str, ...],
        *,
        timeout_seconds: float = 300.0,
        max_output_bytes: int = 1_048_576,
    ) -> None:
        if not argv or any(not value for value in argv) or timeout_seconds <= 0 or max_output_bytes < 2:
            message = "edge node worker command, timeout, or output bound is invalid"
            raise EdgeNodeError(message)
        self._argv = argv
        self._timeout = timeout_seconds
        self._max_output_bytes = max_output_bytes

    async def __call__(self, payload: dict[str, object]) -> dict[str, object]:
        """Send exact JSON on stdin and require one bounded JSON-object result on stdout."""
        process = await asyncio.create_subprocess_exec(
            *self._argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            message = "edge node worker timed out"
            raise EdgeNodeError(message) from exc
        if process.returncode != 0 or len(stdout) > self._max_output_bytes:
            message = "edge node worker failed or exceeded its output bound"
            raise EdgeNodeError(message)
        try:
            result = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            message = "edge node worker returned invalid JSON"
            raise EdgeNodeError(message) from exc
        if not isinstance(result, dict):
            message = "edge node worker must return one JSON object"
            raise EdgeNodeError(message)
        return result


def _urllib_transport(request: urllib.request.Request, timeout: float) -> tuple[int, object | None]:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - URL was validated by client
            return response.status, json.load(response) if response.status != 204 else None
    except urllib.error.HTTPError as exc:
        return exc.code, None


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: object) -> bytes:
    if not isinstance(value, str) or not value or "=" in value:
        message = "edge node key encoding is invalid"
        raise EdgeNodeError(message)
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
    except ValueError as exc:
        message = "edge node key encoding is invalid"
        raise EdgeNodeError(message) from exc
    if _b64(decoded) != value:
        message = "edge node key encoding is non-canonical"
        raise EdgeNodeError(message)
    return decoded
