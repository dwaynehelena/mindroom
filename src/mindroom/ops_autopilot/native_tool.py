"""In-process native tool for the Personal Ops Autopilot.

Exposes ``run_autopilot_brief`` which drives the existing orchestrator
IN-PROCESS (no subprocess), wiring the live approval store and the current
Matrix client so the pipeline's ARIP gate and delivery use the real runtime.

Mirrors the registration pattern of the custom tools in
``src/mindroom/custom_tools/`` (a ``Toolkit`` subclass whose methods are the
exposed tools, reading the shared tool runtime context).
"""

from __future__ import annotations

from agno.tools import Toolkit

from mindroom.approval_manager import get_approval_store
from mindroom.ops_autopilot.collectors.calendar import CalendarCollector
from mindroom.ops_autopilot.collectors.git import GitCollector
from mindroom.ops_autopilot.collectors.mail import MailCollector
from mindroom.ops_autopilot.collectors.registry import CollectorRegistry
from mindroom.ops_autopilot.collectors.scheduler import SchedulerCollector
from mindroom.ops_autopilot.orchestrator import OpsAutopilotOrchestrator
from mindroom.tool_system.runtime_context import get_tool_runtime_context


class OpsAutopilotNativeTools(Toolkit):
    """Tools that run the ops-autopilot pipeline in-process against the live runtime."""

    def __init__(self) -> None:
        super().__init__(
            name="ops_autopilot_native",
            tools=[self.run_autopilot_brief],
        )

    async def run_autopilot_brief(self) -> str:
        """Run the Personal Ops Autopilot pipeline in-process and deliver the brief.

        Collects git/scheduler signals, composes the ops brief, runs it through
        the ARIP approval gate (using the live approval store), and delivers it
        to Telegram via the Matrix portal bridge. Returns a human-readable
        pipeline report.

        Returns:
            The pipeline report summary string.

        """
        context = get_tool_runtime_context()
        if context is None or context.room is None:
            return "❌ Ops Autopilot native tool is unavailable in this context."

        # Wire the live approval store and Matrix client into the pipeline.
        store = get_approval_store()
        if store is None:
            return "❌ No live approval store; refusing to run the autopilot pipeline."

        # Build a registry whose scheduler collector reads live Matrix room-state
        # via the current client (the orchestrator's gate resolves the live
        # approval store internally through get_approval_store()).
        registry = CollectorRegistry(
            [
                GitCollector(),
                SchedulerCollector(client=context.client, room_id=context.room_id),
                MailCollector(),
                CalendarCollector(),
            ]
        )
        orchestrator = OpsAutopilotOrchestrator(registry=registry, room_id=context.room_id)
        report = await orchestrator.run_async()
        return report.summary()