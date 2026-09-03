"""Runtime candidate-event capture and live Matrix review for governed learning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, Protocol

from mindroom.constants import tracking_dir
from mindroom.learning_capture import LearningCandidate, capture_learning_candidate
from mindroom.learning_loop import LearningLoopError, LearningLoopStore, LearningProposal

if TYPE_CHECKING:
    from mindroom.approval_manager import ApprovalDecision
    from mindroom.constants import RuntimePaths


class LiveApprovalRequester(Protocol):
    """Exact Matrix-backed approval surface used by the learning reviewer."""

    async def request_approval(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        room_id: str | None,
        requester_id: str | None,
        approver_user_id: str | None,
        timeout_seconds: float,
        agent_name: str | None = None,
        thread_id: str | None = None,
        workflow_id: str | None = None,
        participant_id: str | None = None,
    ) -> ApprovalDecision:
        """Deliver and resolve one exact-payload live approval."""


@dataclass(frozen=True, slots=True)
class RuntimeLearningCandidateEvent:
    """One verified candidate emitted by an explicit runtime producer."""

    source_runtime: Literal["mindroom", "openclaw", "hermes"]
    candidate: LearningCandidate
    emitted_at: datetime

    def __post_init__(self) -> None:
        """Require an aware event time and matching nonblank source identity."""
        if self.emitted_at.tzinfo is None or self.emitted_at.utcoffset() is None:
            message = "learning candidate event time must include a timezone"
            raise LearningLoopError(message)


@dataclass(frozen=True, slots=True)
class LearningReviewContext:
    """Canonical Matrix context for one live governed review."""

    room_id: str
    requester_id: str
    approver_user_id: str
    thread_id: str | None = None
    timeout_seconds: float = 3600.0

    def __post_init__(self) -> None:
        """Reject incomplete or unbounded live review context."""
        if (
            not self.room_id.startswith("!")
            or ":" not in self.room_id
            or not self.requester_id
            or not self.approver_user_id
            or self.timeout_seconds <= 0
            or self.timeout_seconds > 86_400
        ):
            message = "learning review Matrix context is invalid"
            raise LearningLoopError(message)


class GovernedLearningRuntime:
    """Bind runtime candidate events and exact live review to governance state."""

    def __init__(
        self,
        *,
        runtime_paths: RuntimePaths,
        store: LearningLoopStore,
        approvals: LiveApprovalRequester,
    ) -> None:
        self._runtime_paths = runtime_paths
        self._store = store
        self._approvals = approvals

    async def ingest(self, event: RuntimeLearningCandidateEvent) -> LearningProposal:
        """Automatically capture one emitted candidate through production trace stores."""
        return await capture_learning_candidate(
            self._runtime_paths,
            event.candidate,
            captured_at=event.emitted_at.astimezone(UTC),
        )

    async def review(self, proposal_id: str, context: LearningReviewContext) -> LearningProposal:
        """Deliver exact evidence to Matrix and persist its attributable human decision."""
        proposal = await self._store.get(proposal_id)
        if proposal.stage != "evaluated" or proposal.evaluation is None:
            message = "only an evaluated learning proposal may request live review"
            raise LearningLoopError(message)
        evidence = proposal.evaluation
        arguments: dict[str, object] = {
            "artifact_digest": proposal.artifact_digest,
            "baseline_score": evidence.baseline_score,
            "candidate_score": evidence.candidate_score,
            "kind": proposal.kind,
            "proposal_id": proposal.proposal_id,
            "source_run_id": proposal.source_run_id,
            "suite_id": evidence.suite_id,
            "tests_passed": evidence.tests_passed,
            "tests_run": evidence.tests_run,
        }
        decision = await self._approvals.request_approval(
            tool_name="mindroom.learning.promote",
            arguments=arguments,
            room_id=context.room_id,
            requester_id=context.requester_id,
            approver_user_id=context.approver_user_id,
            timeout_seconds=context.timeout_seconds,
            agent_name="router",
            thread_id=context.thread_id,
            workflow_id=f"learning:{proposal.proposal_id}",
            participant_id="mindroom-learning-review",
        )
        if decision.status == "expired" or decision.resolved_by is None:
            message = "learning review expired without an attributable human decision"
            raise LearningLoopError(message)
        reason = decision.reason or f"{decision.status.title()} through exact-payload Matrix review"
        return await self._store.review(
            proposal.proposal_id,
            reviewer_id=decision.resolved_by,
            approved=decision.status == "approved",
            reason=reason,
        )


async def capture_runtime_learning_event(
    runtime_paths: RuntimePaths,
    event: RuntimeLearningCandidateEvent,
) -> LearningProposal:
    """Production short-lived sink for runtime candidate-event callbacks."""
    return await capture_learning_candidate(
        runtime_paths,
        event.candidate,
        captured_at=event.emitted_at.astimezone(UTC),
    )


async def request_runtime_learning_review(
    runtime_paths: RuntimePaths,
    *,
    proposal_id: str,
    context: LearningReviewContext,
) -> LearningProposal:
    """Use the initialized MindRoom Matrix approval runtime for one live review."""
    from mindroom.approval_manager import get_approval_store  # noqa: PLC0415

    approvals = get_approval_store()
    if approvals is None:
        message = "MindRoom live approval transport is not initialized"
        raise LearningLoopError(message)
    store = LearningLoopStore(tracking_dir(runtime_paths) / "learning_loop.db")
    await store.open()
    try:
        service = GovernedLearningRuntime(runtime_paths=runtime_paths, store=store, approvals=approvals)
        return await service.review(proposal_id, context)
    finally:
        await store.close()
