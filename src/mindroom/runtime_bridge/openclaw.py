"""Fail-closed OpenClaw adapter with immutable deployment attestation."""
# ruff: noqa: D102, E701, E702, EM101, TRY003

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import signal
import stat
from contextlib import suppress
from pathlib import Path

from .adapter import RuntimeAdapter, RuntimeContractError
from .models import RuntimeIdentity, RuntimeName, RuntimeRequest, RuntimeResult

_MAX_OUTPUT_BYTES = 512 * 1024
_ATTESTATION_KEYS = frozenset({"schema", "agent_id", "openclaw_version", "node_version", "tools", "hooks", "channels", "mcp"})


class OpenClawAdapter(RuntimeAdapter):
    """Invoke only after a strict dedicated-agent deny-all artifact is verified."""

    def __init__(self, *, instance: str, agent_id: str | None = None, executable: str = "",
                 timeout_seconds: int = 120, executable_allowlist: tuple[str, ...] = (),
                 executable_sha256: str | None = None, expected_version: str | None = None,
                 expected_node_version: str | None = None, deny_all_attestation: Path | None = None,
                 config: object | None = None) -> None:
        if config is not None:
            from .adapter import NDJSONSubprocessAdapter, SubprocessContractConfig  # noqa: PLC0415
            if not isinstance(config, SubprocessContractConfig):
                raise TypeError("config must be SubprocessContractConfig")
            self._legacy = NDJSONSubprocessAdapter(RuntimeIdentity(RuntimeName.OPENCLAW, instance), config)
        else:
            self._legacy = None
            if not agent_id:
                raise ValueError("OpenClaw agent_id is required")
            _validate_executable(executable, executable_allowlist, executable_sha256)
        if timeout_seconds <= 0 or timeout_seconds > 3600:
            raise ValueError("OpenClaw timeout_seconds must be between 1 and 3600")
        self._identity = RuntimeIdentity(RuntimeName.OPENCLAW, instance)
        self._agent_id, self._executable, self._timeout = agent_id or "", executable, timeout_seconds
        self._expected_version, self._expected_node_version = expected_version, expected_node_version
        self._attestation = deny_all_attestation
        self._closed = False
        self._active: set[asyncio.subprocess.Process] = set()
        self._lock = asyncio.Lock()

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    async def preflight(self) -> None:
        """Verify offline evidence; never execute the incompatible live CLI."""
        if self._legacy is not None:
            return
        if not self._expected_version or not self._expected_node_version or self._attestation is None:
            raise RuntimeContractError("OpenClaw deny-all/version attestation is required")
        value = _read_attestation(self._attestation)
        if set(value) != _ATTESTATION_KEYS or value != {
            "schema": "mindroom.openclaw.deny-all.v1", "agent_id": self._agent_id,
            "openclaw_version": self._expected_version, "node_version": self._expected_node_version,
            "tools": [], "hooks": [], "channels": [], "mcp": [],
        }:
            raise RuntimeContractError("OpenClaw attestation does not prove the exact dedicated-agent deny-all contract")

    async def invoke(self, request: RuntimeRequest) -> RuntimeResult:
        if self._legacy is not None:
            return await self._legacy.invoke(request)
        if self._closed:
            raise RuntimeError("runtime adapter is closed")
        # Prompt-in-argv is an explicit upstream residual blocker.
        argv = (self._executable, "agent", "--agent", self._agent_id, "--session-key", request.session_id,
                "--message", request.text, "--json", "--timeout", str(self._timeout))
        process = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
            env={k: os.environ[k] for k in ("HOME", "TMPDIR", "LANG", "LC_ALL") if k in os.environ},
            start_new_session=True)
        async with self._lock:
            if self._closed:
                await _stop_group(process)
                raise RuntimeError("runtime adapter is closed")
            self._active.add(process)
        try:
            async with asyncio.timeout(self._timeout + 5):
                stdout, _ = await process.communicate()
            if process.returncode != 0 or len(stdout) > _MAX_OUTPUT_BYTES:
                raise RuntimeContractError("OpenClaw invocation failed")
            value = json.loads(stdout)
            text = value.get("text") if isinstance(value, dict) else None
            if not isinstance(text, str):
                raise RuntimeContractError("OpenClaw JSON response has no exact final text")
            return RuntimeResult(text=text)
        except (TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeContractError("OpenClaw invocation contract failed") from exc
        finally:
            await asyncio.shield(_stop_group(process))
            async with self._lock:
                self._active.discard(process)

    async def close(self) -> None:
        if self._legacy is not None:
            await self._legacy.close(); return
        self._closed = True
        async with self._lock:
            active = tuple(self._active)
        await asyncio.gather(*(_stop_group(p) for p in active))


def _validate_executable(executable: str, allowlist: tuple[str, ...], expected_sha256: str | None) -> None:
    path = Path(executable)
    if not path.is_absolute() or str(path) not in allowlist or path.is_symlink():
        raise ValueError("OpenClaw executable must be an exact allowlisted absolute non-symlink")
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or not os.access(path, os.X_OK):
        raise ValueError("OpenClaw executable must be an executable regular file")
    # Service-UID ownership/writability is forbidden; root ownership is portable proof.
    if info.st_uid != 0 or info.st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise ValueError("OpenClaw artifact must be root-owned and immutable to the service UID")
    if not expected_sha256 or len(expected_sha256) != 64:
        raise ValueError("OpenClaw executable requires a SHA-256 pin")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("OpenClaw executable changed during validation")
        digest = hashlib.sha256()
        while chunk := os.read(fd, 1024 * 1024): digest.update(chunk)
    finally:
        os.close(fd)
    if digest.hexdigest() != expected_sha256.lower():
        raise ValueError("OpenClaw executable hash mismatch")


def _read_attestation(path: Path) -> dict[str, object]:
    if not path.is_absolute() or path.is_symlink():
        raise RuntimeContractError("OpenClaw attestation must be an absolute non-symlink")
    info = path.lstat()
    if info.st_uid != 0 or info.st_mode & 0o222 or not stat.S_ISREG(info.st_mode) or info.st_size > 16384:
        raise RuntimeContractError("OpenClaw attestation is not an immutable root-owned artifact")
    try:
        value = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeContractError("OpenClaw attestation is unreadable") from exc
    if not isinstance(value, dict):
        raise RuntimeContractError("OpenClaw attestation is not an object")
    return value


async def _stop_group(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None: return
    with suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGTERM)
    try:
        async with asyncio.timeout(2): await process.wait(); return
    except TimeoutError: pass
    with suppress(ProcessLookupError): os.killpg(process.pid, signal.SIGKILL)
    await process.wait()
