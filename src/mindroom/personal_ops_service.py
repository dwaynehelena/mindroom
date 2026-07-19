"""Owned, atomically reloadable Personal Ops runtime composition."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mindroom.arip_control import ApprovalControlStore
from mindroom.constants import matrix_state_file
from mindroom.personal_ops import (
    ActionExecutor,
    PersonalOpsAutopilot,
    PersonalOpsError,
    PersonalOpsStore,
    WorkerRuntime,
)
from mindroom.personal_ops_connectors import PersonalOpsConnectorConfig, ToolReadInvoker, mindroom_source_readers
from mindroom.personal_ops_delivery import MatrixPortalBriefSender, load_matrix_access_token
from mindroom.personal_ops_runtime import BriefDeliveryLedger, PersonalOpsBriefRunner
from mindroom.personal_ops_scheduler import PersonalOpsDailySchedule, PersonalOpsDailyScheduler

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from mindroom.config.personal_ops import PersonalOpsConfig
    from mindroom.constants import RuntimePaths


@dataclass(slots=True)
class _Generation:
    scheduler: PersonalOpsDailyScheduler
    stack: AsyncExitStack


class PersonalOpsService:
    """Own all Personal Ops persistence and schedule resources as one generation."""

    def __init__(
        self,
        *,
        runtime_paths: RuntimePaths,
        tool_invoker: ToolReadInvoker | None,
        executors: Mapping[WorkerRuntime, ActionExecutor] | None,
        executor_factory: Callable[[PersonalOpsConfig], Mapping[WorkerRuntime, ActionExecutor]] | None = None,
    ) -> None:
        self._runtime_paths = runtime_paths
        self._tool_invoker = tool_invoker
        self._executors = dict(executors or {})
        self._executor_factory = executor_factory
        self._generation: _Generation | None = None
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        """Return whether a complete generation is active."""
        return self._generation is not None

    async def reload(self, config: PersonalOpsConfig) -> None:
        """Preflight a complete candidate, then replace the prior generation."""
        async with self._lock:
            candidate = await self._build(config) if config.enabled else None
            old, self._generation = self._generation, candidate
            if old is not None:
                await old.stack.aclose()

    async def close(self) -> None:
        """Disable scheduling and close every owned database."""
        async with self._lock:
            old, self._generation = self._generation, None
            if old is not None:
                await old.stack.aclose()

    async def _build(self, config: PersonalOpsConfig) -> _Generation:
        if self._tool_invoker is None:
            message = "enabled personal ops requires a read-tool invoker"
            raise PersonalOpsError(message)
        executors = dict(self._executor_factory(config)) if self._executor_factory is not None else self._executors
        if "mindroom" not in executors or "openclaw" not in executors:
            message = "enabled personal ops requires MindRoom and OpenClaw action executors"
            raise PersonalOpsError(message)
        if config.github_repository is None or config.room_id is None:
            message = "enabled personal ops configuration is incomplete"
            raise PersonalOpsError(message)

        stack = AsyncExitStack()
        await stack.__aenter__()
        try:
            root = self._runtime_paths.storage_root / "personal_ops"
            store = PersonalOpsStore(root / "actions.sqlite3")
            approvals = ApprovalControlStore(root / "approvals.sqlite3")
            ledger = BriefDeliveryLedger(root / "briefs.sqlite3")
            for resource in (store, approvals, ledger):
                await resource.open()
                stack.push_async_callback(resource.close)
            await store.mark_interrupted_uncertain()
            readers = mindroom_source_readers(
                invoker=self._tool_invoker,
                config=PersonalOpsConnectorConfig(config.github_repository, config.item_limit),
            )
            autopilot = PersonalOpsAutopilot(
                store=store,
                approval_store=approvals,
                readers=readers,
                executors=executors,
                display_timezone=config.timezone,
            )
            sender = MatrixPortalBriefSender(
                origin=config.matrix_origin,
                room_id=config.room_id,
                access_token=load_matrix_access_token(matrix_state_file(runtime_paths=self._runtime_paths)),
                timeout_seconds=config.delivery_timeout_seconds,
            )
            runner = PersonalOpsBriefRunner(autopilot=autopilot, sender=sender, ledger=ledger)
            scheduler = PersonalOpsDailyScheduler(runner=runner)
            await scheduler.start(PersonalOpsDailySchedule(config.timezone, config.hour, config.minute))
            stack.push_async_callback(scheduler.shutdown)
            return _Generation(scheduler, stack)
        except BaseException:
            await stack.aclose()
            raise
