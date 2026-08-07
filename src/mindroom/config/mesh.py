"""Agent Mesh configuration section.

This is the fully additive P1 config schema for the Agent Mesh Gateway.  It
follows the existing per-section sub-model style used across ``config/main.py``:
a root ``MeshConfig`` Pydantic section with ``extra="forbid"`` and nested
sub-sections for each mesh capability.

Everything in this module is opt-in and gated behind default-``OFF`` flags, so
gateway-only runtime behavior is unchanged unless explicitly configured.
"""

from __future__ import annotations

import enum
import os

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "MESH_GATEWAY_MODE_ENV",
    "MeshCancellationConfig",
    "MeshConfig",
    "MeshCursorConfig",
    "MeshEnrollmentConfig",
    "MeshLoopConfig",
    "MeshRuntimeMode",
    "MeshSessionMappingConfig",
    "MeshToolStateConfig",
    "resolve_mesh_runtime_mode",
]

# The env var that drives the existing GatewayRuntimeMode.from_env.  We keep a
# local copy of the constant here so the config layer stays decoupled from the
# mesh runtime module (which must not be imported at config-construction time).
MESH_GATEWAY_MODE_ENV = "MINDROOM_MESH_GATEWAY_MODE"


class MeshRuntimeMode(str, enum.Enum):
    """Runtime mode controlling gateway vs. full mesh execution."""

    GATEWAY_ONLY = "gateway_only"
    FULL = "full"


class MeshEnrollmentConfig(BaseModel):
    """Mesh worker auto-enrollment settings."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether mesh workers are auto-enrolled on registration",
    )
    require_auth_token: bool = Field(
        default=False,
        description="Whether mesh workers must present an auth token to enroll",
    )


class MeshSessionMappingConfig(BaseModel):
    """Session/thread mapping for mesh workers."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether session/identity mapping is active for mesh workers",
    )
    store_path: str | None = Field(
        default=None,
        description="Durable session-map storage path (directory), when mapping is enabled",
    )


class MeshToolStateConfig(BaseModel):
    """Worker tool-state mirroring across the mesh."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether worker tool state is replicated across the mesh",
    )


class MeshCancellationConfig(BaseModel):
    """Outbox cancellation behavior for the mesh gateway."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether cancellation of pending outbox entries is enabled",
    )
    default_cancel_source: str = Field(
        default="user_stop",
        description="Default cancellation source when cancelling a pending outbox entry",
    )


class MeshCursorConfig(BaseModel):
    """Reconnect-cursor persistence for the mesh gateway."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether reconnect-cursor persistence is enabled",
    )
    persist: bool = Field(
        default=True,
        description="Whether cursors are persisted to disk when a storage path is available",
    )


class MeshLoopConfig(BaseModel):
    """Loop-prevention limits for the mesh gateway."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = Field(
        default=False,
        description="Whether loop prevention is enabled for the mesh gateway",
    )
    max_hops: int = Field(
        default=8,
        ge=1,
        description="Maximum number of hops a mesh message may traverse before being dropped",
    )
    ttl_seconds: int = Field(
        default=300,
        ge=1,
        description="Maximum age of a mesh message before it is treated as stale and dropped",
    )


class MeshConfig(BaseModel):
    """Agent Mesh configuration section (gated, additive)."""

    model_config = ConfigDict(extra="forbid")

    mode: MeshRuntimeMode = Field(
        default=MeshRuntimeMode.GATEWAY_ONLY,
        description="Configured mesh runtime mode (file config wins over env when explicitly set)",
    )
    enrollment: MeshEnrollmentConfig = Field(
        default_factory=MeshEnrollmentConfig,
        description="Mesh worker auto-enrollment settings",
    )
    session_mapping: MeshSessionMappingConfig = Field(
        default_factory=MeshSessionMappingConfig,
        description="Session/identity mapping settings",
    )
    tool_state: MeshToolStateConfig = Field(
        default_factory=MeshToolStateConfig,
        description="Worker tool-state mirroring settings",
    )
    cancellation: MeshCancellationConfig = Field(
        default_factory=MeshCancellationConfig,
        description="Outbox cancellation settings",
    )
    cursor: MeshCursorConfig = Field(
        default_factory=MeshCursorConfig,
        description="Reconnect-cursor persistence settings",
    )
    loop: MeshLoopConfig = Field(
        default_factory=MeshLoopConfig,
        description="Loop-prevention limits",
    )


def _mesh_section(config: object) -> MeshConfig:
    """Return the ``MeshConfig`` section from a root ``Config`` or the section itself."""
    section = getattr(config, "mesh", config)
    if not isinstance(section, MeshConfig):
        msg = f"resolve_mesh_runtime_mode expects a MeshConfig or a Config with a mesh section, got {type(config)!r}"
        raise TypeError(msg)
    return section


def resolve_mesh_runtime_mode(
    config: MeshConfig | object,
    env: dict[str, str] | None = None,
) -> MeshRuntimeMode:
    """Resolve the effective mesh runtime mode.

    Precedence:
      1. ``mesh.mode`` explicitly authored in the file config
      2. ``MINDROOM_MESH_GATEWAY_MODE`` env var
      3. ``GATEWAY_ONLY`` default

    This is deliberately independent of ``GatewayRuntimeMode.from_env`` (which
    is left untouched): the file config wins when it explicitly sets ``mode``,
    otherwise the env var is honoured, otherwise the default applies.
    """
    section = _mesh_section(config)
    if "mode" in section.model_fields_set:
        return section.mode

    source = env if env is not None else os.environ
    raw = (source.get(MESH_GATEWAY_MODE_ENV) or "").strip().lower()
    if raw in ("full",):
        return MeshRuntimeMode.FULL
    return MeshRuntimeMode.GATEWAY_ONLY