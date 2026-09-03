"""MissionCompiler — the core compilation engine for the P4 Federated Mission Compiler.

The MissionCompiler:
1. Accepts a MissionSpec
2. Validates the DAG using DAGValidator
3. Produces a CompilationResult
4. Supports incremental compilation (re-validate only changed steps)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from mindroom.compiler.models import (
    CompilationError,
    CompilationResult,
    CompilationWarning,
    MissionDAG,
    MissionSpec,
    MissionStep,
)
from mindroom.compiler.validation import DAGValidator, ValidationResult

logger = logging.getLogger(__name__)


def _stable_dict_hash(d: dict[str, object]) -> int:
    """Compute a stable hash for a dictionary.

    Handles nested dictionaries by converting to a sorted tuple of
    (key, value) pairs. Values that are themselves dicts are recursively
    hashed.
    """
    items: list[tuple[str, int]] = []
    for key, value in sorted(d.items()):
        if isinstance(value, dict):
            value_hash = _stable_dict_hash(value)
        else:
            value_hash = hash(repr(value))
        items.append((key, value_hash))
    return hash(tuple(items))


@dataclass
class CompilationCache:
    """Cache for incremental compilation.

    Stores the last known state of each step so that unchanged steps
    can skip re-validation.
    """

    step_hashes: dict[str, int] = field(default_factory=dict)
    """Hash of each step's content for change detection."""

    last_result: CompilationResult | None = None
    """The last successful compilation result."""


