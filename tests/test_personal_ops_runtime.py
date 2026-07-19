"""Tests for schedule-safe Personal Ops runtime composition."""

# ruff: noqa: ANN001, ANN003, ANN201, ANN202, D103, EM101, S106, TRY003

from __future__ import annotations

import io
import json
import urllib.error
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio

from mindroom.arip_control import ApprovalControlStore
from mindroom.personal_ops import PersonalOpsAutopilot, PersonalOpsError, PersonalOpsStore
from mindroom.personal_ops_delivery import MatrixPortalBriefSender
from mindroom.personal_ops_runtime import BriefDeliveryLedger, PersonalOpsBriefRunner, source_reader

if TYPE_CHECKING:
    from types import TracebackType

NOW = datetime(2026, 7, 18, 8, tzinfo=UTC)
pytestmark = pytest.mark.asyncio


class _Response(io.BytesIO):
    def __enter__(self) -> _Response:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        self.close()


@pytest_asyncio.fixture
async def runtime(tmp_path):
    ops = PersonalOpsStore(tmp_path / "ops.db")
    approvals = ApprovalControlStore(tmp_path / "approvals.db")
    ledger = BriefDeliveryLedger(tmp_path / "briefs.db")
    await ops.open()
    await approvals.open()
    await ledger.open()
    yield ops, approvals, ledger
    await ledger.close()
    await approvals.close()
    await ops.close()


async def _unused_executor(_action, _key):
    return "unused"


def _readers(state):
    async def fetch(source, _observed_at):
        if source == "mail" and state.get("mail_fails"):
            raise RuntimeError("private connector failure")
        return [
            {
                "item_id": f"{source}-1",
                "summary": f"{source} {state.get('suffix', 'summary')}",
                "observed_at": NOW.isoformat(),
                "importance": 10,
            },
        ]

    return {
        source: source_reader(source, lambda observed, source=source: fetch(source, observed))
        for source in ("mail", "calendar", "tasks", "github")
    }


def _runner(runtime, state, opener):
    ops, approvals, ledger = runtime
    autopilot = PersonalOpsAutopilot(
        store=ops,
        approval_store=approvals,
        readers=_readers(state),
        executors={"mindroom": _unused_executor, "openclaw": _unused_executor},
        display_timezone="Australia/Sydney",
    )
    sender = MatrixPortalBriefSender(
        origin="http://127.0.0.1:8008",
        room_id="!telegram:test",
        access_token="secret",
        opener=opener,
    )
    return PersonalOpsBriefRunner(autopilot=autopilot, sender=sender, ledger=ledger)


async def test_runner_delivers_once_and_reuses_durable_receipt(runtime) -> None:
    requests = []

    def opener(request, **_kwargs):
        requests.append(request)
        return _Response(json.dumps({"event_id": "$brief"}).encode())

    runner = _runner(runtime, {}, opener)
    first = await runner.run(NOW)
    second = await runner.run(NOW)
    assert first.newly_delivered is True
    assert second.newly_delivered is False
    assert second.receipt == first.receipt
    assert len(requests) == 1


async def test_runner_retries_same_matrix_transaction_after_known_send_failure(runtime) -> None:
    calls = 0
    transactions = []

    def opener(request, **_kwargs):
        nonlocal calls
        calls += 1
        transactions.append(request.full_url.rsplit("/", 1)[-1])
        if calls == 1:
            raise urllib.error.URLError("offline")
        return _Response(json.dumps({"event_id": "$brief"}).encode())

    runner = _runner(runtime, {}, opener)
    with pytest.raises(PersonalOpsError, match="delivery failed"):
        await runner.run(NOW)
    outcome = await runner.run(NOW)
    assert outcome.newly_delivered is True
    assert calls == 2
    assert transactions[0] == transactions[1]


async def test_runner_denies_second_body_for_same_local_date(runtime) -> None:
    state = {}

    def opener(_request, **_kwargs):
        return _Response(json.dumps({"event_id": "$brief"}).encode())

    runner = _runner(runtime, state, opener)
    await runner.run(NOW)
    state["suffix"] = "changed"
    with pytest.raises(PersonalOpsError, match="equivocation"):
        await runner.run(NOW)


async def test_runner_delivers_degraded_brief_without_connector_details(runtime) -> None:
    bodies = []

    def opener(request, **_kwargs):
        bodies.append(json.loads(request.data)["body"])
        return _Response(json.dumps({"event_id": "$brief"}).encode())

    outcome = await _runner(runtime, {"mail_fails": True}, opener).run(NOW)
    assert "Unavailable sources: mail" in outcome.brief.render()
    assert "private connector failure" not in bodies[0]


@pytest.mark.parametrize(
    "raw",
    [
        [{"item_id": "1", "summary": "x", "observed_at": "naive"}],
        [{"item_id": "1", "summary": "x", "observed_at": NOW.isoformat(), "extra": True}],
        [{"item_id": "1", "summary": "x", "observed_at": NOW.isoformat(), "importance": True}],
    ],
)
async def test_source_reader_rejects_malformed_external_items(raw) -> None:
    async def fetch(_observed_at):
        return raw

    with pytest.raises(PersonalOpsError):
        await source_reader("tasks", fetch)(NOW)
