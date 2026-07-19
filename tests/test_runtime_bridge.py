"""Focused tests for the external runtime bridge foundation."""
# ruff: noqa: ANN001, ANN201, ANN202, D102, D103, EM101, PT018, TC003

from __future__ import annotations

import asyncio
import json
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import httpx
import pytest
import pytest_asyncio

from mindroom.message_target import MessageTarget
from mindroom.runtime_bridge import (
    CONTRACT_VERSION,
    ConversationScope,
    DuplicateSourceEventError,
    EventOrigin,
    HermesAdapter,
    OpenClawAdapter,
    RuntimeBridgeService,
    RuntimeBridgeStore,
    RuntimeContractError,
    RuntimeIdentity,
    RuntimeName,
    RuntimeRequest,
    RuntimeResult,
    SourceEventConflictError,
    SubprocessContractConfig,
)
from mindroom.runtime_bridge.hermes import _RUN_ID
from mindroom.runtime_bridge.integration import _assert_human_ingress
from mindroom.turn_origin import SenderKind, TurnIntent, TurnOrigin, TurnTrust

if TYPE_CHECKING:
    from collections.abc import Mapping

pytestmark = pytest.mark.asyncio


class FakeAdapter:
    """Cancellation-safe in-memory contract fake."""

    def __init__(self) -> None:
        self._identity = RuntimeIdentity(RuntimeName.HERMES, "test")
        self.requests: list[RuntimeRequest] = []

    @property
    def identity(self) -> RuntimeIdentity:
        return self._identity

    async def invoke(self, request: RuntimeRequest) -> RuntimeResult:
        self.requests.append(request)
        return RuntimeResult(text=f"echo:{request.text}", state={"session": request.session_id})

    async def close(self) -> None:
        return


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    bridge_store = RuntimeBridgeStore(tmp_path / "runtime-bridge.sqlite3")
    await bridge_store.open()
    yield bridge_store
    await bridge_store.close()


async def test_service_maps_scope_and_deduplicates(store: RuntimeBridgeStore) -> None:
    adapter = FakeAdapter()
    service = RuntimeBridgeService(store)
    kwargs = {
        "adapter": adapter,
        "source_event_id": "$event",
        "origin": EventOrigin.HUMAN,
        "scope": ConversationScope("!room:example.org", "$thread"),
        "text": "hello",
    }

    result = await service.forward(**kwargs)

    assert result.text == "echo:hello"
    assert adapter.requests[0].session_id.startswith("mrb1_")
    lifecycle = await store.lifecycle("$event")
    assert lifecycle is not None
    assert lifecycle[0] == "response_ready"
    assert lifecycle[1] is not None and lifecycle[2] is not None
    assert lifecycle[3] is None
    with pytest.raises(DuplicateSourceEventError):
        await service.forward(**kwargs)
    assert len(adapter.requests) == 1


async def test_failure_is_sanitized_and_not_replayed(store: RuntimeBridgeStore) -> None:
    class FailingAdapter(FakeAdapter):
        async def invoke(self, request: RuntimeRequest) -> RuntimeResult:
            self.requests.append(request)
            raise RuntimeError("secret-token-must-not-be-persisted")

    adapter = FailingAdapter()
    service = RuntimeBridgeService(store)
    kwargs = {
        "adapter": adapter,
        "source_event_id": "$failed",
        "origin": EventOrigin.HUMAN,
        "scope": ConversationScope("!room:example.org"),
        "text": "hello",
    }
    with pytest.raises(RuntimeError, match="secret-token"):
        await service.forward(**kwargs)
    lifecycle = await store.lifecycle("$failed")
    assert lifecycle is not None and lifecycle[0] == "failed"
    assert lifecycle[3] == "builtins.RuntimeError"
    with pytest.raises(DuplicateSourceEventError):
        await service.forward(**kwargs)
    assert len(adapter.requests) == 1


