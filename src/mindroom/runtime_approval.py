"""Exact-payload ARIP gates for consequential OpenClaw and Hermes actions."""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from mindroom.arip import JsonValue, canonical_json

if TYPE_CHECKING:
    from datetime import datetime

    from mindroom.approval_manager import ApprovalDecision
    from mindroom.arip_control import ApprovalControlStore

RuntimeName = Literal["openclaw", "hermes"]
RuntimeExecutor = Callable[["RuntimeAction", str], Awaitable[str]]


class LiveApprovalManager(Protocol):
    """Narrow live Matrix/Telegram approval transport used by runtime adapters."""

    async def request_approval(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        room_id: str | None,
        requester_id: str | None,
        approver_user_id: str | None,
        timeout_seconds: float,
        agent_name: str | None = None,
        thread_id: str | None = None,
        workflow_id: str | None = None,
        participant_id: str | None = None,
    ) -> ApprovalDecision:
        """Request, resolve, and consume one exact live approval."""
        ...


class RuntimeApprovalError(RuntimeError):
    """A runtime approval identity or execution invariant failed."""


@dataclass(frozen=True, slots=True)
class RuntimeAction:
    """One immutable external-runtime action bound to an ARIP approval."""

    action_id: str
    runtime: RuntimeName
    operation: str
    arguments: JsonValue
    approval_id: str

    def __post_init__(self) -> None:
        """Validate identity fields and strict JSON arguments."""
        if not self.action_id.strip() or not self.operation.strip() or not self.approval_id.strip():
            message = "runtime action identity, operation, and approval are required"
            raise RuntimeApprovalError(message)
        canonical_json(self.arguments)

    @property
    def approval_tool_name(self) -> str:
        """Return a runtime-namespaced tool identity that prevents substitution."""
        return f"runtime.{self.runtime}.{self.operation}"

    @property
    def approval_arguments(self) -> JsonValue:
        """Return the complete immutable envelope used for approval hashing."""
        return {
            "action_id": self.action_id,
            "arguments": self.arguments,
            "runtime": self.runtime,
        }

    @property
    def idempotency_key(self) -> str:
        """Return a stable executor key derived from the exact approved envelope."""
        value = {
            "approval_arguments": self.approval_arguments,
            "approval_tool_name": self.approval_tool_name,
        }
        return hashlib.sha256(canonical_json(value)).hexdigest()


class RuntimeApprovalAdapter:
    """Consume one matching ARIP grant immediately before one runtime attempt.

    The adapter never retries. Its caller must durably mark an interrupted call
    uncertain because authorization consumption proves an attempt may have begun.
    """

    def __init__(
        self,
        *,
        runtime: RuntimeName,
        approval_store: ApprovalControlStore,
        executor: RuntimeExecutor,
        live_manager: LiveApprovalManager | None = None,
    ) -> None:
        self._runtime = runtime
        self._approval_store = approval_store
        self._executor = executor
        self._live_manager = live_manager

    async def execute(self, action: RuntimeAction, *, observed_at: datetime) -> str:
        """Authorize the exact envelope, invoke once, and require a receipt."""
        if action.runtime != self._runtime:
            message = "runtime action does not match its approval adapter"
            raise RuntimeApprovalError(message)
        await self._approval_store.consume(
            approval_id=action.approval_id,
            tool_name=action.approval_tool_name,
            arguments=action.approval_arguments,
            observed_at=observed_at,
        )
        receipt = await self._executor(action, action.idempotency_key)
        if not isinstance(receipt, str) or not receipt.strip():
            message = "runtime action executor returned no receipt"
            raise RuntimeApprovalError(message)
        return receipt

    async def request_and_execute(
        self,
        *,
        action_id: str,
        operation: str,
        arguments: JsonValue,
        room_id: str,
        requester_id: str,
        approver_user_id: str,
        timeout_seconds: float,
        thread_id: str | None = None,
    ) -> str:
        """Request live approval, consume it through the manager, then invoke once."""
        if self._live_manager is None:
            message = "live runtime approval manager is not configured"
            raise RuntimeApprovalError(message)
        action = RuntimeAction(action_id, self._runtime, operation, arguments, "live-managed")
        approval_arguments = action.approval_arguments
        if not isinstance(approval_arguments, dict):
            message = "runtime approval envelope must be an object"
            raise RuntimeApprovalError(message)
        decision = await self._live_manager.request_approval(
            tool_name=action.approval_tool_name,
            arguments=approval_arguments,
            room_id=room_id,
            requester_id=requester_id,
            approver_user_id=approver_user_id,
            timeout_seconds=timeout_seconds,
            agent_name=f"runtime:{self._runtime}",
            thread_id=thread_id,
            participant_id=self._runtime,
        )
        if decision.status != "approved":
            message = f"runtime action approval denied (status={decision.status})"
            raise RuntimeApprovalError(message)
        receipt = await self._executor(action, action.idempotency_key)
        if not isinstance(receipt, str) or not receipt.strip():
            message = "runtime action executor returned no receipt"
            raise RuntimeApprovalError(message)
        return receipt


class OpenClawApprovalAdapter(RuntimeApprovalAdapter):
    """Exact-payload approval adapter for OpenClaw actions."""

    def __init__(
        self,
        approval_store: ApprovalControlStore,
        executor: RuntimeExecutor,
        live_manager: LiveApprovalManager | None = None,
    ) -> None:
        super().__init__(
            runtime="openclaw",
            approval_store=approval_store,
            executor=executor,
            live_manager=live_manager,
        )


class HermesApprovalAdapter(RuntimeApprovalAdapter):
    """Exact-payload approval adapter for Hermes actions."""

    def __init__(
        self,
        approval_store: ApprovalControlStore,
        executor: RuntimeExecutor,
        live_manager: LiveApprovalManager | None = None,
    ) -> None:
        super().__init__(runtime="hermes", approval_store=approval_store, executor=executor, live_manager=live_manager)
