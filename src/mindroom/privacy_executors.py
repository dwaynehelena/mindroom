"""Bounded production executors for governed model and isolated-tool routes."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from typing import TYPE_CHECKING, cast

from mindroom.privacy_handlers import PrivacyHandlerError

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import AbstractContextManager
    from typing import IO

    from pydantic import JsonValue

    UrlOpener = Callable[..., AbstractContextManager[IO[bytes]]]

_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_DIGEST_IMAGE = re.compile(r"^[a-z0-9./_-]+@sha256:[0-9a-f]{64}$")
_MAX_JSON_BYTES = 1_048_576
_DEFAULT_OPENER = cast("UrlOpener", urllib.request.urlopen)


class OpenAICompatibleModelExecutor:
    """Call one fixed OpenAI-compatible model over HTTPS or loopback HTTP."""

    def __init__(
        self,
        *,
        origin: str,
        model: str,
        api_key: str | None = None,
        timeout_seconds: float = 120,
        opener: UrlOpener = _DEFAULT_OPENER,
    ) -> None:
        parsed = urllib.parse.urlsplit(origin)
        secure = parsed.scheme == "https"
        loopback = parsed.scheme == "http" and parsed.hostname in _LOOPBACK
        if (
            not (secure or loopback)
            or parsed.query
            or parsed.fragment
            or not model.strip()
            or timeout_seconds <= 0
            or timeout_seconds > 600
            or (secure and not api_key)
        ):
            message = "privacy model executor requires HTTPS or loopback HTTP, a fixed model, and bounded timeout"
            raise ValueError(message)
        self._url = f"{origin.rstrip('/')}/chat/completions"
        self._model = model
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._opener = opener

    async def __call__(self, prompt: str) -> str:
        """Return text from one non-streaming fixed-model request."""
        payload = json.dumps(
            {"messages": [{"content": prompt, "role": "user"}], "model": self._model, "stream": False},
            separators=(",", ":"),
        ).encode()
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        request = urllib.request.Request(  # noqa: S310 - constructor permits only HTTPS or loopback HTTP
            self._url,
            data=payload,
            headers=headers,
            method="POST",
        )
        try:
            content = _model_content(await asyncio.to_thread(self._send, request))
        except (TypeError, OSError, ValueError, urllib.error.URLError) as exc:
            message = "privacy model executor request failed"
            raise PrivacyHandlerError(message) from exc
        if not isinstance(content, str):
            message = "privacy model executor returned invalid content"
            raise PrivacyHandlerError(message)
        return content

    def _send(self, request: urllib.request.Request) -> object:
        with self._opener(request, timeout=self._timeout) as response:
            return json.load(response)


class DockerJsonToolExecutor:
    """Execute one fixed JSON-in/JSON-out command in a pinned offline container."""

    def __init__(
        self,
        *,
        docker_executable: str,
        image: str,
        command: tuple[str, ...],
        timeout_seconds: float = 60,
    ) -> None:
        if (
            not docker_executable.startswith("/")
            or _DIGEST_IMAGE.fullmatch(image) is None
            or not command
            or any(not part for part in command)
            or timeout_seconds <= 0
            or timeout_seconds > 600
        ):
            message = "privacy Docker tool requires an absolute CLI, pinned image, fixed argv, and bounded timeout"
            raise ValueError(message)
        self._docker = docker_executable
        self._image = image
        self._command = command
        self._timeout = timeout_seconds

    async def __call__(self, arguments: Mapping[str, JsonValue]) -> JsonValue:
        """Run once without shell, network, privilege, or writable root access."""
        try:
            payload = json.dumps(dict(arguments), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError) as exc:
            message = "privacy Docker tool arguments must be finite JSON"
            raise PrivacyHandlerError(message) from exc
        if len(payload) > _MAX_JSON_BYTES:
            message = "privacy Docker tool arguments exceed the size limit"
            raise PrivacyHandlerError(message)
        process = await asyncio.create_subprocess_exec(
            *self._argv(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        assert process.stdin is not None
        assert process.stdout is not None
        process.stdin.write(payload)
        await process.stdin.drain()
        process.stdin.close()
        try:
            output = await asyncio.wait_for(process.stdout.read(_MAX_JSON_BYTES + 1), timeout=self._timeout)
            return_code = await asyncio.wait_for(process.wait(), timeout=self._timeout)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            message = "privacy Docker tool timed out"
            raise PrivacyHandlerError(message) from exc
        if return_code != 0 or len(output) > _MAX_JSON_BYTES:
            message = "privacy Docker tool failed or exceeded its output limit"
            raise PrivacyHandlerError(message)
        try:
            return cast("JsonValue", json.loads(output))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            message = "privacy Docker tool returned invalid JSON"
            raise PrivacyHandlerError(message) from exc

    def _argv(self) -> tuple[str, ...]:
        return (
            self._docker,
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=64",
            "--memory=256m",
            "--cpus=1",
            "--user=65534:65534",
            "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=16m",
            "-i",
            self._image,
            *self._command,
        )


def _model_content(value: object) -> str:
    if not isinstance(value, Mapping):
        raise TypeError
    data = cast("Mapping[str, object]", value)
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise TypeError
    choice = cast("Mapping[str, object]", choices[0])
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise TypeError
    message_data = cast("Mapping[str, object]", message)
    if not isinstance(message_data.get("content"), str):
        raise TypeError
    content = message_data["content"]
    return cast("str", content)
