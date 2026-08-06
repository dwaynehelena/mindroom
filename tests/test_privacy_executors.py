"""Tests for bounded production privacy executors."""

# ruff: noqa: ANN001, ANN002, ANN202, ANN204, ARG005, D103

from __future__ import annotations

import io
import json
from unittest.mock import AsyncMock

import pytest

from mindroom.privacy_executors import DockerJsonToolExecutor, OpenAICompatibleModelExecutor
from mindroom.privacy_handlers import PrivacyHandlerError

pytestmark = pytest.mark.asyncio
IMAGE = "python@sha256:" + "a" * 64


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


async def test_model_executor_restricts_transport_and_parses_exact_text() -> None:
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return _Response(json.dumps({"choices": [{"message": {"content": "local"}}]}).encode())

    executor = OpenAICompatibleModelExecutor(origin="http://127.0.0.1:11434/v1", model="fixed", opener=opener)
    assert await executor("private") == "local"
    assert json.loads(requests[0][0].data)["model"] == "fixed"
    with pytest.raises(ValueError, match="HTTPS or loopback"):
        OpenAICompatibleModelExecutor(origin="http://example.test/v1", model="unsafe")


async def test_docker_tool_uses_offline_unprivileged_fixed_command(monkeypatch) -> None:
    process = AsyncMock()
    process.stdin = AsyncMock()
    process.stdin.write = lambda value: None
    process.stdin.close = lambda: None
    process.stdout.read.return_value = b'{"ok":true}'
    process.wait.return_value = 0
    factory = AsyncMock(return_value=process)
    monkeypatch.setattr("mindroom.privacy_executors.asyncio.create_subprocess_exec", factory)
    executor = DockerJsonToolExecutor(
        docker_executable="/usr/local/bin/docker",
        image=IMAGE,
        command=("python", "tool.py"),
    )
    assert await executor({"value": "safe"}) == {"ok": True}
    argv = factory.call_args.args
    assert "--network=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert argv[-2:] == ("python", "tool.py")


async def test_docker_tool_rejects_failure_and_oversized_input(monkeypatch) -> None:
    executor = DockerJsonToolExecutor(
        docker_executable="/usr/local/bin/docker",
        image=IMAGE,
        command=("false",),
    )
    with pytest.raises(PrivacyHandlerError, match="size limit"):
        await executor({"value": "x" * 1_048_576})

    process = AsyncMock()
    process.stdin = AsyncMock()
    process.stdin.write = lambda value: None
    process.stdin.close = lambda: None
    process.stdout.read.return_value = b""
    process.wait.return_value = 2
    monkeypatch.setattr("mindroom.privacy_executors.asyncio.create_subprocess_exec", AsyncMock(return_value=process))
    with pytest.raises(PrivacyHandlerError, match="failed"):
        await executor({"value": "safe"})
