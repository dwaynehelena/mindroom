"""Configured MindRoom tool invocation for the owned Personal Ops service."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mindroom.agents import build_agent_toolkit
from mindroom.personal_ops import PersonalOpsError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from mindroom.config.main import Config
    from mindroom.constants import RuntimePaths


@dataclass(frozen=True, slots=True)
class PersonalOpsToolRuntime:
    """Resolve every call through the active config and registered tool system."""

    runtime_paths: RuntimePaths
    config_provider: Callable[[], Config | None]

    async def __call__(self, tool_name: str, function_name: str, arguments: Mapping[str, object]) -> object:
        """Invoke one exact configured tool function."""
        config: Config | None = self.config_provider()
        if config is None or config.personal_ops.executor_agent is None:
            message = "Personal Ops tool runtime has no active configuration"
            raise PersonalOpsError(message)
        toolkit = build_agent_toolkit(
            tool_name,
            agent_name=config.personal_ops.executor_agent,
            config=config,
            runtime_paths=self.runtime_paths,
            worker_tools=[],
            runtime_overrides=None,
            execution_identity=None,
        )
        if toolkit is None:
            message = f"Personal Ops tool is unavailable: {tool_name}"
            raise PersonalOpsError(message)
        async_function = toolkit.get_async_functions().get(function_name)
        if async_function is not None and async_function.entrypoint is not None:
            return await async_function.entrypoint(**arguments)
        sync_function = toolkit.get_functions().get(function_name)
        if sync_function is None or sync_function.entrypoint is None:
            message = f"Personal Ops tool function is unavailable: {tool_name}.{function_name}"
            raise PersonalOpsError(message)
        return await asyncio.to_thread(sync_function.entrypoint, **arguments)