class MissionCompiler:
    """Compiles MissionSpecs into validated, executable MissionDAGs.

    The compiler is the central orchestrator of the compilation pipeline.
    It validates the mission DAG, enriches it with defaults, and produces
    a CompilationResult that downstream stages (decompose, assign, execute)
    can consume.

    Supports incremental compilation: when re-compiling with only a subset
    of steps changed, only those steps are re-validated.
    """

    def __init__(self) -> None:
        """Initialize the compiler with an empty cache."""
        self._cache: dict[str, CompilationCache] = {}
        """Per-mission compilation caches, keyed by mission_id."""

    def compile(
        self,
        spec: MissionSpec,
        *,
        changed_step_ids: frozenset[str] | None = None,
    ) -> CompilationResult:
        """Compile a mission spec into a validated DAG.

        Args:
            spec: The mission specification to compile.
            changed_step_ids: If provided, only these steps are re-validated
                (incremental compilation). All other steps use cached results.

        Returns:
            A CompilationResult with the compiled DAG and any errors/warnings.
        """
        mission_id = spec.mission_id

        # Get or create cache for this mission
        cache = self._cache.setdefault(mission_id, CompilationCache())

        # Detect changed steps if not explicitly provided
        if changed_step_ids is None:
            changed_step_ids = self._detect_changed_steps(spec, cache)

        # Run validation
        validator = DAGValidator(spec)

        if changed_step_ids and cache.last_result is not None:
            # Incremental: only validate changed steps
            validation_result = validator.validate_steps(changed_step_ids)
        else:
            # Full validation
            validation_result = validator.validate()

        # If validation failed, return errors
        if not validation_result.valid:
            result = CompilationResult(
                success=False,
                errors=validation_result.errors,
                warnings=validation_result.warnings,
                changed_step_ids=changed_step_ids,
            )
            # Still cache the result for incremental compilation
            self._update_cache(spec, cache, result)
            return result

        # Build the compiled DAG with enriched metadata
        compiled_dag = self._enrich_dag(spec.dag, validation_result)

        result = CompilationResult(
            success=True,
            compiled_dag=compiled_dag,
            warnings=validation_result.warnings,
            changed_step_ids=changed_step_ids,
        )

        # Update cache
        self._update_cache(spec, cache, result)

        return result

    def compile_from_spec_dict(
        self,
        spec_dict: dict[str, Any],
        *,
        changed_step_ids: frozenset[str] | None = None,
    ) -> CompilationResult:
        """Compile a mission spec from a dictionary.

        This is a convenience method for programmatic usage. The dictionary
        should follow the same structure as MissionSpec fields.

        Args:
            spec_dict: Dictionary representation of a MissionSpec.
            changed_step_ids: Optional set of step IDs for incremental compilation.

        Returns:
            A CompilationResult.
        """
        spec = self._dict_to_spec(spec_dict)
        return self.compile(spec, changed_step_ids=changed_step_ids)

    def get_cache(self, mission_id: str) -> CompilationCache | None:
        """Return the compilation cache for a mission, if any."""
        return self._cache.get(mission_id)

    def clear_cache(self, mission_id: str | None = None) -> None:
        """Clear the compilation cache.

        Args:
            mission_id: If provided, clear only the cache for this mission.
                If None, clear all caches.
        """
        if mission_id is None:
            self._cache.clear()
        else:
            self._cache.pop(mission_id, None)

    # ── Internal helpers ──────────────────────────────────────────────

    def _detect_changed_steps(
        self,
        spec: MissionSpec,
        cache: CompilationCache,
    ) -> frozenset[str]:
        """Detect which steps have changed since the last compilation.

        Uses a simple hash of each step's content for change detection.
        """
        if cache.last_result is None:
            # No prior compilation — all steps are "changed"
            return spec.dag.step_ids

        changed: set[str] = set()
        for step in spec.dag.steps:
            step_hash = self._step_hash(step)
            cached_hash = cache.step_hashes.get(step.id)
            if cached_hash is None or step_hash != cached_hash:
                changed.add(step.id)

        # Also detect removed steps
        for cached_id in cache.step_hashes:
            if cached_id not in spec.dag.step_map:
                changed.add(cached_id)

        return frozenset(changed)

    def _update_cache(
        self,
        spec: MissionSpec,
        cache: CompilationCache,
        result: CompilationResult,
    ) -> None:
        """Update the compilation cache after a compilation pass."""
        cache.last_result = result
        cache.step_hashes = {
            step.id: self._step_hash(step) for step in spec.dag.steps
        }

    @staticmethod
    def _step_hash(step: MissionStep) -> int:
        """Compute a stable hash for a MissionStep.

        Uses a string representation since frozen dataclasses with dict
        fields are not natively hashable.
        """
        return hash((
            step.id,
            step.type.value,
            step.description,
            _stable_dict_hash(step.inputs),
            _stable_dict_hash(step.outputs),
            step.dependencies,
            step.timeout,
            step.retry.max_attempts,
            step.retry.backoff,
            step.retry.base_delay_seconds,
            step.retry.max_delay_seconds,
            step.retry.backoff_factor,
            step.retry.retry_on,
            step.retry.no_retry_on,
            step.target_runtime.value,
            step.capabilities,
            step.rollback_step_id,
            _stable_dict_hash(step.metadata),
        ))

    def _enrich_dag(
        self,
        dag: MissionDAG,
        validation_result: ValidationResult,
    ) -> MissionDAG:
        """Enrich the DAG with computed metadata.

        Currently:
        - Sets the topological order as step ordering
        - Ensures entry points are computed
        """
        # Reorder steps by topological order for deterministic output
        order = validation_result.topological_order
        ordered_steps = tuple(
            dag.step_map[step_id] for step_id in order
        )

        return MissionDAG(
            steps=ordered_steps,
            entry_points=dag.entry_points or tuple(
                step_id for step_id in order
                if not dag.step_map[step_id].dependencies
            ),
        )

    def _dict_to_spec(self, spec_dict: dict[str, Any]) -> MissionSpec:
        """Convert a dictionary to a MissionSpec.

        Handles nested structures including MissionDAG and MissionStep.
        """
        from mindroom.compiler.models import RetryPolicy, RuntimeTarget, StepType  # noqa: PLC0415

        dag_data = spec_dict.get("dag", {})
        steps_data = dag_data.get("steps", [])

        steps: list[MissionStep] = []
        for step_data in steps_data:
            # Parse retry policy
            retry_data = step_data.get("retry", {})
            if isinstance(retry_data, dict):
                retry = RetryPolicy(**retry_data)
            elif isinstance(retry_data, RetryPolicy):
                retry = retry_data
            else:
                retry = RetryPolicy()

            # Parse enums
            step_type = StepType(step_data.get("type", "task"))
            target_runtime = RuntimeTarget(step_data.get("target_runtime", "mindroom"))

            step = MissionStep(
                id=step_data["id"],
                type=step_type,
                description=step_data.get("description", ""),
                inputs=step_data.get("inputs", {}),
                outputs=step_data.get("outputs", {}),
                dependencies=tuple(step_data.get("dependencies", [])),
                timeout=float(step_data.get("timeout", 300.0)),
                retry=retry,
                target_runtime=target_runtime,
                capabilities=tuple(step_data.get("capabilities", [])),
                rollback_step_id=step_data.get("rollback_step_id"),
                metadata=step_data.get("metadata", {}),
            )
            steps.append(step)

        dag = MissionDAG(
            steps=tuple(steps),
            entry_points=tuple(dag_data.get("entry_points", [])),
        )

        return MissionSpec(
            mission_id=spec_dict["mission_id"],
            name=spec_dict["name"],
            version=spec_dict.get("version", "1.0"),
            author=spec_dict.get("author", ""),
            created=spec_dict.get("created", ""),
            dag=dag,
            timeout=float(spec_dict.get("timeout", 600.0)),
            constraints=spec_dict.get("constraints", {}),
            metadata=spec_dict.get("metadata", {}),
        )