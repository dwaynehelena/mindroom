"""Ops-autopilot in-process schedule hook plugin.

Registers a ``schedule:fired`` hook that runs the ops-autopilot pipeline
IN-PROCESS when the recurring autopilot task (``ops-autopilot-ba0760ba``,
cron ``30 7 * * *``) fires. The pipeline delivers a brief UNGATED to the
portal room and surfaces one ARIP-gated suggested action.
"""

from __future__ import annotations

from mindroom.hooks import EVENT_SCHEDULE_FIRED, ScheduleFiredContext, hook
from mindroom.ops_autopilot.inprocess import run_autopilot_in_process


# schedule:fired defaults to 1000ms; the pipeline collects git/mail/calendar
# and previously aborted at 1649ms ("Hook execution failed").
@hook(EVENT_SCHEDULE_FIRED, priority=10, timeout_ms=120_000)
async def ops_autopilot_schedule_fired(ctx: ScheduleFiredContext) -> None:
    """Run the ops-autopilot pipeline in-process for the autopilot task."""
    await run_autopilot_in_process(ctx)