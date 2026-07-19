"""MindRoom external runtime bridge foundation (not yet wired to ingress)."""

from .adapter import (
    ConsequentialExecutionDenied,
    NDJSONSubprocessAdapter,
    RuntimeAdapter,
    RuntimeContractError,
    SubprocessContractConfig,
)
from .hermes import HermesAdapter
from .integration import MatrixRuntimeBridge, RoomEncryptionState, RuntimeDelivery
from .lifecycle import RuntimeBridgeLifecycle
from .models import (
    CONTRACT_VERSION,
    ConversationScope,
    EventOrigin,
    RuntimeIdentity,
    RuntimeName,
    RuntimeRequest,
    RuntimeResult,
)
from .openclaw import OpenClawAdapter
from .relay import PORTAL_ROOM_ID, MilestoneRelay, MilestoneRelayEntry, MilestoneRelayStore, milestone_entry
from .service import DuplicateSourceEventError, RuntimeBridgeService
from .store import RuntimeBridgeStore, RuntimeStateEvent, SourceEventConflictError, validate_database

__all__ = [
    "CONTRACT_VERSION",
    "PORTAL_ROOM_ID",
    "ConsequentialExecutionDenied",
    "ConversationScope",
    "DuplicateSourceEventError",
    "EventOrigin",
    "HermesAdapter",
    "MatrixRuntimeBridge",
    "MilestoneRelay",
    "MilestoneRelayEntry",
    "MilestoneRelayStore",
    "NDJSONSubprocessAdapter",
    "OpenClawAdapter",
    "RoomEncryptionState",
    "RuntimeAdapter",
    "RuntimeBridgeLifecycle",
    "RuntimeBridgeService",
    "RuntimeBridgeStore",
    "RuntimeContractError",
    "RuntimeDelivery",
    "RuntimeIdentity",
    "RuntimeName",
    "RuntimeRequest",
    "RuntimeResult",
    "RuntimeStateEvent",
    "SourceEventConflictError",
    "SubprocessContractConfig",
    "milestone_entry",
    "validate_database",
]
