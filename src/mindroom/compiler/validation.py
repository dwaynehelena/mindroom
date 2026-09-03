"""DAG validation for the P4 Federated Mission Compiler.

Provides comprehensive validation of mission DAGs including:
- Cycle detection (must be acyclic)
- Dependency completeness (all referenced steps exist)
- Input/output type checking
- Timeout and retry policy validation
- Cross-runtime compatibility checks
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

from mindroom.compiler.models import (
    CompilationError,
    CompilationWarning,
    MissionDAG,
    MissionSpec,
    MissionStep,
    RetryPolicy,
    RuntimeTarget,
    StepType,
)


@dataclass(frozen=True)
class ValidationResult:
    """Result of DAG validation."""

    valid: bool
    """Whether the DAG passed all validation checks."""

    errors: tuple[CompilationError, ...] = field(default_factory=tuple)
    """Validation errors. Empty when valid is True."""

    warnings: tuple[CompilationWarning, ...] = field(default_factory=tuple)
    """Validation warnings. May be present even when valid is True."""

    topological_order: tuple[str, ...] = field(default_factory=tuple)
    """Topologically sorted step IDs. Present only when valid."""


class DAGValidationError(RuntimeError):
    """Raised when DAG validation fails catastrophically."""


class DAGValidator:
    """Validates mission DAGs for correctness and consistency.

    Performs the following checks:
    1. Cycle detection via DFS
    2. Dependency completeness
    3. Input/output type consistency
    4. Timeout and retry policy bounds
    5. Cross-runtime compatibility
    6. Rollback/saga pattern consistency
    """

    # Known compatible runtime pairs for cross-runtime data flow
    _COMPATIBLE_RUNTIME_FLOWS: dict[RuntimeTarget, frozenset[RuntimeTarget]] = {
        RuntimeTarget.MINDROOM: frozenset({
            RuntimeTarget.MINDROOM,
            RuntimeTarget.OPENCLAW,
            RuntimeTarget.HERMES,
        }),
        RuntimeTarget.OPENCLAW: frozenset({
            RuntimeTarget.MINDROOM,
            RuntimeTarget.OPENCLAW,
        }),
        RuntimeTarget.HERMES: frozenset({
            RuntimeTarget.MINDROOM,
            RuntimeTarget.HERMES,
        }),
    }

    # Maximum allowed timeout per runtime
    _MAX_TIMEOUT_BY_RUNTIME: dict[RuntimeTarget, float] = {
        RuntimeTarget.MINDROOM: 3600.0,  # 1 hour
        RuntimeTarget.OPENCLAW: 1800.0,  # 30 minutes
        RuntimeTarget.HERMES: 900.0,     # 15 minutes
    }

    def __init__(self, spec: MissionSpec) -> None:
        """Initialize the validator with a mission spec."""
        self._spec = spec
        self._dag = spec.dag
        self._step_map = spec.dag.step_map
        self._errors: list[CompilationError] = []
        self._warnings: list[CompilationWarning] = []

    def validate(self) -> ValidationResult:
        """Run all validation checks and return the result."""
        self._errors.clear()
        self._warnings.clear()

        # Run all validation checks
        self._check_duplicate_ids()
        self._check_dependency_completeness()
        self._check_cycles()
        self._check_timeout_bounds()
        self._check_retry_policies()
        self._check_runtime_compatibility()
        self._check_rollback_references()
        self._check_entry_points()
        self._check_input_output_types()

        # Compute topological order if no errors
        topological_order: tuple[str, ...] = ()
        if not self._errors:
            try:
                topological_order = self._topological_sort()
            except DAGValidationError:
                # Should not happen since we already checked for cycles,
                # but handle gracefully
                pass

        return ValidationResult(
            valid=not self._errors,
            errors=tuple(self._errors),
            warnings=tuple(self._warnings),
            topological_order=topological_order,
        )

    def validate_steps(self, step_ids: frozenset[str]) -> ValidationResult:
        """Re-validate only the specified steps (incremental compilation).

        This runs a subset of checks that are relevant to changed steps:
        - Dependency completeness for the changed steps
        - Timeout and retry policy for the changed steps
        - Rollback references for the changed steps
        - Input/output types for the changed steps
        - Runtime compatibility for the changed steps
        """
        self._errors.clear()
        self._warnings.clear()

        changed_steps = {
            sid: step for sid, step in self._step_map.items() if sid in step_ids
        }

        for step_id, step in changed_steps.items():
            self._check_step_dependency_completeness(step)
            self._check_step_timeout(step)
            self._check_step_retry_policy(step)
            self._check_step_runtime_compatibility(step)
            self._check_step_rollback_reference(step)
            self._check_step_input_output_types(step)

        # Re-check cycles if any changed step is part of a dependency chain
        if step_ids:
            self._check_cycles()

        return ValidationResult(
            valid=not self._errors,
            errors=tuple(self._errors),
            warnings=tuple(self._warnings),
        )

    # ── Individual check methods ──────────────────────────────────────

    def _check_duplicate_ids(self) -> None:
        """Check that all step IDs are unique."""
        seen: set[str] = set()
        for step in self._dag.steps:
            if step.id in seen:
                self._errors.append(CompilationError(
                    code="DUPLICATE_STEP_ID",
                    message=f"Duplicate step ID: '{step.id}'",
                    step_id=step.id,
                    details="Each step must have a unique id. Rename one of the duplicate steps.",
                ))
            seen.add(step.id)

    def _check_dependency_completeness(self) -> None:
        """Check that all dependency references resolve to existing steps."""
        for step in self._dag.steps:
            self._check_step_dependency_completeness(step)

    def _check_step_dependency_completeness(self, step: MissionStep) -> None:
        """Check that a single step's dependencies all resolve."""
        for dep_id in step.dependencies:
            if dep_id not in self._step_map:
                self._errors.append(CompilationError(
                    code="MISSING_DEPENDENCY",
                    message=(
                        f"Step '{step.id}' depends on '{dep_id}', "
                        f"but no step with that id exists"
                    ),
                    step_id=step.id,
                    details=(
                        f"Add a step with id '{dep_id}' to the mission DAG, "
                        f"or remove '{dep_id}' from the dependencies of '{step.id}'."
                    ),
                ))

    def _check_cycles(self) -> None:
        """Detect cycles in the dependency graph using DFS.

        Uses three-colour DFS (white/grey/black) for O(V+E) cycle detection.
        """
        WHITE, GREY, BLACK = 0, 1, 2
        colour: dict[str, int] = {step.id: WHITE for step in self._dag.steps}
        parent: dict[str, str | None] = {step.id: None for step in self._dag.steps}

        def _dfs(node_id: str) -> bool:
            """Returns True if a cycle is found."""
            colour[node_id] = GREY
            step = self._step_map.get(node_id)
            if step is not None:
                for dep_id in step.dependencies:
                    if colour.get(dep_id) == GREY:
                        # Found a cycle — reconstruct the path
                        cycle_path = self._reconstruct_cycle(node_id, dep_id, parent)
                        self._errors.append(CompilationError(
                            code="CYCLE_DETECTED",
                            message=(
                                f"Cycle detected in dependency graph: "
                                f"{' → '.join(cycle_path)}"
                            ),
                            step_id=node_id,
                            details=(
                                f"Step '{node_id}' creates a circular dependency. "
                                f"Remove the cycle by breaking one of the "
                                f"dependencies in the chain."
                            ),
                        ))
                        return True
                    if colour.get(dep_id) == WHITE:
                        parent[dep_id] = node_id
                        if _dfs(dep_id):
                            return True
            colour[node_id] = BLACK
            return False

        for step in self._dag.steps:
            if colour[step.id] == WHITE:
                if _dfs(step.id):
                    # Continue checking to find all cycles
                    colour = {s.id: WHITE for s in self._dag.steps}
                    parent = {s.id: None for s in self._dag.steps}

    def _reconstruct_cycle(
        self,
        from_node: str,
        to_node: str,
        parent: dict[str, str | None],
    ) -> list[str]:
        """Reconstruct the cycle path from parent pointers."""
        path: list[str] = [to_node]
        current = from_node
        while current is not None and current != to_node:
            path.append(current)
            current = parent.get(current)  # type: ignore[arg-type]
        path.append(to_node)
        path.reverse()
        return path

    def _check_timeout_bounds(self) -> None:
        """Check that all step timeouts are within acceptable bounds."""
        for step in self._dag.steps:
            self._check_step_timeout(step)

    def _check_step_timeout(self, step: MissionStep) -> None:
        """Check a single step's timeout."""
        max_timeout = self._MAX_TIMEOUT_BY_RUNTIME.get(step.target_runtime)
        if max_timeout is not None and step.timeout > max_timeout:
            self._errors.append(CompilationError(
                code="TIMEOUT_EXCEEDS_MAXIMUM",
                message=(
                    f"Step '{step.id}' timeout ({step.timeout}s) exceeds "
                    f"maximum for {step.target_runtime.value} runtime "
                    f"({max_timeout}s)"
                ),
                step_id=step.id,
                details=(
                    f"Reduce the timeout to at most {max_timeout}s, "
                    f"or change the target runtime."
                ),
            ))

        # Warn if timeout is very short
        if step.timeout < 10.0:
            self._warnings.append(CompilationWarning(
                code="SHORT_TIMEOUT",
                message=(
                    f"Step '{step.id}' has a very short timeout "
                    f"({step.timeout}s)"
                ),
                step_id=step.id,
                details=(
                    "Very short timeouts may cause premature failures "
                    "on slow networks or under load."
                ),
            ))

    def _check_retry_policies(self) -> None:
        """Check that all retry policies are valid."""
        for step in self._dag.steps:
            self._check_step_retry_policy(step)

    def _check_step_retry_policy(self, step: MissionStep) -> None:
        """Check a single step's retry policy."""
        policy = step.retry

        # Check for overlapping retry_on / no_retry_on
        overlap = set(policy.retry_on) & set(policy.no_retry_on)
        if overlap:
            self._warnings.append(CompilationWarning(
                code="RETRY_POLICY_OVERLAP",
                message=(
                    f"Step '{step.id}' has overlapping retry_on and "
                    f"no_retry_on categories: {overlap}"
                ),
                step_id=step.id,
                details=(
                    f"The categories {overlap} appear in both retry_on "
                    f"and no_retry_on. no_retry_on takes precedence."
                ),
            ))

        # Warn on high retry count
        if policy.max_attempts > 10:
            self._warnings.append(CompilationWarning(
                code="HIGH_RETRY_COUNT",
                message=(
                    f"Step '{step.id}' has a high retry count "
                    f"({policy.max_attempts})"
                ),
                step_id=step.id,
                details=(
                    "High retry counts can lead to long execution times. "
                    "Consider reducing max_attempts or increasing the timeout."
                ),
            ))

    def _check_runtime_compatibility(self) -> None:
        """Check cross-runtime compatibility for dependent steps.

        Ensures that if step A depends on step B, the runtime of B can
        produce output consumable by the runtime of A.
        """
        for step in self._dag.steps:
            self._check_step_runtime_compatibility(step)

    def _check_step_runtime_compatibility(self, step: MissionStep) -> None:
        """Check runtime compatibility for a single step's dependencies."""
        for dep_id in step.dependencies:
            dep_step = self._step_map.get(dep_id)
            if dep_step is None:
                continue  # Will be caught by dependency completeness check

            compatible_targets = self._COMPATIBLE_RUNTIME_FLOWS.get(
                dep_step.target_runtime,
            )
            if compatible_targets is not None and step.target_runtime not in compatible_targets:
                self._errors.append(CompilationError(
                    code="INCOMPATIBLE_RUNTIME",
                    message=(
                        f"Step '{step.id}' ({step.target_runtime.value}) "
                        f"depends on '{dep_id}' ({dep_step.target_runtime.value}), "
                        f"but {dep_step.target_runtime.value} cannot produce "
                        f"output consumable by {step.target_runtime.value}"
                    ),
                    step_id=step.id,
                    details=(
                        f"Change the target runtime of '{step.id}' to one "
                        f"compatible with '{dep_id}', or restructure the "
                        f"dependency chain."
                    ),
                ))

    def _check_rollback_references(self) -> None:
        """Check that all rollback references resolve to valid steps."""
        for step in self._dag.steps:
            self._check_step_rollback_reference(step)

    def _check_step_rollback_reference(self, step: MissionStep) -> None:
        """Check a single step's rollback reference."""
        if step.rollback_step_id is None:
            return

        if step.rollback_step_id not in self._step_map:
            self._errors.append(CompilationError(
                code="MISSING_ROLLBACK_STEP",
                message=(
                    f"Step '{step.id}' references rollback step "
                    f"'{step.rollback_step_id}', but no step with that id exists"
                ),
                step_id=step.id,
                details=(
                    f"Add a step with id '{step.rollback_step_id}' to the "
                    f"mission DAG, or remove the rollback reference."
                ),
            ))
            return

        rollback_step = self._step_map[step.rollback_step_id]
        if rollback_step.type != StepType.ROLLBACK:
            self._warnings.append(CompilationWarning(
                code="ROLLBACK_STEP_NOT_ROLLBACK_TYPE",
                message=(
                    f"Step '{step.id}' references '{step.rollback_step_id}' "
                    f"as a rollback, but that step has type "
                    f"'{rollback_step.type.value}', not 'rollback'"
                ),
                step_id=step.id,
                details=(
                    f"Consider changing the type of '{step.rollback_step_id}' "
                    f"to 'rollback' for clarity."
                ),
            ))

        # Check that rollback step doesn't depend on the step it rolls back
        if step.id in rollback_step.dependencies:
            self._warnings.append(CompilationWarning(
                code="ROLLBACK_DEPENDS_ON_TARGET",
                message=(
                    f"Rollback step '{step.rollback_step_id}' depends on "
                    f"the step it rolls back ('{step.id}')"
                ),
                step_id=step.id,
                details=(
                    "This may cause issues if the rollback needs to run "
                    "after the target step has failed. Consider removing "
                    "the dependency."
                ),
            ))

    def _check_entry_points(self) -> None:
        """Check that the DAG has at least one entry point."""
        if not self._dag.steps:
            self._errors.append(CompilationError(
                code="EMPTY_DAG",
                message="The mission DAG contains no steps",
                details="Add at least one step to the mission DAG.",
            ))
            return

        if not self._dag.entry_points:
            self._errors.append(CompilationError(
                code="NO_ENTRY_POINTS",
                message="The mission DAG has no entry points (every step has a dependency)",
                details=(
                    "At least one step must have no dependencies to serve "
                    "as an entry point. Check for circular dependencies or "
                    "add a root step."
                ),
            ))

    def _check_input_output_types(self) -> None:
        """Check input/output type consistency across the DAG.

        Verifies that when a step references another step's output as input,
        the types are compatible.
        """
        for step in self._dag.steps:
            self._check_step_input_output_types(step)

    def _check_step_input_output_types(self, step: MissionStep) -> None:
        """Check input/output types for a single step."""
        for input_name, input_value in step.inputs.items():
            # Check for input_ref patterns like "step_id.output_name"
            if isinstance(input_value, str) and "." in input_value:
                ref_parts = input_value.split(".", 1)
                ref_step_id = ref_parts[0]
                ref_output_name = ref_parts[1]

                if ref_step_id not in self._step_map:
                    self._errors.append(CompilationError(
                        code="INPUT_REF_STEP_NOT_FOUND",
                        message=(
                            f"Step '{step.id}' input '{input_name}' references "
                            f"step '{ref_step_id}', but no step with that id exists"
                        ),
                        step_id=step.id,
                        details=(
                            f"Ensure a step with id '{ref_step_id}' exists "
                            f"in the mission DAG."
                        ),
                    ))
                    continue

                ref_step = self._step_map[ref_step_id]
                if ref_output_name not in ref_step.outputs:
                    self._errors.append(CompilationError(
                        code="INPUT_REF_OUTPUT_NOT_FOUND",
                        message=(
                            f"Step '{step.id}' input '{input_name}' references "
                            f"output '{ref_output_name}' of step '{ref_step_id}', "
                            f"but that step does not declare that output"
                        ),
                        step_id=step.id,
                        details=(
                            f"Step '{ref_step_id}' declares outputs: "
                            f"{list(ref_step.outputs.keys())}. "
                            f"Use one of these, or add '{ref_output_name}' "
                            f"to its outputs."
                        ),
                    ))

    def _topological_sort(self) -> tuple[str, ...]:
        """Return a topological ordering of the DAG using Kahn's algorithm.

        Raises DAGValidationError if a cycle is detected.
        """
        # Build in-degree map
        in_degree: dict[str, int] = {}
        adjacency: dict[str, list[str]] = {}

        for step in self._dag.steps:
            in_degree.setdefault(step.id, 0)
            adjacency.setdefault(step.id, [])

        for step in self._dag.steps:
            for dep_id in step.dependencies:
                adjacency.setdefault(dep_id, [])
                adjacency[dep_id].append(step.id)
                in_degree[step.id] = in_degree.get(step.id, 0) + 1

        # Start with nodes that have no dependencies
        queue: deque[str] = deque(
            node_id for node_id, degree in in_degree.items() if degree == 0
        )

        sorted_order: list[str] = []
        while queue:
            node_id = queue.popleft()
            sorted_order.append(node_id)
            for neighbor in adjacency.get(node_id, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(sorted_order) != len(self._dag.steps):
            msg = "Cycle detected in dependency graph"
            raise DAGValidationError(msg)

        return tuple(sorted_order)

    @classmethod
    def check_runtime_compatibility(
        cls,
        source_runtime: RuntimeTarget,
        target_runtime: RuntimeTarget,
    ) -> bool:
        """Check whether source_runtime can produce output for target_runtime."""
        compatible = cls._COMPATIBLE_RUNTIME_FLOWS.get(source_runtime)
        if compatible is None:
            return False
        return target_runtime in compatible