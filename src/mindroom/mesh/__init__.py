"""Agent Mesh Gateway: gateway-only runtime mode and cross-worker message routing.

This package implements the P1 Agent Mesh Gateway — a gateway-only runtime mode
where the Matrix transport layer is active (sync loop, delivery, event cache)
but worker execution is gated (no sandbox runner, no dedicated workers).  The
gateway routes messages between registered workers using Matrix rooms as the
wire protocol, with reconnect cursors for resumable delivery.

Built on existing MindRoom primitives:
- Identity/session: ``ToolExecutionIdentity``, ``session_ids``
- Outbox: provenance-memory propagation outbox pattern
- Cancellation: ``cancellation`` module (user_stop / sync_restart)
- Content-free lifecycle: content-free outcome streaming from provenance drain
- Reconnect cursors: ``sync_tokens`` / ``sync_certification``
- Worker backends: ``workers.backend`` Protocol
- Delivery: ``delivery_gateway`` for Matrix transport
"""

from __future__ import annotations

from mindroom.mesh.cursor import MeshCursorStore, MeshReconnectCursor
from mindroom.mesh.enrollment import (
    PHASE_B_HANDSHAKE_ENABLED,
    MeshEnrollmentAuthority,
    MeshEnrollmentCoordinator,
    MeshEnrollmentError,
    MeshEnrollmentRegistry,
    MeshEnrollmentResult,
    MeshWorkerIdentity,
    enrollment_flag_enabled,
)
from mindroom.mesh.gateway import (
    GatewayExecutionGate,
    GatewayOnlyRuntime,
    GatewayRuntimeMode,
    MeshGateway,
    MeshGatewayError,
)
from mindroom.mesh.lifecycle import (
    MeshLifecycleEvent,
    MeshLifecycleEventType,
    MeshLifecycleSink,
    content_free_lifecycle_outcomes,
)
from mindroom.mesh.loop_guard import MeshLoopError, MeshLoopGuard, MeshLoopVerdict
from mindroom.mesh.models import (
    MeshMessage,
    MeshMessageEnvelope,
    MeshRouteDecision,
    MeshWorkerRegistration,
    MeshWorkerStatus,
)
from mindroom.mesh.reconnect import (
    MESH_RESUME_ENV,
    PHASE_B_RESUME_ENABLED,
    MeshReconnectCoordinator,
    MeshResumeResult,
    resume_flag_enabled,
)
from mindroom.mesh.transport import (
    MatrixMeshTransport,
    MeshTransport,
    MeshTransportError,
)

__all__ = [
    "MESH_RESUME_ENV",
    "PHASE_B_HANDSHAKE_ENABLED",
    "PHASE_B_RESUME_ENABLED",
    "GatewayExecutionGate",
    "GatewayOnlyRuntime",
    "GatewayRuntimeMode",
    "MatrixMeshTransport",
    "MeshCursorStore",
    "MeshEnrollmentAuthority",
    "MeshEnrollmentCoordinator",
    "MeshEnrollmentError",
    "MeshEnrollmentRegistry",
    "MeshEnrollmentResult",
    "MeshGateway",
    "MeshGatewayError",
    "MeshLifecycleEvent",
    "MeshLifecycleEventType",
    "MeshLifecycleSink",
    "MeshLoopError",
    "MeshLoopGuard",
    "MeshLoopVerdict",
    "MeshMessage",
    "MeshMessageEnvelope",
    "MeshReconnectCoordinator",
    "MeshReconnectCursor",
    "MeshResumeResult",
    "MeshRouteDecision",
    "MeshTransport",
    "MeshTransportError",
    "MeshWorkerIdentity",
    "MeshWorkerRegistration",
    "MeshWorkerStatus",
    "content_free_lifecycle_outcomes",
    "enrollment_flag_enabled",
    "resume_flag_enabled",
]
