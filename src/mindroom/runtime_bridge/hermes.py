"""Fail-closed Hermes 0.18.2 numeric-loopback HTTP adapter."""
# ruff: noqa: D102, E702, EM101, EM102, TC003, TRY003

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import stat
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from .adapter import RuntimeAdapter, RuntimeContractError
from .models import MAX_TEXT_BYTES, RuntimeIdentity, RuntimeName, RuntimeRequest, RuntimeResult

_MAX_HTTP_BYTES = 512 * 1024
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_TERMINAL_STOP = frozenset({"cancelled", "stopped", "failed", "error", "completed", "succeeded", "done"})


class HermesAdapter(RuntimeAdapter):
    """Authenticated bounded API usage except the deliberately public health probe."""

    def __init__(
        self,
        *,
        instance: str,
        endpoint: str | None = None,
        approved_port: int | None = None,
        api_key_env: str | None = None,
        api_key_file: Path | None = None,
        secret_root: Path | None = None,
        timeout_seconds: float = 120.0,
        endpoint_allowlist: tuple[str, ...] = (),
        expected_version: str | None = None,
        config: object | None = None,
    ) -> None:
        if config is not None:
            from .adapter import NDJSONSubprocessAdapter, SubprocessContractConfig  # noqa: PLC0415

            if not isinstance(config, SubprocessContractConfig):
                raise TypeError("config must be SubprocessContractConfig")
            self._legacy = NDJSONSubprocessAdapter(RuntimeIdentity(RuntimeName.HERMES, instance), config)
        else:
            self._legacy = None
        if endpoint is None and config is None:
            raise ValueError("Hermes endpoint is required")
        resolved_endpoint = (endpoint or "http://127.0.0.1").rstrip("/")
        if config is None:
            _validate_endpoint(resolved_endpoint, approved_port, endpoint_allowlist)
        if bool(api_key_env) == bool(api_key_file) and config is None:
            raise ValueError("configure exactly one Hermes API key env or file secret reference")
        if api_key_env not in {None, "API_SERVER_KEY"}:
            raise ValueError("Hermes env secret reference must be API_SERVER_KEY")
        self._secret_root: Path | None = None
        self._secret_parts: tuple[str, ...] = ()
        if api_key_file is not None:
            self._secret_root, self._secret_parts = _secret_reference(api_key_file, secret_root)
            _read_secret(self._secret_root, self._secret_parts)
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("Hermes timeout_seconds must be between 1 and 3600")
        self._identity = RuntimeIdentity(RuntimeName.HERMES, instance)
        self._endpoint = resolved_endpoint
        self._api_key_env = api_key_env
        self._api_key_file = api_key_file
        self._timeout = timeout_seconds
        self._expected_version = expected_version
        limits = httpx.Limits(max_connections=8, max_keepalive_connections=4)
        timeout = httpx.Timeout(connect=3, read=min(timeout_seconds, 10), write=5, pool=3)
        self._client = httpx.AsyncClient(timeout=timeout, limits=limits, trust_env=False, follow_redirects=False)
        self._closed = False
        self._active_runs: set[str] = set()
        self._lock = asyncio.Lock()

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    async def preflight(self) -> None:
        """Require secret availability and exact health/capability evidence."""
        if self._legacy is not None:
            return
        if not self._expected_version:
            raise RuntimeContractError("Hermes exact expected version is required")
        health = _bounded_json(await self._request("GET", "/health", authenticated=False))
        if not isinstance(health, dict) or health.get("status") != "ok" or health.get("version") != self._expected_version:
            raise RuntimeContractError("Hermes health/version contract mismatch")
        capabilities = await self.capabilities()
        runtime = capabilities.get("runtime")
        features = capabilities.get("features")
        endpoints = capabilities.get("endpoints")
        if (
            capabilities.get("object") != "hermes.api_server.capabilities"
            or capabilities.get("platform") != "hermes-agent"
            or not isinstance(runtime, dict)
            or runtime.get("tool_execution") != "disabled"
            or not isinstance(features, dict)
            or features.get("run_submission") is not True
            or not isinstance(endpoints, dict)
            or endpoints.get("runs") != {"method": "POST", "path": "/v1/runs"}
        ):
            raise RuntimeContractError("Hermes capabilities do not prove the required deny-all run contract")

    async def health(self) -> bool:
        response = await self._request("GET", "/health", authenticated=False)
        return response.is_success

    async def capabilities(self) -> dict[str, Any]:
        value = _bounded_json(await self._request("GET", "/v1/capabilities"))
        if not isinstance(value, dict):
            raise RuntimeContractError("Hermes capabilities response is not an object")
        return value

    async def invoke(self, request: RuntimeRequest) -> RuntimeResult:
        if self._legacy is not None:
            return await self._legacy.invoke(request)
        if self._closed:
            raise RuntimeError("runtime adapter is closed")
        created = _bounded_json(
            await self._request(
                "POST",
                "/v1/runs",
                json={"input": request.text, "session_id": request.session_id},
                session_key=request.session_id,
            ),
        )
        run_id = _required_string(created, "run_id", fallback="id")
        if not _RUN_ID.fullmatch(run_id):
            raise RuntimeContractError("Hermes run_id violates strict path-segment grammar")
        async with self._lock:
            self._active_runs.add(run_id)
        try:
            async with asyncio.timeout(self._timeout):
                while True:
                    status = _bounded_json(await self._request("GET", f"/v1/runs/{run_id}"))
                    state = _required_string(status, "status")
                    if state in {"completed", "succeeded", "done"}:
                        return RuntimeResult(text=_result_text(status), state={"run_id": run_id})
                    if state in {"failed", "cancelled", "stopped", "error"}:
                        raise RuntimeContractError("Hermes run failed")
                    await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            await asyncio.shield(self._stop(run_id))
            raise
        except TimeoutError as exc:
            await self._stop(run_id)
            raise RuntimeContractError("Hermes invocation timed out") from exc
        finally:
            async with self._lock:
                self._active_runs.discard(run_id)

    async def close(self) -> None:
        if self._legacy is not None:
            await self._legacy.close()
            return
        self._closed = True
        async with self._lock:
            active = tuple(self._active_runs)
        await asyncio.gather(*(self._stop(run_id) for run_id in active), return_exceptions=True)
        await self._client.aclose()

    async def _stop(self, run_id: str) -> None:
        await self._request("POST", f"/v1/runs/{run_id}/stop")
        async with asyncio.timeout(10):
            while True:
                status = _bounded_json(await self._request("GET", f"/v1/runs/{run_id}"))
                if _required_string(status, "status") in _TERMINAL_STOP:
                    return
                await asyncio.sleep(0.25)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        json: object = None,
        session_key: str | None = None,
    ) -> httpx.Response:
        headers = self._headers() if authenticated else {}
        if session_key is not None:
            headers["X-Hermes-Session-Key"] = session_key
        response = await self._client.request(method, f"{self._endpoint}{path}", headers=headers, json=json)
        if 300 <= response.status_code < 400:
            raise RuntimeContractError("Hermes redirects are forbidden")
        response.raise_for_status()
        if len(response.content) > _MAX_HTTP_BYTES:
            raise RuntimeContractError("Hermes response exceeds byte limit")
        return response

    def _headers(self) -> dict[str, str]:
        if self._api_key_env is not None:
            secret = os.environ.get(self._api_key_env)
        else:
            assert self._secret_root is not None
            secret = _read_secret(self._secret_root, self._secret_parts)
        if not secret or len(secret.encode()) > 4096:
            raise RuntimeError("Hermes API secret reference is unavailable")
        return {"Authorization": f"Bearer {secret}"}


