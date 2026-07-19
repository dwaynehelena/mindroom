"""Idempotent Telegram delivery for Personal Ops briefs through a Matrix portal."""

from __future__ import annotations

import asyncio
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from mindroom.personal_ops import DailyBrief, PersonalOpsError

if TYPE_CHECKING:
    from pathlib import Path

_MAX_BODY_BYTES = 12 * 1024
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
UrlOpener = Callable[..., object]


@dataclass(frozen=True, slots=True)
class MatrixBriefReceipt:
    """Content-free receipt for one exact daily-brief delivery."""

    event_id: str
    transaction_id: str
    body_digest: str


class MatrixPortalBriefSender:
    """Deliver exact daily briefs through one canonical local Matrix portal."""

    def __init__(
        self,
        *,
        origin: str,
        room_id: str,
        access_token: str,
        timeout_seconds: float = 10.0,
        opener: UrlOpener = urllib.request.urlopen,
    ) -> None:
        parsed = urllib.parse.urlsplit(origin)
        if parsed.scheme != "http" or parsed.hostname not in _LOOPBACK_HOSTS or parsed.query or parsed.fragment:
            message = "personal ops Matrix origin must be a loopback HTTP origin"
            raise PersonalOpsError(message)
        if not room_id.startswith("!") or ":" not in room_id or not access_token:
            message = "personal ops canonical Matrix room and access token are required"
            raise PersonalOpsError(message)
        if timeout_seconds <= 0:
            message = "personal ops delivery timeout must be positive"
            raise PersonalOpsError(message)
        self._origin = origin.rstrip("/")
        self._room_id = room_id
        self._access_token = access_token
        self._timeout_seconds = timeout_seconds
        self._opener = opener

    async def send(self, brief: DailyBrief) -> MatrixBriefReceipt:
        """Send one exact rendered brief idempotently and require a Matrix event receipt."""
        body = brief.render()
        encoded = body.encode()
        if not encoded or len(encoded) > _MAX_BODY_BYTES:
            message = "personal ops brief body is blank or exceeds the delivery limit"
            raise PersonalOpsError(message)
        digest = hashlib.sha256(encoded).hexdigest()
        transaction_id = f"personal-ops-{brief.display_date}-{digest[:24]}"
        room = urllib.parse.quote(self._room_id, safe="")
        transaction = urllib.parse.quote(transaction_id, safe="")
        url = f"{self._origin}/_matrix/client/v3/rooms/{room}/send/m.room.message/{transaction}"
        request = urllib.request.Request(  # noqa: S310 - constructor permits loopback HTTP origins only
            url,
            data=json.dumps({"msgtype": "m.text", "body": body}).encode(),
            headers={"Authorization": f"Bearer {self._access_token}", "Content-Type": "application/json"},
            method="PUT",
        )
        try:
            result = await asyncio.to_thread(self._send_sync, request)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            message = "personal ops brief delivery failed"
            raise PersonalOpsError(message) from exc
        if not isinstance(result, dict) or not isinstance(result.get("event_id"), str) or not result["event_id"]:
            message = "personal ops brief delivery returned no event receipt"
            raise PersonalOpsError(message)
        return MatrixBriefReceipt(result["event_id"], transaction_id, digest)

    def _send_sync(self, request: urllib.request.Request) -> object:
        with self._opener(request, timeout=self._timeout_seconds) as response:
            return json.load(response)


def load_matrix_access_token(state_path: Path) -> str:
    """Load the canonical agent token without returning unrelated Matrix state."""
    try:
        state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
        token = state.get("accounts", {}).get("agent_user", {}).get("access_token")
    except (AttributeError, OSError, ValueError, yaml.YAMLError) as exc:
        message = "canonical Matrix access token is unavailable"
        raise PersonalOpsError(message) from exc
    if not isinstance(token, str) or not token:
        message = "canonical Matrix access token is unavailable"
        raise PersonalOpsError(message)
    return token
