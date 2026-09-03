"""Core data models for the P4 Federated Mission Compiler.

Defines the fundamental types used throughout the compiler pipeline:
- MissionStep: individual DAG node with execution metadata
- MissionDAG: directed acyclic graph of mission steps
- MissionSpec: full mission specification with metadata and constraints
- CompilationResult: output of the compilation pipeline
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any, Literal


class StepType(str, enum.Enum):
    """Type of a mission step, determining how it is executed."""

    TASK = "task"
    """Standard execution step."""

    DECISION = "decision"
    """Branching/decision point — output determines which path to follow."""

    SUB_MISSION = "sub_mission"
    """Nested sub-mission executed as a unit."""

    ROLLBACK = "rollback"
    """Compensating action for a previous step (saga pattern)."""

    WAIT = "wait"
    """Pause execution until a condition is met or timeout expires."""


class RuntimeTarget(str, enum.Enum):
    """Target runtime for a mission step."""

    MINDROOM = "mindroom"
    """MindRoom primary runtime — standard agent execution."""

    OPENCLAW = "openclaw"
    """OpenClaw worker runtime — sandboxed or dedicated worker."""

    HERMES = "hermes"
    """Hermes runtime — external service or function."""


@dataclass(frozen=True)
class RetryPolicy:
    """Retry policy for a mission step.

    Controls how many times a step is retried on failure and the backoff
    strategy between attempts.
    """

    max_attempts: int = 3
    """Maximum number of execution attempts (including the first)."""

    backoff: Literal["exponential", "fixed", "immediate"] = "exponential"
    """Backoff strategy between retries."""

    base_delay_seconds: float = 5.0
    """Base delay before the first retry."""

    max_delay_seconds: float = 120.0
    """Maximum delay between retries (cap for exponential backoff)."""

    backoff_factor: float = 2.0
    """Multiplier for exponential backoff."""

    retry_on: tuple[str, ...] = ("timeout", "worker_error", "partial_result")
    """Failure types that trigger a retry."""

    no_retry_on: tuple[str, ...] = ("invalid_input", "capability_mismatch")
    """Failure types that never trigger a retry."""

    def __post_init__(self) -> None:
        """Validate retry policy constraints."""
        if self.max_attempts < 1:
            msg = f"max_attempts must be >= 1, got {self.max_attempts}"
            raise ValueError(msg)
        if self.base_delay_seconds < 0:
            msg = f"base_delay_seconds must be >= 0, got {self.base_delay_seconds}"
            raise ValueError(msg)
        if self.max_delay_seconds < self.base_delay_seconds:
            msg = (
                f"max_delay_seconds ({self.max_delay_seconds}) must be >= "
                f"base_delay_seconds ({self.base_delay_seconds})"
            )
            raise ValueError(msg)
        if self.backoff_factor <= 0:
            msg = f"backoff_factor must be > 0, got {self.backoff_factor}"
            raise ValueError(msg)


@dataclass(frozen=True)
class MissionStep:
    """A single step in a mission DAG.

    Each step represents one unit of work that can be assigned to a worker
    on a supported runtime. Steps declare their dependencies, input/output
    contracts, timeout, and retry policy.
    """

    id: str
    """Unique identifier for this step within the mission."""

    type: StepType = StepType.TASK
    """Type of step — determines execution semantics."""

    description: str = ""
    """Human-readable description of what this step does."""

    inputs: dict[str, Any] = field(default_factory=dict)
    """Input parameters for this step. Keys are parameter names."""

    outputs: dict[str, str] = field(default_factory=dict)
    """Output contract. Keys are output names, values are type annotations."""

    dependencies: tuple[str, ...] = field(default_factory=tuple)
    """IDs of steps that must complete before this step can execute."""

    timeout: float = 300.0
    """Maximum execution time in seconds."""

    retry: RetryPolicy = field(default_factory=RetryPolicy)
    """Retry policy for this step."""

    target_runtime: RuntimeTarget = RuntimeTarget.MINDROOM
    """Target runtime for executing this step."""

    capabilities: tuple[str, ...] = field(default_factory=tuple)
    """Required capabilities for executing this step."""

    rollback_step_id: str | None = None
    """ID of a compensating step to execute on failure (saga pattern)."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary metadata for extensibility."""

    def __post_init__(self) -> None:
        """Validate step constraints."""
        if not self.id:
            msg = "Step id must be a non-empty string"
            raise ValueError(msg)
        if self.timeout <= 0:
            msg = f"timeout must be > 0, got {self.timeout}"
            raise ValueError(msg)
        if self.rollback_step_id == self.id:
            msg = f"Step {self.id} cannot reference itself as its rollback step"
            raise ValueError(msg)