async def test_session_mapping_is_stable_across_store_reopen(tmp_path: Path) -> None:
    path = tmp_path / "bridge.sqlite3"
    identity = RuntimeIdentity(RuntimeName.OPENCLAW, "primary")
    scope = ConversationScope("!room:example.org")
    first = RuntimeBridgeStore(path)
    await first.open()
    session_one, _ = await first.reserve_source_event(identity=identity, scope=scope, source_event_id="$one")
    await first.close()
    second = RuntimeBridgeStore(path)
    await second.open()
    session_two, _ = await second.reserve_source_event(identity=identity, scope=scope, source_event_id="$two")
    await second.close()

    assert session_one == session_two


async def test_v1_database_migrates_lifecycle_without_replay(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite3"
    with sqlite3.connect(path) as db:
        db.executescript(
            """PRAGMA user_version=1;
            CREATE TABLE sessions (
                runtime_key TEXT NOT NULL, room_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                session_id TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (runtime_key, room_id, thread_id));
            CREATE TABLE source_events (
                source_event_id TEXT PRIMARY KEY, runtime_key TEXT NOT NULL, room_id TEXT NOT NULL,
                thread_id TEXT NOT NULL, session_id TEXT NOT NULL,
                accepted_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id));
            INSERT INTO sessions VALUES ('openclaw:primary', '!room:example.org', '', 'mrb1_old', CURRENT_TIMESTAMP);
            INSERT INTO source_events VALUES ('$old', 'openclaw:primary', '!room:example.org', '', 'mrb1_old', CURRENT_TIMESTAMP);
            """,
        )
    bridge_store = RuntimeBridgeStore(path)
    await bridge_store.open()
    lifecycle = await bridge_store.lifecycle("$old")
    await bridge_store.close()
    assert lifecycle is not None
    assert lifecycle[0] == "delivered"
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (5,)


async def test_state_stream_reconnects_from_monotonic_cursor(store: RuntimeBridgeStore) -> None:
    service = RuntimeBridgeService(store)
    await service.forward(
        adapter=FakeAdapter(),
        source_event_id="$stream",
        origin=EventOrigin.HUMAN,
        scope=ConversationScope("!room:example.org"),
        text="hello",
    )

    first = [event async for event in service.observe("$stream")]
    assert [(event.sequence, event.phase) for event in first] == [
        (1, "reserved"),
        (2, "invoking"),
        (3, "response_ready"),
    ]
    resumed = [event async for event in service.observe("$stream", after_sequence=1)]
    assert [(event.sequence, event.phase) for event in resumed] == [
        (2, "invoking"),
        (3, "response_ready"),
    ]


async def test_state_stream_failure_contains_no_exception_content(store: RuntimeBridgeStore) -> None:
    class FailingAdapter(FakeAdapter):
        async def invoke(self, _request: RuntimeRequest) -> RuntimeResult:
            message = "secret must never enter stream"
            raise RuntimeError(message)

    service = RuntimeBridgeService(store)
    with pytest.raises(RuntimeError, match="secret"):
        await service.forward(
            adapter=FailingAdapter(),
            source_event_id="$stream-failed",
            origin=EventOrigin.HUMAN,
            scope=ConversationScope("!room:example.org"),
            text="hello",
        )
    events = [event async for event in service.observe("$stream-failed")]
    assert [event.phase for event in events] == ["reserved", "invoking", "failed"]
    assert "secret" not in repr(events)


async def test_audit_sink_records_content_free_states(store: RuntimeBridgeStore) -> None:
    recorded = []

    async def sink(source_event_id, runtime_key, phase):
        recorded.append((source_event_id, runtime_key, phase))

    service = RuntimeBridgeService(store, state_sink=sink)
    await service.forward(
        adapter=FakeAdapter(),
        source_event_id="$audited",
        origin=EventOrigin.HUMAN,
        scope=ConversationScope("!room:example.org"),
        text="private prompt",
    )
    assert recorded == [
        ("$audited", "hermes:test", "reserved"),
        ("$audited", "hermes:test", "invoking"),
        ("$audited", "hermes:test", "response_ready"),
    ]
    assert "private prompt" not in repr(recorded)


async def test_audit_failure_before_invocation_fails_closed(store: RuntimeBridgeStore) -> None:
    adapter = FakeAdapter()

    async def sink(_source_event_id, _runtime_key, phase):
        if phase == "invoking":
            message = "audit unavailable"
            raise RuntimeError(message)

    service = RuntimeBridgeService(store, state_sink=sink)
    with pytest.raises(RuntimeError, match="audit unavailable"):
        await service.forward(
            adapter=adapter,
            source_event_id="$audit-failed",
            origin=EventOrigin.HUMAN,
            scope=ConversationScope("!room:example.org"),
            text="hello",
        )
    assert adapter.requests == []
    assert (await store.lifecycle("$audit-failed"))[0] == "invoking"


async def test_newer_database_version_is_rejected_without_overwrite(tmp_path: Path) -> None:

    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version=99")
    bridge_store = RuntimeBridgeStore(path)
    with pytest.raises(RuntimeError, match="newer than supported"):
        await bridge_store.open()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone() == (99,)


async def test_source_event_reuse_in_another_scope_is_conflict(store: RuntimeBridgeStore) -> None:
    identity = RuntimeIdentity(RuntimeName.OPENCLAW, "primary")
    await store.reserve_source_event(
        identity=identity,
        scope=ConversationScope("!one:example.org"),
        source_event_id="$same",
    )

    with pytest.raises(SourceEventConflictError):
        await store.reserve_source_event(
            identity=identity,
            scope=ConversationScope("!two:example.org"),
            source_event_id="$same",
        )


async def test_service_enforces_explicit_concurrency_bound(store: RuntimeBridgeStore) -> None:
    class BlockingAdapter(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.high_water = 0

        async def invoke(self, request: RuntimeRequest) -> RuntimeResult:
            self.requests.append(request)
            self.active += 1
            self.high_water = max(self.high_water, self.active)
            await asyncio.sleep(0.05)
            self.active -= 1
            return RuntimeResult(text="done")

    adapter = BlockingAdapter()
    service = RuntimeBridgeService(store, max_concurrency=2)
    await asyncio.gather(
        *(
            service.forward(
                adapter=adapter,
                source_event_id=f"$event-{index}",
                origin=EventOrigin.HUMAN,
                scope=ConversationScope("!room:example.org"),
                text="hello",
            )
            for index in range(5)
        ),
    )
    assert adapter.high_water == 2


async def test_strict_human_origin_guard_precedes_reservation(store: RuntimeBridgeStore) -> None:
    service = RuntimeBridgeService(store)
    adapter = FakeAdapter()

    with pytest.raises(ValueError, match="human-origin"):
        await service.forward(
            adapter=adapter,
            source_event_id="$runtime-echo",
            origin=EventOrigin.RUNTIME,
            scope=ConversationScope("!room:example.org"),
            text="do not loop",
        )
    result = await service.forward(
        adapter=adapter,
        source_event_id="$runtime-echo",
        origin=EventOrigin.HUMAN,
        scope=ConversationScope("!room:example.org"),
        text="accepted",
    )
    assert result.text == "echo:accepted"


async def test_named_adapters_use_explicit_contract(tmp_path: Path) -> None:
    script = tmp_path / "contract.py"
    script.write_text(
        "import json, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        "assert request['policy'] == {'allow_tools': False, 'allow_consequential_execution': False}\n"
        "print(json.dumps({'version': request['version'], 'type': 'final', "
        "'request_id': request['request_id'], 'text': 'done', 'state': {'cursor': 2}}))\n",
    )
    config = SubprocessContractConfig(argv=(sys.executable, str(script)), max_attempts=1)
    request = _request()

    for adapter in (OpenClawAdapter(instance="one", config=config), HermesAdapter(instance="two", config=config)):
        result = await adapter.invoke(request)
        assert result == RuntimeResult(text="done", state={"cursor": 2})
        await adapter.close()


async def test_hermes_uses_versioned_run_api_and_session_header(monkeypatch: pytest.MonkeyPatch) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            assert request.url.path == "/v1/runs"
            assert request.headers["X-Hermes-Session-Key"] == "mrb1_test"
            assert json.loads(request.content) == {"input": "hello", "session_id": "mrb1_test"}
            return httpx.Response(202, json={"run_id": "run_test", "status": "started"})
        assert request.url.path == "/v1/runs/run_test"
        return httpx.Response(200, json={"status": "completed", "output": "done"})

    monkeypatch.setenv("API_SERVER_KEY", "test-secret")
    adapter = HermesAdapter(
        instance="test",
        endpoint="http://127.0.0.1:8642",
        approved_port=8642,
        endpoint_allowlist=("http://127.0.0.1:8642",),
        api_key_env="API_SERVER_KEY",
        expected_version="0.18.2",
    )
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    assert await adapter.invoke(_request()) == RuntimeResult(text="done", state={"run_id": "run_test"})
    assert len(requests) == 2
    await adapter.close()


async def test_hermes_preflight_rejects_host_tool_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok", "platform": "hermes-agent", "version": "0.18.2"})
        assert request.url.path == "/v1/capabilities"
        return httpx.Response(
            200,
            json={
                "object": "hermes.api_server.capabilities",
                "platform": "hermes-agent",
                "runtime": {"tool_execution": "server"},
                "features": {"run_submission": True},
                "endpoints": {"runs": {"method": "POST", "path": "/v1/runs"}},
            },
        )

    monkeypatch.setenv("API_SERVER_KEY", "test-secret")
    adapter = HermesAdapter(
        instance="test",
        endpoint="http://127.0.0.1:8642",
        approved_port=8642,
        endpoint_allowlist=("http://127.0.0.1:8642",),
        api_key_env="API_SERVER_KEY",
        expected_version="0.18.2",
    )
    await adapter._client.aclose()
    adapter._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(RuntimeContractError, match="deny-all"):
        await adapter.preflight()
    await adapter.close()


async def test_human_ingress_accepts_authorized_transport_alias() -> None:
    origin = TurnOrigin(
        transport_sender_id="@telegram_8411753427:localhost",
        requester_id="@mindroom_user:localhost",
        sender_entity_name=None,
        requester_entity_name=None,
        sender_kind=SenderKind.USER,
        requester_kind=SenderKind.USER,
        intent=TurnIntent.USER_MESSAGE,
        source_kind="matrix",
        trust=TurnTrust.EXTERNAL,
    )
    _assert_human_ingress(SimpleNamespace(origin=origin))


async def test_unknown_response_keys_are_denied(tmp_path: Path) -> None:
    adapter = _scripted_adapter(
        tmp_path,
        {"text": "unsafe", "state": {}, "tool_calls": [{"name": "shell"}]},
    )

    with pytest.raises(RuntimeContractError, match="exact response shape"):
        await adapter.invoke(_request())


async def test_nonfinite_response_is_denied(tmp_path: Path) -> None:
    script = tmp_path / "nonfinite.py"
    script.write_text(
        "import sys\nsys.stdin.readline()\n"
        f'print(\'{{"version":"{CONTRACT_VERSION}","type":"final","request_id":"$request",\''
        '\'"text":"bad","state":{"number":NaN}}\')\n',
    )
    adapter = OpenClawAdapter(instance="test", config=SubprocessContractConfig(argv=(sys.executable, str(script))))
    with pytest.raises(RuntimeContractError, match="strict NDJSON"):
        await adapter.invoke(_request())


async def test_external_invocation_retries_are_forbidden() -> None:
    with pytest.raises(ValueError, match="max_attempts must be 1"):
        SubprocessContractConfig(argv=(sys.executable,), max_attempts=2)


async def test_runtime_instance_requires_identity_slug() -> None:
    with pytest.raises(ValueError, match="identity slug"):
        RuntimeIdentity(RuntimeName.HERMES, "Not A Slug")


async def test_cancellation_terminates_process_group_descendant(tmp_path: Path) -> None:
    marker = tmp_path / "descendant-finished"
    child = tmp_path / "child.py"
    child.write_text(f"import pathlib, time\ntime.sleep(2)\npathlib.Path({str(marker)!r}).write_text('bad')\n")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import subprocess, sys, time\nsys.stdin.readline()\n"
        f"subprocess.Popen([sys.executable, {str(child)!r}])\ntime.sleep(10)\n",
    )
    adapter = OpenClawAdapter(
        instance="descendants",
        config=SubprocessContractConfig(argv=(sys.executable, str(parent))),
    )
    task = asyncio.create_task(adapter.invoke(_request()))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(2.2)
    assert not marker.exists()
    await adapter.close()


async def test_cancellation_terminates_subprocess(tmp_path: Path) -> None:
    marker = tmp_path / "finished"
    script = tmp_path / "slow.py"
    script.write_text(
        "import pathlib, sys, time\n"
        "sys.stdin.readline()\n"
        "time.sleep(10)\n"
        f"pathlib.Path({str(marker)!r}).write_text('bad')\n",
    )
    adapter = OpenClawAdapter(
        instance="cancel",
        config=SubprocessContractConfig(argv=(sys.executable, str(script)), max_attempts=1),
    )
    task = asyncio.create_task(adapter.invoke(_request()))
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.sleep(0.1)
    assert not marker.exists()
    await adapter.close()


def _request() -> RuntimeRequest:
    return RuntimeRequest(
        source_event_id="$request",
        origin=EventOrigin.HUMAN,
        scope=ConversationScope("!room:example.org"),
        session_id="mrb1_test",
        text="hello",
    )


def _scripted_adapter(tmp_path: Path, response: Mapping[str, object]) -> OpenClawAdapter:
    script = tmp_path / "response.py"
    response = {"version": CONTRACT_VERSION, "type": "final", "request_id": "$request", **response}
    script.write_text(f"import json, sys\nsys.stdin.readline()\nprint(json.dumps({json.dumps(response)}))\n")
    return OpenClawAdapter(
        instance="test",
        config=SubprocessContractConfig(argv=(sys.executable, str(script)), max_attempts=1),
    )

async def test_openclaw_live_cli_is_disabled_without_immutable_attestation(tmp_path: Path) -> None:
    script = tmp_path / "openclaw"
    script.write_text("#!/bin/sh\nexit 99\n")
    script.chmod(0o755)
    with pytest.raises(ValueError, match="root-owned and immutable"):
        OpenClawAdapter(
            instance="prod", agent_id="dedicated", executable=str(script),
            executable_allowlist=(str(script),), timeout_seconds=5,
        )


async def test_outbox_recovery_replays_only_response_ready(store: RuntimeBridgeStore) -> None:
    adapter = FakeAdapter()
    service = RuntimeBridgeService(store)
    await service.forward(
        adapter=adapter,
        source_event_id="$ready",
        origin=EventOrigin.HUMAN,
        scope=ConversationScope("!room:example.org"),
        text="hello",
        target=MessageTarget.resolve("!room:example.org", None, "$ready", room_mode=True),
    )
    await store.reserve_source_event(
        identity=adapter.identity,
        scope=ConversationScope("!room:example.org"),
        source_event_id="$uncertain",
    )
    await store.mark_invoking("$uncertain", "digest")
    rows = await store.recover_delivery_queue()
    assert [row.source_event_id for row in rows] == ["$ready"]
    transaction_id = rows[0].transaction_id
    await store.mark_delivering("$ready")
    uncertain = await store.recover_delivery_queue()
    assert [(row.source_event_id, row.transaction_id, row.status) for row in uncertain] == [
        ("$ready", transaction_id, "delivering"),
    ]

async def test_v0_nonempty_database_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "v0.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE surprise(value TEXT)")
    candidate = RuntimeBridgeStore(path)
    with pytest.raises(RuntimeError, match="unversioned non-empty"):
        await candidate.open()


async def test_max_sessions_is_enforced_before_new_unsettled_scope(tmp_path: Path) -> None:
    candidate = RuntimeBridgeStore(tmp_path / "bounded.sqlite3")
    await candidate.open()
    identity = RuntimeIdentity(RuntimeName.HERMES, "bounded")
    await candidate.reserve_source_event(identity=identity, scope=ConversationScope("!one:x"),
                                         source_event_id="$one", max_sessions=1)
    with pytest.raises(RuntimeError, match="all eviction candidates are unsettled"):
        await candidate.reserve_source_event(identity=identity, scope=ConversationScope("!two:x"),
                                             source_event_id="$two", max_sessions=1)
    await candidate.close()


async def test_hermes_rejects_run_id_path_injection() -> None:
    assert _RUN_ID.fullmatch("safe_123")
    assert not _RUN_ID.fullmatch("../stop")
    assert not _RUN_ID.fullmatch("a/b")
