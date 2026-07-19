"""Fail-closed concrete write boundaries for Personal Ops."""

# ruff: noqa: EM101, EM102, TRY003

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from mindroom.arip import canonical_json
from mindroom.personal_ops import ActionExecutor, OpsAction, PersonalOpsError, WorkerRuntime

if TYPE_CHECKING:
    from mindroom.config.personal_ops import PersonalOpsConfig

ToolInvoker = Callable[[str, str, Mapping[str, object]], Awaitable[object]]


def _arguments(action: OpsAction) -> Mapping[str, object]:
    if not isinstance(action.arguments, dict):
        raise PersonalOpsError("write action arguments must be an object")
    return cast("Mapping[str, object]", action.arguments)


def _receipt(runtime: str, idempotency_key: str, result: object) -> str:
    """Return a content-free receipt bound to the action and bounded result."""
    try:
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    except (TypeError, ValueError) as exc:
        raise PersonalOpsError("write executor returned a non-JSON result") from exc
    if len(encoded) > 1024 * 1024:
        raise PersonalOpsError("write executor result exceeds size limit")
    digest = hashlib.sha256(encoded).hexdigest()
    return f"{runtime}:{idempotency_key}:{digest}"


@dataclass(frozen=True, slots=True)
class MindRoomActionExecutor:
    """Invoke one explicitly allowlisted MindRoom tool function."""

    invoker: ToolInvoker
    allowed_tools: frozenset[str]

    async def __call__(self, action: OpsAction, idempotency_key: str) -> str:
        """Execute one exact approved MindRoom write."""
        if action.runtime != "mindroom" or action.tool_name not in self.allowed_tools:
            raise PersonalOpsError("MindRoom write target is not allowlisted")
        tool_name, separator, function_name = action.tool_name.partition(".")
        if not separator or not tool_name or not function_name:
            raise PersonalOpsError("MindRoom write target must be tool.function")
        result = await self.invoker(tool_name, function_name, _arguments(action))
        return _receipt("mindroom", idempotency_key, result)


@dataclass(frozen=True, slots=True)
class OpenClawGatewayExecutor:
    """Send an allowlisted OpenClaw RPC through a private stdin frame."""

    gateway_url: str
    token_env: str
    allowed_methods: frozenset[str]
    timeout_seconds: float
    worker_path: Path
    node_path: str = "node"
    openclaw_path: str = "openclaw"

    async def __call__(self, action: OpsAction, idempotency_key: str) -> str:
        """Execute one exact approved OpenClaw gateway write."""
        if action.runtime != "openclaw" or action.tool_name not in self.allowed_methods:
            raise PersonalOpsError("OpenClaw write method is not allowlisted")
        token = os.environ.get(self.token_env)
        if not token:
            raise PersonalOpsError(f"OpenClaw gateway token is unavailable in {self.token_env}")
        params = dict(_arguments(action))
        supplied_key = params.get("idempotencyKey")
        if supplied_key not in (None, idempotency_key):
            raise PersonalOpsError("OpenClaw idempotency key equivocation denied")
        params["idempotencyKey"] = idempotency_key
        executable = shutil.which(self.openclaw_path)
        if executable is None:
            raise PersonalOpsError("OpenClaw executable is unavailable")
        request = {
            "url": self.gateway_url,
            "token": token,
            "method": action.tool_name,
            "params": params,
            "timeoutMs": round(self.timeout_seconds * 1000),
            "packageRoot": str(Path(executable).resolve().parent),
        }
        process = await asyncio.create_subprocess_exec(
            self.node_path,
            str(self.worker_path),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        frame = canonical_json(request) + b"\n"
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(frame), timeout=self.timeout_seconds + 2)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if len(stdout) > 1024 * 1024 or len(stderr) > 64 * 1024:
            raise PersonalOpsError("OpenClaw gateway worker output exceeds size limit")
        try:
            response = json.loads(stdout)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PersonalOpsError("OpenClaw gateway worker returned an invalid response") from exc
        if process.returncode != 0 or not isinstance(response, dict) or response.get("ok") is not True:
            raise PersonalOpsError("OpenClaw gateway write failed")
        return _receipt("openclaw", idempotency_key, response.get("result"))


def build_action_executors(
    config: PersonalOpsConfig,
    *,
    invoker: ToolInvoker,
    repository_root: Path,
) -> dict[WorkerRuntime, ActionExecutor]:
    """Build both concrete executors from one validated config snapshot."""
    if config.openclaw_gateway_url is None:
        raise PersonalOpsError("OpenClaw gateway URL is required")
    return {
        "mindroom": MindRoomActionExecutor(invoker, frozenset(config.mindroom_write_tools)),
        "openclaw": OpenClawGatewayExecutor(
            config.openclaw_gateway_url,
            config.openclaw_token_env,
            frozenset(config.openclaw_write_methods),
            config.execution_timeout_seconds,
            repository_root / "scripts" / "openclaw_gateway_worker.mjs",
        ),
    }
