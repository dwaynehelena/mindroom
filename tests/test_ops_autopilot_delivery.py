"""Unit tests for the ops-autopilot Telegram/Matrix portal delivery (idempotent)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from mindroom.ops_autopilot.delivery.telegram import TelegramDeliverer


def _state_file(tmp_path: Path, token: str = "tok123") -> Path:
    path = tmp_path / "matrix_state.yaml"
    path.write_text(
        "accounts:\n  agent_user:\n    access_token: %s\n" % token,
        encoding="utf-8",
    )
    return path


def _deliverer(tmp_path: Path, **kw) -> TelegramDeliverer:
    kw.setdefault("room_id", "!room:localhost")
    kw.setdefault("origin", "http://127.0.0.1:8008")
    kw.setdefault("state_path", _state_file(tmp_path))
    return TelegramDeliverer(**kw)


class _Resp:
    def __init__(self, payload) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()


def test_deliver_success(tmp_path: Path) -> None:
    d = _deliverer(tmp_path)
    seen: dict[str, str] = {}

    def fake_urlopen(request, timeout=10):  # noqa: ARG001
        seen["url"] = request.full_url
        seen["body"] = request.data.decode()
        return _Resp({"event_id": "$e1"})

    with patch("mindroom.ops_autopilot.delivery.telegram.urllib.request.urlopen", fake_urlopen):
        receipt = d.deliver("hello")

    assert receipt.ok is True
    assert receipt.event_id == "$e1"
    assert json.loads(seen["body"])["body"] == "hello"


def test_deliver_is_idempotent_by_transaction_bucket(tmp_path: Path) -> None:
    d = _deliverer(tmp_path)
    urls = []

    def fake_urlopen(request, timeout=10):  # noqa: ARG001
        urls.append(request.full_url)
        return _Resp({"event_id": "$e1"})

    with patch("mindroom.ops_autopilot.delivery.telegram.urllib.request.urlopen", fake_urlopen):
        d.deliver("one")
        d.deliver("two")

    # Same transaction id within the same 5-minute bucket => same PUT target URL.
    assert len(urls) == 2
    assert urls[0] == urls[1]


def test_deliver_explicit_transaction_id(tmp_path: Path) -> None:
    d = _deliverer(tmp_path)
    captured = {}

    def fake_urlopen(request, timeout=10):  # noqa: ARG001
        captured["url"] = request.full_url
        return _Resp({"event_id": "$e1"})

    with patch("mindroom.ops_autopilot.delivery.telegram.urllib.request.urlopen", fake_urlopen):
        d.deliver("x", transaction_id="my-txn")

    assert "/m.room.message/my-txn" in captured["url"]


def test_deliver_network_error_returns_failed_receipt(tmp_path: Path) -> None:
    d = _deliverer(tmp_path)

    def boom(request, timeout=10):  # noqa: ARG001
        raise OSError("connection refused")

    with patch("mindroom.ops_autopilot.delivery.telegram.urllib.request.urlopen", boom):
        receipt = d.deliver("x")
    assert receipt.ok is False
    assert receipt.error is not None


def test_deliver_missing_token_returns_failed_receipt(tmp_path: Path) -> None:
    state_path = tmp_path / "matrix_state.yaml"
    state_path.write_text("accounts: {}\n", encoding="utf-8")
    d = TelegramDeliverer(state_path=state_path)
    receipt = d.deliver("x")
    assert receipt.ok is False
    assert "token is unavailable" in (receipt.error or "")


def test_deliver_missing_event_id_fails(tmp_path: Path) -> None:
    d = _deliverer(tmp_path)

    def fake_urlopen(request, timeout=10):  # noqa: ARG001
        return _Resp({"foo": "bar"})

    with patch("mindroom.ops_autopilot.delivery.telegram.urllib.request.urlopen", fake_urlopen):
        receipt = d.deliver("x")
    assert receipt.ok is False
    assert "no event_id" in (receipt.error or "")


def test_deliver_http_error_returns_failed_receipt(tmp_path: Path) -> None:
    import urllib.error

    d = _deliverer(tmp_path)

    def boom(request, timeout=10):  # noqa: ARG001
        raise urllib.error.URLError("bad gateway")

    with patch("mindroom.ops_autopilot.delivery.telegram.urllib.request.urlopen", boom):
        receipt = d.deliver("x")
    assert receipt.ok is False
    assert receipt.error is not None


def test_receipt_ok_property() -> None:
    from mindroom.ops_autopilot.delivery.telegram import DeliveryReceipt

    assert DeliveryReceipt().ok is False
    assert DeliveryReceipt(event_id="$e", delivered=True).ok is True
    assert DeliveryReceipt(event_id="$e", delivered=False).ok is False


def test_deliverer_defaults() -> None:
    d = TelegramDeliverer()
    assert d.telegram_dm == 8411753427