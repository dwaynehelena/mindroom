"""Tests for federated mission compilation, checkpoints, retries and compensation."""

# ruff: noqa: ANN001, ANN201, ANN202, C420, D103, EM101, TRY003

from __future__ import annotations

from collections import defaultdict

import pytest
import pytest_asyncio
from pydantic import ValidationError

from mindroom.mission_compiler import (
    MissionCheckpointStore,
    MissionExecutor,
    MissionNode,
    MissionPlan,
    compile_federated_mission,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def store(tmp_path):
    value = MissionCheckpointStore(tmp_path / "mission.db")
    await value.open()
    yield value
    await value.close()


async def test_compiled_mission_places_research_action_and_review(store: MissionCheckpointStore) -> None:
    calls = []

    async def adapter(node, dependencies, _context):
        calls.append((node.runtime, node.action, tuple(dependencies)))
        return {"node": node.node_id}

    plan = compile_federated_mission(
        mission_id="daily-brief",
        goal="Research, act, and review",
        research_action="research",
        device_action="notify",
        review_action="review",
    )
    result = await MissionExecutor(store, {role: adapter for role in ("mindroom", "hermes", "openclaw")}).execute(plan)
    assert result.status == "succeeded"
    assert calls == [
        ("hermes", "research", ()),
        ("openclaw", "notify", ("research",)),
        ("mindroom", "review", ("act",)),
    ]


async def test_retry_then_checkpointed_resume_skips_successes(store: MissionCheckpointStore) -> None:
    attempts = defaultdict(int)

    async def adapter(node, _dependencies, _context):
        attempts[node.node_id] += 1
        if node.node_id == "research" and attempts[node.node_id] == 1:
            raise RuntimeError("temporary")
        return {"ok": True}

    plan = compile_federated_mission(
        mission_id="retry-demo",
        goal="Retry safely",
        research_action="research",
        device_action="act",
        review_action="review",
    )
    executor = MissionExecutor(store, {role: adapter for role in ("mindroom", "hermes", "openclaw")})
    assert (await executor.execute(plan)).status == "succeeded"
    assert attempts == {"research": 2, "act": 1, "review": 1}
    assert (await executor.execute(plan)).status == "succeeded"
    assert attempts == {"research": 2, "act": 1, "review": 1}


async def test_terminal_failure_compensates_completed_nodes_in_reverse(store: MissionCheckpointStore) -> None:
    calls = []

    async def adapter(node, _dependencies, _context):
        calls.append(node.action)
        if node.node_id == "review":
            raise RuntimeError("review failed")
        return {"ok": True}

    plan = MissionPlan(
        mission_id="compensate",
        goal="Compensate",
        nodes=(
            MissionNode(node_id="prepare", runtime="hermes", action="prepare", compensation_action="undo-prepare"),
            MissionNode(
                node_id="act",
                runtime="openclaw",
                action="act",
                depends_on=("prepare",),
                compensation_action="undo-act",
            ),
            MissionNode(node_id="review", runtime="mindroom", action="review", depends_on=("act",)),
        ),
    )
    result = await MissionExecutor(store, {role: adapter for role in ("mindroom", "hermes", "openclaw")}).execute(plan)
    assert result.status == "failed"
    assert calls == ["prepare", "act", "review", "undo-act", "undo-prepare"]
    assert [item.status for item in result.checkpoints] == ["compensated", "compensated", "failed"]


async def test_cycles_and_unknown_dependencies_are_rejected() -> None:
    with pytest.raises(ValidationError, match="cycle"):
        MissionPlan(
            mission_id="cycle",
            goal="invalid",
            nodes=(
                MissionNode(node_id="one", runtime="mindroom", action="one", depends_on=("two",)),
                MissionNode(node_id="two", runtime="hermes", action="two", depends_on=("one",)),
            ),
        )
    with pytest.raises(ValidationError, match="invalid dependency"):
        MissionPlan(
            mission_id="unknown",
            goal="invalid",
            nodes=(MissionNode(node_id="one", runtime="mindroom", action="one", depends_on=("missing",)),),
        )


async def test_non_idempotent_nodes_cannot_enable_automatic_retry() -> None:
    with pytest.raises(ValidationError, match="idempotent"):
        MissionNode(node_id="write", runtime="openclaw", action="send", retry_limit=1)


async def test_failure_checkpoint_does_not_persist_exception_content(store: MissionCheckpointStore) -> None:
    async def adapter(_node, _dependencies, _context):
        raise RuntimeError("secret-token")

    plan = MissionPlan(
        mission_id="redacted-failure",
        goal="Fail safely",
        nodes=(MissionNode(node_id="one", runtime="mindroom", action="fail"),),
    )
    result = await MissionExecutor(store, {role: adapter for role in ("mindroom", "hermes", "openclaw")}).execute(plan)
    assert result.status == "failed"
    assert result.checkpoints[0].failure == "builtins.RuntimeError"
