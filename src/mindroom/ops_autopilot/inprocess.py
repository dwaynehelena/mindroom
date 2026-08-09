"""In-process ops-autopilot handler wired to the scheduler dispatch hook.

When the recurring ops-autopilot scheduled task (``ops-autopilot-ba0760ba``,
cron ``30 7 * * *``) fires, the scheduler emits ``schedule:fired``. A plugin
hook registered on that event calls :func:`run_autopilot_in_process`, which
runs the full pipeline IN-PROCESS against the live runtime:

* collect git / scheduler / mail / calendar signals,
* compose a bounded brief,
* deliver the brief UNGATED to the portal room (→ Telegram DM),
* surface ONE ARIP-gated suggested action (``ops_autopilot.gh_action``) that
  must be Approved/Denied by ``@dwayne:localhost`` (gate fails closed).

The hook then suppresses the scheduler's default synthetic message so the
pipeline's own delivery is the only outbound message.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mindroom.ops_autopilot.approval.gate import ApprovalGate
from mindroom.ops_autopilot.collectors.calendar import CalendarCollector
from mindroom.ops_autopilot.collectors.git import GitCollector
from mindroom.ops_autopilot.collectors.mail import MailCollector
from mindroom.ops_autopilot.collectors.registry import CollectorRegistry
from mindroom.ops_autopilot.collectors.scheduler import SchedulerCollector
from mindroom.ops_autopilot.composer import compose_brief
from mindroom.ops_autopilot.delivery.telegram import TelegramDeliverer

if TYPE_CHECKING:
    from mindroom.hooks import ScheduleFiredContext

# The recurring autopilot task id registered as Matrix room state.
AUTOPILOT_TASK_ID = "ops-autopilot-ba0760ba"
# The ARIP-gated suggested action surfaced by the pipeline.
SUGGESTED_ACTION_TOOL = "ops_autopilot.gh_action"
# Operator that must Approve/Deny the suggested action.
APPROVER = "@dwayne:localhost"
# Portal room bridged to Dwayne's Telegram DM (8411753427).
PORTAL_ROOM_ID = "!CzLMONAFcsUmQXAVZw:localhost"


def _build_registry() -> CollectorRegistry:
    """Build the standard collector set for the in-process pipeline."""
    return CollectorRegistry(
        [
            GitCollector(),
            SchedulerCollector(),
            MailCollector(),
            CalendarCollector(),
        ]
    )


async def run_autopilot_in_process(ctx: ScheduleFiredContext) -> None:
    """Run the ops-autopilot pipeline in-process for the autopilot task.

    Only acts when the fired task is the ops-autopilot recurring task. On any
    other task it returns without suppressing the scheduler's default message.
    """
    if ctx.task_id != AUTOPILOT_TASK_ID:
        return

    # 1) Collect + compose a bounded brief.
    results = _build_registry().run_all()
    brief = compose_brief(results)

    # 2) Deliver the brief UNGATED to the portal room (→ Telegram DM).
    deliverer = TelegramDeliverer(room_id=PORTAL_ROOM_ID)
    receipt = deliverer.deliver(brief)
    if not receipt.ok:
        ctx.logger.warning(
            "ops_autopilot_brief_delivery_failed",
            task_id=ctx.task_id,
            error=receipt.error,
        )
    else:
        ctx.logger.info(
            "ops_autopilot_brief_delivered_ungated",
            task_id=ctx.task_id,
            event_id=receipt.event_id,
        )

    # 3) Surface ONE ARIP-gated suggested action (Approve/Deny by @dwayne).
    gate = ApprovalGate(tool_name=SUGGESTED_ACTION_TOOL, approver=APPROVER)
    action_desc = "gh pr list --limit 3 (ops-autopilot suggested action)"
    outcome = await gate.gate(
        f"Suggested action: {action_desc}. Approve to run it, deny to block it.",
        room_id=ctx.room_id or PORTAL_ROOM_ID,
    )
    ctx.logger.info(
        "ops_autopilot_suggested_action_gated",
        task_id=ctx.task_id,
        status=outcome.status,
        approved=outcome.approved,
        live=outcome.live,
    )

    # The pipeline produced its own outbound delivery; suppress the scheduler's
    # default synthetic message so we do not double-post.
    ctx.suppress = True