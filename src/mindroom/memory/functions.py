"""Public memory API and orchestration."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from mindroom.flight_recorder import record_flight_event
from mindroom.logging_config import get_logger
from mindroom.timing import timed

from ._backend import resolve_memory_backend
from ._file_backend import append_agent_daily_file_memory
from ._prompting import format_memories_as_context
from ._shared import MemorySearchOutcome

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from mindroom.config.main import Config
    from mindroom.constants import RuntimePaths
    from mindroom.matrix.client_visible_messages import ResolvedVisibleMessage
    from mindroom.tool_system.worker_routing import ToolExecutionIdentity

    from ._shared import MemoryResult

logger = get_logger(__name__)


def _memory_run_id(*, correlation_id: str | None, session_id: str | None) -> str:
    if correlation_id:
        return correlation_id
    return f"memory:{session_id}" if session_id else "memory:unscoped"


async def _record_memory_write(
    runtime_paths: RuntimePaths,
    *,
    operation: str,
    backend: str,
    scope: str | list[str],
    memory_id: str | None = None,
    session_id: str | None = None,
    correlation_id: str | None = None,
    status: str,
    side_effect: bool,
) -> None:
    """Record one content-free memory mutation phase."""
    await record_flight_event(
        runtime_paths,
        run_id=_memory_run_id(correlation_id=correlation_id, session_id=session_id),
        kind="memory_write",
        payload={
            "backend": backend,
            "memory_id": memory_id,
            "operation": operation,
            "scope": scope,
            "session_id": session_id,
            "status": status,
        },
        side_effect=side_effect,
    )


async def _record_completed_memory_write(runtime_paths: RuntimePaths, **facts: object) -> None:
    """Record post-commit evidence without making a successful write look retryable."""
    try:
        await _record_memory_write(runtime_paths, status="completed", side_effect=True, **facts)  # type: ignore[arg-type]
    except Exception:
        logger.exception("Failed to append completed memory flight record", operation=facts.get("operation"))


@dataclass(frozen=True)
class MemoryPromptParts:
    """Stable and turn-local prompt fragments used by the AI layer."""

    session_preamble: str = ""
    turn_context: str = ""


async def add_agent_memory(
    content: str,
    agent_name: str,
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    metadata: dict | None = None,
    execution_identity: ToolExecutionIdentity | None = None,
    correlation_id: str | None = None,
) -> None:
    """Add a memory for an agent."""
    if (backend := resolve_memory_backend(agent_name, config, runtime_paths)) is None:
        return
    flight_facts = {
        "operation": "add",
        "backend": backend.context_label,
        "scope": agent_name,
        "session_id": execution_identity.session_id if execution_identity is not None else None,
        "correlation_id": correlation_id,
    }
    await _record_memory_write(runtime_paths, status="requested", side_effect=False, **flight_facts)
    await backend.add(
        content,
        agent_name,
        storage_path,
        config,
        metadata=metadata,
        execution_identity=execution_identity,
    )
    await _record_completed_memory_write(runtime_paths, **flight_facts)


def append_agent_daily_memory(
    content: str,
    agent_name: str,
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    execution_identity: ToolExecutionIdentity | None = None,
    *,
    preserve_resolved_storage_path: bool = False,
) -> MemoryResult:
    """Append one memory entry to today's per-agent daily memory file."""
    return append_agent_daily_file_memory(
        content,
        agent_name,
        storage_path,
        config,
        runtime_paths,
        preserve_resolved_storage_path=preserve_resolved_storage_path,
        execution_identity=execution_identity,
    )


@timed("system_prompt_assembly.memory_search")
async def search_agent_memories(
    query: str,
    agent_name: str,
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    limit: int = 3,
    execution_identity: ToolExecutionIdentity | None = None,
) -> MemorySearchOutcome:
    """Search agent memories including team memories."""
    if (backend := resolve_memory_backend(agent_name, config, runtime_paths)) is None:
        return MemorySearchOutcome(results=[])
    return await backend.search(
        query,
        agent_name,
        storage_path,
        config,
        limit=limit,
        execution_identity=execution_identity,
    )


async def list_all_agent_memories(
    agent_name: str,
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    limit: int = 100,
    execution_identity: ToolExecutionIdentity | None = None,
    *,
    preserve_resolved_storage_path: bool = False,
) -> list[MemoryResult]:
    """List all memories for an agent."""
    if (backend := resolve_memory_backend(agent_name, config, runtime_paths)) is None:
        return []
    return await backend.list_all(
        agent_name,
        storage_path,
        config,
        limit=limit,
        preserve_resolved_storage_path=preserve_resolved_storage_path,
        execution_identity=execution_identity,
    )


async def get_agent_memory(
    memory_id: str,
    caller_context: str | list[str],
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    execution_identity: ToolExecutionIdentity | None = None,
) -> MemoryResult | None:
    """Get a single memory by ID."""
    if (backend := resolve_memory_backend(caller_context, config, runtime_paths)) is None:
        return None
    return await backend.get(
        memory_id,
        caller_context,
        storage_path,
        config,
        execution_identity=execution_identity,
    )


