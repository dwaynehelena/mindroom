"""Canonical Telegram delivery through the local Matrix portal room bridge.

Reuses the exact delivery pattern from ``scripts/heartbeat_broadcast.py``:

* Matrix access token from ``~/.mindroom/mindroom_data/matrix_state.yaml``
  at ``accounts.agent_user.access_token``.
* Idempotent PUT of an ``m.room.message`` to the portal room at
  ``http://127.0.0.1:8008``.

The portal room bridge forwards the message to Dwayne's Telegram DM
(8411753427).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import yaml

PORTAL_ROOM_ID = "!CzLMONAFcsUmQXAVZw:localhost"
MATRIX_ORIGIN = "http://127.0.0.1:8008"
STATE_PATH = Path.home() / ".mindroom/mindroom_data/matrix_state.yaml"
TELEGRAM_DM = 8411753427


@dataclass(slots=True)
class DeliveryReceipt:
    """Result of one delivery attempt."""

    event_id: str | None = None
    delivered: bool = False
    error: str | None = None
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def ok(self) -> bool:
        return self.delivered and self.event_id is not None


def _access_token(state_path: Path = STATE_PATH) -> str:
    state = yaml.safe_load(state_path.read_text(encoding="utf-8"))
    token = state.get("accounts", {}).get("agent_user", {}).get("access_token")
    if not isinstance(token, str) or not token:
        message = "canonical Matrix access token is unavailable"
        raise RuntimeError(message)
    return token


class TelegramDeliverer:
    """Deliver a text body to the portal room, which bridges to Telegram DM."""

    def __init__(
        self,
        *,
        room_id: str = PORTAL_ROOM_ID,
        origin: str = MATRIX_ORIGIN,
        state_path: Path = STATE_PATH,
        telegram_dm: int = TELEGRAM_DM,
    ) -> None:
        self._room_id = room_id
        self._origin = origin
        self._state_path = state_path
        self.telegram_dm = telegram_dm

    def deliver(self, body: str, *, transaction_id: str | None = None) -> DeliveryReceipt:
        """Send one idempotent message to the portal room.

        Returns a receipt carrying the Matrix ``event_id`` on success.
        """
        if transaction_id is None:
            bucket = int(datetime.now(UTC).timestamp()) // 300
            transaction_id = f"ops-autopilot-brief-{bucket}"
        try:
            token = _access_token(self._state_path)
            room = urllib.parse.quote(self._room_id, safe="")
            txn = urllib.parse.quote(transaction_id, safe="")
            url = f"{self._origin}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}"
            request = urllib.request.Request(  # noqa: S310 - fixed loopback origin only
                url,
                data=json.dumps({"msgtype": "m.text", "body": body}).encode("utf-8"),
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                method="PUT",
            )
            with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed loopback
                result = json.load(response)
        except (OSError, ValueError, urllib.error.URLError) as exc:
            return DeliveryReceipt(error=f"{type(exc).__name__}: {exc}")
        except RuntimeError as exc:
            return DeliveryReceipt(error=str(exc))

        if not isinstance(result, dict) or not isinstance(result.get("event_id"), str):
            return DeliveryReceipt(error="Matrix returned no event_id")
        return DeliveryReceipt(event_id=result["event_id"], delivered=True)