def _validate_endpoint(endpoint: str, approved_port: int | None, allowlist: tuple[str, ...]) -> None:
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("Hermes endpoint must be plain HTTP numeric loopback origin")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as exc:
        raise ValueError("Hermes endpoint host must be numeric loopback") from exc
    if not address.is_loopback or parsed.port is None or parsed.port != approved_port:
        raise ValueError("Hermes endpoint must use the exact approved loopback port")
    if endpoint.rstrip("/") not in {item.rstrip("/") for item in allowlist}:
        raise ValueError("Hermes endpoint must be exactly allowlisted")


def _secret_reference(path: Path, root: Path | None) -> tuple[Path, tuple[str, ...]]:
    if not path.is_absolute() or root is None or not root.is_absolute():
        raise ValueError("Hermes secret file and approved root must be absolute")
    if any(part in {"", ".", ".."} for part in path.parts[1:]):
        raise ValueError("Hermes secret path contains a forbidden component")
    root_resolved = root.resolve(strict=True)
    if root.is_symlink() or not path.is_relative_to(root_resolved):
        raise ValueError("Hermes secret file must be beneath the approved root")
    parts = path.relative_to(root_resolved).parts
    if not parts:
        raise ValueError("Hermes secret reference must name a file")
    return root_resolved, parts


def _read_secret(root: Path, parts: tuple[str, ...]) -> str:
    """Open every component relative to a retained root fd with no-follow semantics."""
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    dirfd = os.open(root, flags | getattr(os, "O_DIRECTORY", 0))
    try:
        for part in parts[:-1]:
            nextfd = os.open(part, flags | getattr(os, "O_DIRECTORY", 0), dir_fd=dirfd)
            os.close(dirfd); dirfd = nextfd
        fd = os.open(parts[-1], flags, dir_fd=dirfd)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
                raise ValueError("Hermes secret requires current owner and mode 0600 or stricter")
            if not 0 < info.st_size <= 4096:
                raise ValueError("Hermes secret file size is invalid")
            raw = os.read(fd, 4097)
        finally:
            os.close(fd)
    except OSError as exc:
        raise ValueError("Hermes secret path must contain no symlinks") from exc
    finally:
        os.close(dirfd)
    try:
        secret = raw.decode().strip()
    except UnicodeDecodeError as exc:
        raise ValueError("Hermes secret must be UTF-8") from exc
    if not secret:
        raise ValueError("Hermes secret is blank")
    return secret


def _bounded_json(response: httpx.Response) -> object:
    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeContractError("Hermes returned invalid JSON") from exc


def _required_string(value: object, key: str, *, fallback: str | None = None) -> str:
    if not isinstance(value, dict):
        raise RuntimeContractError("Hermes response is not an object")
    candidate = value.get(key)
    if not isinstance(candidate, str) and fallback is not None:
        candidate = value.get(fallback)
    if not isinstance(candidate, str) or not candidate or len(candidate.encode()) > 512:
        raise RuntimeContractError(f"Hermes response has no valid {key}")
    return candidate


def _result_text(value: object) -> str:
    if not isinstance(value, dict):
        raise RuntimeContractError("Hermes status response is not an object")
    for key in ("text", "response", "output", "result"):
        candidate = value.get(key)
        if isinstance(candidate, str) and len(candidate.encode()) <= MAX_TEXT_BYTES:
            return candidate
        if isinstance(candidate, dict):
            for nested in ("text", "response", "output"):
                text = candidate.get(nested)
                if isinstance(text, str) and len(text.encode()) <= MAX_TEXT_BYTES:
                    return text
    raise RuntimeContractError("Hermes completed run has no supported final text field")