@dataclass(frozen=True)
class MissionDAG:
    """A directed acyclic graph of mission steps.

    The DAG represents the execution plan for a mission. Steps are nodes
    and dependencies are edges. The graph must be acyclic and all dependency
    references must resolve to existing steps.
    """

    steps: tuple[MissionStep, ...] = field(default_factory=tuple)
    """Ordered collection of steps in the DAG. Order is not significant."""

    entry_points: tuple[str, ...] = field(default_factory=tuple)
    """Step IDs that have no dependencies (root nodes). Computed if empty."""

    def __post_init__(self) -> None:
        """Compute entry points if not explicitly provided."""
        if not self.entry_points:
            object.__setattr__(
                self,
                "entry_points",
                tuple(
                    step.id for step in self.steps if not step.dependencies
                ),
            )

    @property
    def step_ids(self) -> frozenset[str]:
        """Return the set of all step IDs in this DAG."""
        return frozenset(step.id for step in self.steps)

    @property
    def step_map(self) -> dict[str, MissionStep]:
        """Return a mapping from step ID to step."""
        return {step.id: step for step in self.steps}

    def get_step(self, step_id: str) -> MissionStep | None:
        """Look up a step by ID."""
        return self.step_map.get(step_id)


@dataclass(frozen=True)
class MissionSpec:
    """Full mission specification.

    A MissionSpec is the input to the compiler. It describes the mission
    metadata, the DAG of steps, global constraints, and the output contract.
    """

    mission_id: str
    """Unique identifier for this mission."""

    name: str
    """Human-readable mission name."""

    version: str = "1.0"
    """Mission specification version."""

    author: str = ""
    """Author or originating agent."""

    created: str = ""
    """ISO 8601 timestamp of creation."""

    dag: MissionDAG = field(default_factory=lambda: MissionDAG())
    """The directed acyclic graph of mission steps."""

    timeout: float = 600.0
    """Mission-level timeout in seconds."""

    constraints: dict[str, Any] = field(default_factory=dict)
    """Global constraints (min/max workers, required capabilities, etc.)."""

    metadata: dict[str, Any] = field(default_factory=dict)
    """Arbitrary metadata for extensibility."""

    def __post_init__(self) -> None:
        """Validate mission spec constraints."""
        if not self.mission_id:
            msg = "mission_id must be a non-empty string"
            raise ValueError(msg)
        if not self.name:
            msg = "name must be a non-empty string"
            raise ValueError(msg)
        if self.timeout <= 0:
            msg = f"timeout must be > 0, got {self.timeout}"
            raise ValueError(msg)


@dataclass(frozen=True)
class CompilationError:
    """An actionable compilation error with location and remediation guidance."""

    code: str
    """Machine-readable error code (e.g., 'CYCLE_DETECTED', 'MISSING_DEPENDENCY')."""

    message: str
    """Human-readable error description."""

    step_id: str | None = None
    """The step ID where the error was detected, if applicable."""

    details: str = ""
    """Additional context or remediation guidance."""


@dataclass(frozen=True)
class CompilationWarning:
    """A non-fatal compilation warning."""

    code: str
    """Machine-readable warning code."""

    message: str
    """Human-readable warning description."""

    step_id: str | None = None
    """The step ID where the warning was detected, if applicable."""

    details: str = ""
    """Additional context."""


@dataclass(frozen=True)
class CompilationResult:
    """Result of compiling a MissionSpec.

    Contains the compiled plan (a MissionDAG with validated metadata),
    any errors or warnings, and the overall success status.
    """

    success: bool
    """Whether compilation succeeded without errors."""

    compiled_dag: MissionDAG | None = None
    """The validated and compiled DAG. Present only on success."""

    errors: tuple[CompilationError, ...] = field(default_factory=tuple)
    """Compilation errors. Empty on success."""

    warnings: tuple[CompilationWarning, ...] = field(default_factory=tuple)
    """Compilation warnings. May be present even on success."""

    changed_step_ids: frozenset[str] = field(default_factory=frozenset)
    """Step IDs that were re-validated during incremental compilation."""