async def update_agent_memory(
    memory_id: str,
    content: str,
    caller_context: str | list[str],
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    execution_identity: ToolExecutionIdentity | None = None,
    correlation_id: str | None = None,
) -> None:
    """Update a single memory by ID."""
    if (backend := resolve_memory_backend(caller_context, config, runtime_paths)) is None:
        return
    flight_facts = {
        "operation": "update",
        "backend": backend.context_label,
        "scope": caller_context,
        "memory_id": memory_id,
        "session_id": execution_identity.session_id if execution_identity is not None else None,
        "correlation_id": correlation_id,
    }
    await _record_memory_write(runtime_paths, status="requested", side_effect=False, **flight_facts)
    await backend.update(
        memory_id,
        content,
        caller_context,
        storage_path,
        config,
        execution_identity=execution_identity,
    )
    await _record_completed_memory_write(runtime_paths, **flight_facts)


async def delete_agent_memory(
    memory_id: str,
    caller_context: str | list[str],
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    execution_identity: ToolExecutionIdentity | None = None,
    correlation_id: str | None = None,
) -> None:
    """Delete a single memory by ID."""
    if (backend := resolve_memory_backend(caller_context, config, runtime_paths)) is None:
        return
    flight_facts = {
        "operation": "delete",
        "backend": backend.context_label,
        "scope": caller_context,
        "memory_id": memory_id,
        "session_id": execution_identity.session_id if execution_identity is not None else None,
        "correlation_id": correlation_id,
    }
    await _record_memory_write(runtime_paths, status="requested", side_effect=False, **flight_facts)
    await backend.delete(
        memory_id,
        caller_context,
        storage_path,
        config,
        execution_identity=execution_identity,
    )
    await _record_completed_memory_write(runtime_paths, **flight_facts)


@timed("system_prompt_assembly.memory_enhancement")
async def build_memory_prompt_parts(
    prompt: str,
    agent_name: str,
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    execution_identity: ToolExecutionIdentity | None = None,
) -> MemoryPromptParts:
    """Split stable entrypoint context from turn-local searched memories."""
    logger.debug("Building enhanced prompt", agent=agent_name)
    if (backend := resolve_memory_backend(agent_name, config, runtime_paths)) is None:
        return MemoryPromptParts()

    search_outcome = await search_agent_memories(
        prompt,
        agent_name,
        storage_path,
        config,
        runtime_paths,
        execution_identity=execution_identity,
    )
    agent_memories = search_outcome.results
    if agent_memories:
        logger.debug("Agent memories added", count=len(agent_memories))

    session_preamble = ""
    # The file backend reads the scoped MEMORY.md from disk; keep it off the
    # event loop (#1260).
    agent_entrypoint = await asyncio.to_thread(
        backend.load_entrypoint_context,
        agent_name,
        storage_path,
        config,
        execution_identity=execution_identity,
    )
    if agent_entrypoint:
        session_preamble = f"{config.get_prompt('FILE_MEMORY_ENTRYPOINT_HEADER')}\n{agent_entrypoint}"

    # The automatic per-turn path must not silently drop the degradation
    # signal: a broken embedder would otherwise look like an agent with no
    # relevant memories, the original ISSUE-237 failure shape.
    degradation_notice = ""
    if search_outcome.degraded_reason is not None:
        degradation_notice = (
            f"Semantic memory search is unavailable this turn ({search_outcome.degraded_reason}); "
            "stored memories may be missing or keyword-only. Do not claim to have checked stored memories."
        )

    memory_context = (
        format_memories_as_context(
            agent_memories,
            backend.context_label,
            prompt_template=config.get_prompt("MEMORY_CONTEXT_PROMPT_TEMPLATE"),
        )
        if agent_memories
        else ""
    )
    turn_context = "\n\n".join(part for part in (degradation_notice, memory_context) if part)
    return MemoryPromptParts(
        session_preamble=session_preamble,
        turn_context=turn_context,
    )


async def build_memory_enhanced_prompt(
    prompt: str,
    agent_name: str,
    storage_path: Path,
    config: Config,
    runtime_paths: RuntimePaths,
    execution_identity: ToolExecutionIdentity | None = None,
) -> str:
    """Compatibility wrapper that preserves the legacy monolithic prompt shape."""
    prompt_parts = await build_memory_prompt_parts(
        prompt,
        agent_name,
        storage_path,
        config,
        runtime_paths,
        execution_identity=execution_identity,
    )
    prompt_chunks = [chunk for chunk in (prompt_parts.session_preamble, prompt_parts.turn_context, prompt) if chunk]
    return "\n\n".join(prompt_chunks)


async def store_conversation_memory(
    prompt: str,
    agent_name: str | list[str],
    storage_path: Path,
    session_id: str,
    config: Config,
    runtime_paths: RuntimePaths,
    thread_history: Sequence[ResolvedVisibleMessage] | None = None,
    user_id: str | None = None,
    execution_identity: ToolExecutionIdentity | None = None,
    correlation_id: str | None = None,
) -> None:
    """Store conversation in memory for future recall."""
    if not prompt:
        return
    if (backend := resolve_memory_backend(agent_name, config, runtime_paths)) is None:
        return
    flight_facts = {
        "operation": "store_conversation",
        "backend": backend.context_label,
        "scope": agent_name,
        "session_id": session_id,
        "correlation_id": correlation_id,
    }
    await _record_memory_write(runtime_paths, status="requested", side_effect=False, **flight_facts)
    await backend.store_conversation(
        prompt,
        agent_name,
        storage_path,
        session_id,
        config,
        thread_history=thread_history,
        user_id=user_id,
        execution_identity=execution_identity,
    )
    await _record_completed_memory_write(runtime_paths, **flight_facts)
