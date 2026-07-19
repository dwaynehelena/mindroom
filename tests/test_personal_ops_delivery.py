"""Tests for idempotent Personal Ops delivery through the Telegram Matrix portal."""

# ruff: noqa: ANN001, ANN002, ANN202, ANN204, D103, EM101, S106, TRY003

from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import pytest

from mindroom.personal_ops import DailyBrief, PersonalOpsError
from mindroom.personal_ops_delivery import MatrixPortalBriefSender, load_matrix_access_token

NOW = datetime(2026, 7, 18, 8, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


async def test_sends_exact_brief_with_stable_idempotency_and_receipt() -> None:
    requests = []

    def opener(request, *, timeout):
        requests.append((request, timeout))
        return _Response(json.dumps({"event_id": "$delivered"}).encode())

    sender = MatrixPortalBriefSender(
        origin="http://127.0.0.1:8008",
        room_id="!telegram:localhost",
        access_token="private-token",
        opener=opener,
    )
    brief = DailyBrief(datetime(2026, 7, 17, 23, tzinfo=UTC), (), (), "Australia/Sydney")
    first = await sender.send(brief)
    second = await sender.send(brief)
    assert first == second
    assert first.event_id == "$delivered"
    assert "personal-ops-2026-07-18-" in first.transaction_id
    assert first.transaction_id in requests[0][0].full_url
    assert json.loads(requests[0][0].data) == {"msgtype": "m.text", "body": brief.render()}
    assert requests[0][0].headers["Authorization"] == "Bearer private-token"


@pytest.mark.parametrize(
    "origin",
    ["https://matrix.example.test", "http://192.0.2.1:8008", "file:///tmp/socket", "http://localhost:8008?x=1"],
)
async def test_rejects_non_loopback_or_ambiguous_origin(origin) -> None:
    with pytest.raises(PersonalOpsError, match="loopback"):
        MatrixPortalBriefSender(origin=origin, room_id="!telegram:localhost", access_token="token")


async def test_sanitizes_transport_failure_and_rejects_missing_receipt() -> None:
    def failing(_request, *, timeout):
        del timeout
        raise OSError("private transport detail")

    sender = MatrixPortalBriefSender(
        origin="http://localhost:8008",
        room_id="!telegram:localhost",
        access_token="token",
        opener=failing,
    )
    with pytest.raises(PersonalOpsError, match="delivery failed") as failure:
        await sender.send(DailyBrief(NOW, (), ()))
    assert "private transport detail" not in str(failure.value)

    sender = MatrixPortalBriefSender(
        origin="http://localhost:8008",
        room_id="!telegram:localhost",
        access_token="token",
        opener=lambda *_args, **_kwargs: _Response(b"{}"),
    )
    with pytest.raises(PersonalOpsError, match="no event receipt"):
        await sender.send(DailyBrief(NOW, (), ()))


async def test_loads_only_canonical_agent_token(tmp_path) -> None:
    state = tmp_path / "matrix.yaml"
    state.write_text("accounts:\n  agent_user:\n    access_token: secret\n  other:\n    access_token: ignore\n")
    assert load_matrix_access_token(state) == "secret"
    state.write_text("accounts: {}\n")
    with pytest.raises(PersonalOpsError, match="unavailable"):
        load_matrix_access_token(state)
