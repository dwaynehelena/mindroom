"""ARIP approval gate reusing the canonical ``approval_manager`` runtime.

The gate routes through the live ``request_approval`` from
``mindroom.approval_manager`` (the module-level approval store). When no live
store is wired (standalone/test pipeline run), it fails closed and refuses to
deliver, recording that fact on the outcome.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from mindroom.approval_manager import get_approval_store

# Default gate timeout in seconds (short, matching short timeout_days rules).
DEFAULT_TIMEOUT_SECONDS = 60.0

# Operator identity that must approve in a live runtime.
DEFAULT_APPROVER = "@dwayne:localhost"


@dataclass(slots=True)
class ApprovalOutcome:
    """One gate outcome."""

    approved: bool
    status: str
    reason: str | None = None
    live: bool = False
    resolved_by: str | None = None


class ApprovalGate:
    """Explicit single gate for a composed brief before delivery."""

    def __init__(
        self,
        *,
        tool_name: str = "ops_autopilot.deliver_brief",
        approver: str = DEFAULT_APPROVER,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._tool_name = tool_name
        self._approver = approver
        self._timeout_seconds = timeout_seconds

    async def gate(self, brief: str, *, room_id: str | None = None) -> ApprovalOutcome:
        """Request approval to deliver ``brief`` and return the decision."""
        store = get_approval_store()
        arguments = {"brief_length": len(brief), "target": "telegram_dm"}

        if store is None:
            # Fail closed: a missing live approval store must NEVER silently
            # auto-approve a delivery. The brief's suggested action is gated and
            # stays blocked until a real Approve/Deny can be resolved in Matrix.
            return ApprovalOutcome(
                approved=False,
                status="denied",
                reason="No live approval runtime; refusing to auto-approve delivery.",
            )

        decision = await store.request_approval(
            tool_name=self._tool_name,
            arguments=arguments,
            room_id=room_id,
            requester_id=self._approver,
            approver_user_id=self._approver,
            timeout_seconds=self._timeout_seconds,
        )
        return ApprovalOutcome(
            approved=decision.status == "approved",
            status=decision.status,
            reason=decision.reason,
            live=True,
            resolved_by=decision.resolved_by,
        )


def request_approval(brief: str, *, room_id: str | None = None) -> ApprovalOutcome:
    """Synchronous convenience wrapper around :meth:`ApprovalGate.gate`."""
    gate = ApprovalGate()
    return asyncio.run(gate.gate(brief, room_id=room_id))