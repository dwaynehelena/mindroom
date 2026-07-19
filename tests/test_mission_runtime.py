"""Tests for federated mission Runtime Bridge bindings."""

# ruff: noqa: ANN001, ANN201, ANN202, D101, D102, D103

from __future__ import annotations

import pytest
import pytest_asyncio

from mindroom.mission_compiler import (
    MissionCheckpointStore,
    MissionExecutionContext,
    MissionExecutor,
    MissionNode,
    compile_federated_mission,
)
from mindroom.mission_runtime import (
    MindRoomMissionReviewBinding,
    MissionReviewDecision,
    MissionReviewError,
    RuntimeMissionBinding,
)
from mindroom.runtime_bridge import (
    ConversationScope,
    DuplicateSourceEventError,
    RuntimeBridgeService,
    RuntimeBridgeStore,
    RuntimeIdentity,
    RuntimeName,
    RuntimeResult,
)

pytestmark = pytest.mark.asyncio


class Adapter:
    identity = RuntimeIdentity(RuntimeName.HERMES, "mission")

    def __init__(self) -> None:
        self.requests = []

    async def invoke(self, request):
        self.requests.append(request)
        return RuntimeResult("researched", {"safe": True})

    async def close(self) -> None:
        return


@pytest_asyncio.fixture
async def binding(tmp_path):
    store = RuntimeBridgeStore(tmp_path / "bridge.db")
    await store.open()
    adapter = Adapter()
    yield RuntimeMissionBinding(
        RuntimeBridgeService(store),
        adapter,
        ConversationScope("!room:test", "$thread"),
        "$human",
    ), adapter
    await store.close()


async def test_binding_uses_stable_attempt_identity_and_bridge_contract(binding) -> None:
    value, adapter = binding
    node = MissionNode(node_id="research", runtime="hermes", action="investigate", idempotent=True)
    context = MissionExecutionContext("mission-1", 1)
    assert await value(node, {"seed": {"value": 1}}, context) == {
        "state": {"safe": True},
        "text": "researched",
    }
    request = adapter.requests[0]
    assert request.source_event_id.startswith("$mission_")
    assert request.scope == ConversationScope("!room:test", "$thread")
    with pytest.raises(DuplicateSourceEventError):
        await value(node, {"seed": {"value": 1}}, context)
    assert len(adapter.requests) == 1


async def test_binding_rejects_runtime_mismatch_before_reservation(binding) -> None:
    value, adapter = binding
    node = MissionNode(node_id="act", runtime="openclaw", action="notify")
    with pytest.raises(ValueError, match="does not match"):
        await value(node, {}, MissionExecutionContext("mission-1", 1))
    assert adapter.requests == []


async def test_mindroom_review_returns_attributable_bounded_evidence() -> None:
    observed = []

    async def review(node, dependencies, context):
        observed.append((node.node_id, dependencies, context.mission_id))
        return MissionReviewDecision(True, "@reviewer:test", "Verified exact action output")

    binding = MindRoomMissionReviewBinding(review)
    node = MissionNode(node_id="review", runtime="mindroom", action="quality.review", depends_on=("act",))
    result = await binding(node, {"act": {"receipt": "exact"}}, MissionExecutionContext("mission-1", 1))
    assert result == {
        "approved": True,
        "reason": "Verified exact action output",
        "reviewer_id": "@reviewer:test",
    }
    assert observed == [("review", {"act": {"receipt": "exact"}}, "mission-1")]


async def test_denied_mindroom_review_triggers_openclaw_compensation(tmp_path) -> None:
    calls = []

    async def runtime(node, _dependencies, context):
        calls.append((node.runtime, node.action, context.compensation))
        return {"receipt": node.node_id}

    async def deny(_node, _dependencies, _context):
        return MissionReviewDecision(False, "@reviewer:test", "Action output failed review")

    store = MissionCheckpointStore(tmp_path / "mission.db")
    await store.open()
    try:
        plan = compile_federated_mission(
            mission_id="review-denial",
            goal="Research, act, review, compensate",
            research_action="research",
            device_action="notify",
            review_action="quality.review",
            device_compensation="notify.rollback",
        )
        result = await MissionExecutor(
            store,
            {
                "hermes": runtime,
                "openclaw": runtime,
                "mindroom": MindRoomMissionReviewBinding(deny),
            },
        ).execute(plan)
    finally:
        await store.close()
    assert result.status == "failed"
    assert calls == [
        ("hermes", "research", False),
        ("openclaw", "notify", False),
        ("openclaw", "notify.rollback", True),
    ]
    assert next(checkpoint for checkpoint in result.checkpoints if checkpoint.node_id == "act").status == "compensated"


async def test_mindroom_review_rejects_missing_evidence_or_attribution() -> None:
    async def unattributed(_node, _dependencies, _context):
        return MissionReviewDecision(True, "", "")

    binding = MindRoomMissionReviewBinding(unattributed)
    node = MissionNode(node_id="review", runtime="mindroom", action="quality.review")
    with pytest.raises(MissionReviewError, match="dependency evidence"):
        await binding(node, {}, MissionExecutionContext("mission-1", 1))
    with pytest.raises(MissionReviewError, match="attributable"):
        await binding(node, {"act": {}}, MissionExecutionContext("mission-1", 1))
