"""Cancellation-safe adapter protocol and explicit NDJSON subprocess transport."""
# ruff: noqa: ANN401, D102, D105, EM101, EM102, N818, TC003, TRY003

from __future__ import annotations

import asyncio
import json
import os
import signal
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .models import CONTRACT_VERSION, RuntimeIdentity, RuntimeRequest, RuntimeResult, normalized_json_bytes

_MAX_REQUEST_BYTES = 512 * 1024
_MAX_RESPONSE_BYTES = 512 * 1024
_REQUIRED_RESPONSE_KEYS = frozenset({"version", "type", "request_id", "text", "state"})


class RuntimeContractError(RuntimeError):
    """The configured subprocess violated the bridge contract."""


class ConsequentialExecutionDenied(RuntimeContractError):
    """The subprocess requested tool or consequential execution, which is denied."""


@runtime_checkable
class RuntimeAdapter(Protocol):
    """Common adapter API; cancellation must stop the process session descendants."""

    @property
    def identity(self) -> RuntimeIdentity: ...

    async def invoke(self, request: RuntimeRequest) -> RuntimeResult: ...

    async def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class SubprocessContractConfig:
    """Operator-supplied executable argv for the documented NDJSON v1 contract."""

    argv: tuple[str, ...]
    timeout_seconds: float = 120.0
    max_attempts: int = 1
    extra_env: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        if not self.argv or not self.argv[0] or any(not isinstance(arg, str) or "\x00" in arg for arg in self.argv):
            raise ValueError("argv must contain valid strings and an executable")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_attempts != 1:
            raise ValueError("external invocation max_attempts must be 1 until upstream idempotency is evidenced")


class NDJSONSubprocessAdapter:
    """One contained subprocess session per invocation; no retries or shell."""

    def __init__(self, identity: RuntimeIdentity, config: SubprocessContractConfig) -> None:
        self._identity = identity
        self._config = config
        self._closed = False
        self._active: set[asyncio.subprocess.Process] = set()
        self._active_lock = asyncio.Lock()

    @property
    def identity(self) -> RuntimeIdentity:
        """Return the configured stable internal runtime identity."""
        return self._identity

    async def invoke(self, request: RuntimeRequest) -> RuntimeResult:
        """Invoke once; an uncertain outcome is surfaced and never replayed automatically."""
        if self._closed:
            raise RuntimeError("runtime adapter is closed")
        try:
            async with asyncio.timeout(self._config.timeout_seconds):
                return await self._invoke_once(request)
        except (OSError, EOFError, TimeoutError) as exc:
            raise RuntimeContractError(f"runtime transport failed: {type(exc).__name__}") from exc

    async def close(self) -> None:
        """Prevent new calls and terminate every active process session."""
        self._closed = True
        async with self._active_lock:
            processes = tuple(self._active)
        await asyncio.gather(*(self._stop_process_group(process) for process in processes))

    async def _invoke_once(self, request: RuntimeRequest) -> RuntimeResult:
        payload = normalized_json_bytes(_request_payload(self._identity, request)) + b"\n"
        if len(payload) > _MAX_REQUEST_BYTES:
            raise RuntimeContractError("runtime request exceeds the byte limit")
        process = await asyncio.create_subprocess_exec(
            *self._config.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=_minimal_env(self._config.extra_env),
            start_new_session=True,
        )
        async with self._active_lock:
            if self._closed:
                await self._stop_process_group(process)
                raise RuntimeError("runtime adapter is closed")
            self._active.add(process)
        try:
            assert process.stdin is not None
            assert process.stdout is not None
            process.stdin.write(payload)
            await process.stdin.drain()
            process.stdin.close()
            line = await process.stdout.readline()
            if not line:
                raise EOFError
            if len(line) > _MAX_RESPONSE_BYTES or not line.endswith(b"\n"):
                raise RuntimeContractError("runtime response exceeds the bounded NDJSON line size")
            result = _parse_response(line, request.source_event_id)
            trailing = await process.stdout.read(1)
            if trailing:
                raise RuntimeContractError("runtime returned more than one response line")
            return_code = await process.wait()
            if return_code != 0:
                raise RuntimeContractError(f"runtime contract process exited with status {return_code}")
            return result
        finally:
            await asyncio.shield(self._stop_process_group(process))
            async with self._active_lock:
                self._active.discard(process)

    @staticmethod
    async def _stop_process_group(process: asyncio.subprocess.Process) -> None:
        """Terminate then kill the isolated POSIX process group, including descendants."""
        if process.returncode is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            await process.wait()
            return
        except PermissionError:
            # macOS can reap the short-lived group leader between returncode
            # observation and killpg. Preserve the original error only when
            # the child is now authoritatively exited.
            await asyncio.sleep(0)
            if process.returncode is not None:
                return
            raise
        try:
            async with asyncio.timeout(2):
                await process.wait()
        except TimeoutError:
            pass
        finally:
            # The group leader can exit before a descendant. Kill any remaining
            # members even when process.wait() completed after SIGTERM.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


def _request_payload(identity: RuntimeIdentity, request: RuntimeRequest) -> dict[str, Any]:
    return {
        "version": CONTRACT_VERSION,
        "type": "invoke",
        "request_id": request.source_event_id,
        "runtime": identity.runtime.value,
        "instance": identity.instance,
        "session_id": request.session_id,
        "input": {"text": request.text, "state": dict(request.state)},
        "policy": {"allow_tools": False, "allow_consequential_execution": False},
    }


def _parse_response(line: bytes, request_id: str) -> RuntimeResult:
    try:
        value = json.loads(line, parse_constant=_reject_json_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise RuntimeContractError("runtime returned invalid strict NDJSON") from exc
    if not isinstance(value, dict) or set(value) != _REQUIRED_RESPONSE_KEYS:
        raise RuntimeContractError("runtime returned an invalid exact response shape")
    if value["version"] != CONTRACT_VERSION or value["type"] != "final":
        raise RuntimeContractError("runtime returned an unsupported contract message")
    if value["request_id"] != request_id:
        raise RuntimeContractError("runtime response request_id mismatch")
    try:
        return RuntimeResult(text=value["text"], state=value["state"])
    except (TypeError, ValueError) as exc:
        raise RuntimeContractError("runtime final text/state is invalid") from exc


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"nonfinite JSON constant rejected: {value}")


def _minimal_env(extra_env: Mapping[str, str] | None) -> dict[str, str]:
    allowed = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    if extra_env:
        allowed.update(extra_env)
    return allowed
