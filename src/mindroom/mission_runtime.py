"""Production adapters from federated missions to the Runtime Bridge."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mindroom.runtime_bridge.models import ConversationScope, EventOrigin

if TYPE_CHECKING:
    from mindroom.mission_compiler import MissionExecutionContext, MissionNode
    from mindroom.runtime_bridge.adapter import RuntimeAdapter
    from mindroom.runtime_bridge.service import RuntimeBridgeService

MissionReviewer = Callable[
    ["MissionNode", dict[str, object], "MissionExecutionContext"],
    Awaitable["MissionReviewDecision"],
]


class MissionReviewError(RuntimeError):
    """A MindRoom mission review was invalid or denied."""


@dataclass(frozen=True, slots=True)
class MissionReviewDecision:
    """One attributable MindRoom review of exact dependency outputs."""

    approved: bool
    reviewer_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class MindRoomMissionReviewBinding:
    """Compose an attributable MindRoom review into the mission adapter contract."""

    reviewer: MissionReviewer

    async def __call__(
        self,
        node: MissionNode,
        dependencies: dict[str, object],
        context: MissionExecutionContext,
    ) -> dict[str, object]:
        """Approve exact mission outputs or fail into the executor's compensation path."""
        if node.runtime != "mindroom" or context.compensation or not dependencies:
            message = "MindRoom mission review requires a review node with dependency evidence"
            raise MissionReviewError(message)
        decision = await self.reviewer(node, dependencies, context)
        reviewer_id = decision.reviewer_id.strip()
        reason = decision.reason.strip()
        if not reviewer_id or not reason:
            message = "MindRoom mission review requires an attributable reviewer and reason"
            raise MissionReviewError(message)
        if not decision.approved:
            message = "MindRoom mission review denied the mission output"
            raise MissionReviewError(message)
        return {
            "approved": True,
            "reason": reason[:2000],
            "reviewer_id": reviewer_id[:512],
        }


@dataclass(frozen=True, slots=True)
class RuntimeMissionBinding:
    """One trusted runtime worker and its canonical Matrix scope."""

    service: RuntimeBridgeService
    adapter: RuntimeAdapter
    scope: ConversationScope
    source_human_event_id: str

    async def __call__(
        self,
        node: MissionNode,
        dependencies: dict[str, object],
        context: MissionExecutionContext,
    ) -> dict[str, object]:
        """Invoke one exact mission attempt through the at-most-once bridge."""
        if node.runtime != self.adapter.identity.runtime.value:
            message = "mission node runtime does not match its bound adapter"
            raise ValueError(message)
        request = {
            "action": node.action,
            "compensation": context.compensation,
            "dependencies": dependencies,
            "inputs": node.inputs,
            "mission_id": context.mission_id,
            "node_id": node.node_id,
        }
        result = await self.service.forward(
            adapter=self.adapter,
            source_event_id=_source_event_id(self.source_human_event_id, node, context),
            origin=EventOrigin.HUMAN,
            scope=self.scope,
            text=json.dumps(request, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False),
            state={"mission_id": context.mission_id, "node_id": node.node_id},
        )
        return {"state": dict(result.state), "text": result.text}


def _source_event_id(source_human_event_id: str, node: MissionNode, context: MissionExecutionContext) -> str:
    if not source_human_event_id.strip():
        message = "mission runtime binding requires a source human event"
        raise ValueError(message)
    value = "\x1f".join(
        (
            source_human_event_id,
            context.mission_id,
            node.node_id,
            str(context.attempt),
            "compensate" if context.compensation else "execute",
        ),
    )
    return "$mission_" + hashlib.sha256(value.encode()).hexdigest()
