"""Tests for P4 Federated Mission Compiler — Phase 1: core models, DAG validation, and compiler.

Tests cover:
1. Core model construction and validation
2. DAG validation (cycles, dependencies, types, timeouts, runtimes)
3. MissionCompiler full and incremental compilation
4. Edge cases and error messages
"""

from __future__ import annotations

import pytest

from mindroom.compiler import (
    CompilationError,
    CompilationResult,
    CompilationWarning,
    DAGValidationError,
    DAGValidator,
    MissionCompiler,
    MissionDAG,
    MissionSpec,
    MissionStep,
    RetryPolicy,
    RuntimeTarget,
    StepType,
    ValidationResult,
)


# ══════════════════════════════════════════════════════════════════════════
# Model Construction Tests
# ══════════════════════════════════════════════════════════════════════════


class TestRetryPolicy:
    """Tests for RetryPolicy model."""

    def test_default_policy(self) -> None:
        """Default retry policy should have sensible values."""
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.backoff == "exponential"
        assert policy.base_delay_seconds == 5.0
        assert policy.max_delay_seconds == 120.0
        assert policy.backoff_factor == 2.0

    def test_custom_policy(self) -> None:
        """Custom retry policy values should be preserved."""
        policy = RetryPolicy(
            max_attempts=5,
            backoff="fixed",
            base_delay_seconds=10.0,
            max_delay_seconds=60.0,
            backoff_factor=1.0,
        )
        assert policy.max_attempts == 5
        assert policy.backoff == "fixed"

    def test_invalid_max_attempts(self) -> None:
        """max_attempts must be >= 1."""
        with pytest.raises(ValueError, match="max_attempts"):
            RetryPolicy(max_attempts=0)

    def test_invalid_base_delay(self) -> None:
        """base_delay_seconds must be >= 0."""
        with pytest.raises(ValueError, match="base_delay_seconds"):
            RetryPolicy(base_delay_seconds=-1.0)

    def test_max_delay_less_than_base(self) -> None:
        """max_delay_seconds must be >= base_delay_seconds."""
        with pytest.raises(ValueError, match="max_delay_seconds"):
            RetryPolicy(base_delay_seconds=30.0, max_delay_seconds=10.0)

    def test_invalid_backoff_factor(self) -> None:
        """backoff_factor must be > 0."""
        with pytest.raises(ValueError, match="backoff_factor"):
            RetryPolicy(backoff_factor=0.0)

    def test_negative_backoff_factor(self) -> None:
        """backoff_factor must be > 0 (negative)."""
        with pytest.raises(ValueError, match="backoff_factor"):
            RetryPolicy(backoff_factor=-1.0)


class TestMissionStep:
    """Tests for MissionStep model."""

    def test_minimal_step(self) -> None:
        """A step with only an id should have sensible defaults."""
        step = MissionStep(id="step-1")
        assert step.id == "step-1"
        assert step.type == StepType.TASK
        assert step.timeout == 300.0
        assert step.target_runtime == RuntimeTarget.MINDROOM
        assert step.dependencies == ()
        assert step.rollback_step_id is None

    def test_full_step(self) -> None:
        """A fully specified step should preserve all values."""
        step = MissionStep(
            id="scan-hosts",
            type=StepType.TASK,
            description="Scan subnet for active hosts",
            inputs={"subnet": "10.0.1.0/24"},
            outputs={"hosts": "list[str]"},
            dependencies=("discover",),
            timeout=120.0,
            retry=RetryPolicy(max_attempts=2),
            target_runtime=RuntimeTarget.OPENCLAW,
            capabilities=("network_scan",),
            rollback_step_id="restore-snapshots",
            metadata={"zone": "internal"},
        )
        assert step.id == "scan-hosts"
        assert step.type == StepType.TASK
        assert step.inputs["subnet"] == "10.0.1.0/24"
        assert step.outputs["hosts"] == "list[str]"
        assert step.dependencies == ("discover",)
        assert step.timeout == 120.0
        assert step.retry.max_attempts == 2
        assert step.target_runtime == RuntimeTarget.OPENCLAW
        assert step.capabilities == ("network_scan",)
        assert step.rollback_step_id == "restore-snapshots"

    def test_empty_id_raises(self) -> None:
        """Step id must be non-empty."""
        with pytest.raises(ValueError, match="non-empty"):
            MissionStep(id="")

    def test_zero_timeout_raises(self) -> None:
        """Timeout must be > 0."""
        with pytest.raises(ValueError, match="timeout"):
            MissionStep(id="step-1", timeout=0)

    def test_negative_timeout_raises(self) -> None:
        """Timeout must be > 0 (negative)."""
        with pytest.raises(ValueError, match="timeout"):
            MissionStep(id="step-1", timeout=-10.0)

    def test_self_rollback_raises(self) -> None:
        """A step cannot reference itself as its rollback."""
        with pytest.raises(ValueError, match="cannot reference itself"):
            MissionStep(id="step-1", rollback_step_id="step-1")

    def test_decision_step_type(self) -> None:
        """Step type DECISION should be preserved."""
        step = MissionStep(id="decide", type=StepType.DECISION)
        assert step.type == StepType.DECISION

    def test_rollback_step_type(self) -> None:
        """Step type ROLLBACK should be preserved."""
        step = MissionStep(id="undo", type=StepType.ROLLBACK)
        assert step.type == StepType.ROLLBACK

    def test_hermes_runtime(self) -> None:
        """Hermes runtime target should be preserved."""
        step = MissionStep(id="ext-svc", target_runtime=RuntimeTarget.HERMES)
        assert step.target_runtime == RuntimeTarget.HERMES


class TestMissionDAG:
    """Tests for MissionDAG model."""

    def test_empty_dag(self) -> None:
        """An empty DAG should have no steps and no entry points."""
        dag = MissionDAG()
        assert dag.steps == ()
        assert dag.entry_points == ()

    def test_single_step_dag(self) -> None:
        """A DAG with one step should have that step as entry point."""
        step = MissionStep(id="root")
        dag = MissionDAG(steps=(step,))
        assert dag.entry_points == ("root",)
        assert dag.step_ids == frozenset({"root"})

    def test_linear_dag(self) -> None:
        """A linear DAG should have the first step as entry point."""
        step_a = MissionStep(id="a")
        step_b = MissionStep(id="b", dependencies=("a",))
        step_c = MissionStep(id="c", dependencies=("b",))
        dag = MissionDAG(steps=(step_a, step_b, step_c))
        assert dag.entry_points == ("a",)

    def test_diamond_dag(self) -> None:
        """A diamond DAG should have the root as entry point."""
        root = MissionStep(id="root")
        left = MissionStep(id="left", dependencies=("root",))
        right = MissionStep(id="right", dependencies=("root",))
        merge = MissionStep(id="merge", dependencies=("left", "right"))
        dag = MissionDAG(steps=(root, left, right, merge))
        assert dag.entry_points == ("root",)

    def test_multiple_entry_points(self) -> None:
        """DAG with multiple independent roots should detect all."""
        a = MissionStep(id="a")
        b = MissionStep(id="b")
        c = MissionStep(id="c", dependencies=("a", "b"))
        dag = MissionDAG(steps=(a, b, c))
        assert set(dag.entry_points) == {"a", "b"}

    def test_step_map(self) -> None:
        """step_map should provide id-to-step lookup."""
        a = MissionStep(id="a")
        b = MissionStep(id="b", dependencies=("a",))
        dag = MissionDAG(steps=(a, b))
        assert dag.step_map["a"] is a
        assert dag.step_map["b"] is b
        assert dag.get_step("a") is a
        assert dag.get_step("nonexistent") is None

    def test_explicit_entry_points(self) -> None:
        """Explicit entry points should be preserved."""
        a = MissionStep(id="a")
        b = MissionStep(id="b", dependencies=("a",))
        dag = MissionDAG(steps=(a, b), entry_points=("a",))
        assert dag.entry_points == ("a",)


class TestMissionSpec:
    """Tests for MissionSpec model."""

    def test_minimal_spec(self) -> None:
        """A minimal spec should have sensible defaults."""
        spec = MissionSpec(mission_id="msn-001", name="Test Mission")
        assert spec.mission_id == "msn-001"
        assert spec.name == "Test Mission"
        assert spec.version == "1.0"
        assert spec.timeout == 600.0
        assert spec.dag.steps == ()

    def test_full_spec(self) -> None:
        """A fully specified spec should preserve all values."""
        step = MissionStep(id="scan", timeout=120.0)
        dag = MissionDAG(steps=(step,))
        spec = MissionSpec(
            mission_id="msn-002",
            name="Vulnerability Scan",
            version="2.0",
            author="main",
            created="2026-08-04T09:00:00Z",
            dag=dag,
            timeout=300.0,
            constraints={"min_workers": 2},
            metadata={"env": "production"},
        )
        assert spec.mission_id == "msn-002"
        assert spec.name == "Vulnerability Scan"
        assert spec.version == "2.0"
        assert spec.author == "main"
        assert spec.dag.steps == (step,)
        assert spec.timeout == 300.0
        assert spec.constraints["min_workers"] == 2

    def test_empty_mission_id_raises(self) -> None:
        """mission_id must be non-empty."""
        with pytest.raises(ValueError, match="mission_id"):
            MissionSpec(mission_id="", name="Test")

    def test_empty_name_raises(self) -> None:
        """name must be non-empty."""
        with pytest.raises(ValueError, match="name"):
            MissionSpec(mission_id="msn-001", name="")

    def test_zero_timeout_raises(self) -> None:
        """timeout must be > 0."""
        with pytest.raises(ValueError, match="timeout"):
            MissionSpec(mission_id="msn-001", name="Test", timeout=0)


class TestCompilationResult:
    """Tests for CompilationResult model."""

    def test_success_result(self) -> None:
        """A successful result should have success=True and no errors."""
        dag = MissionDAG(steps=(MissionStep(id="a"),))
        result = CompilationResult(success=True, compiled_dag=dag)
        assert result.success
        assert result.compiled_dag is dag
        assert result.errors == ()
        assert result.warnings == ()

    def test_failure_result(self) -> None:
        """A failed result should have success=False and errors."""
        error = CompilationError(
            code="CYCLE_DETECTED",
            message="Cycle detected",
            step_id="a",
        )
        result = CompilationResult(success=False, errors=(error,))
        assert not result.success
        assert result.compiled_dag is None
        assert len(result.errors) == 1
        assert result.errors[0].code == "CYCLE_DETECTED"

    def test_warnings(self) -> None:
        """Warnings should be preserved in the result."""
        warning = CompilationWarning(
            code="SHORT_TIMEOUT",
            message="Short timeout",
            step_id="a",
        )
        result = CompilationResult(success=True, warnings=(warning,))
        assert result.success
        assert len(result.warnings) == 1
        assert result.warnings[0].code == "SHORT_TIMEOUT"

    def test_changed_step_ids(self) -> None:
        """Changed step IDs should be preserved."""
        result = CompilationResult(
            success=True,
            changed_step_ids=frozenset({"a", "b"}),
        )
        assert result.changed_step_ids == frozenset({"a", "b"})


# ══════════════════════════════════════════════════════════════════════════
# DAG Validation Tests
# ══════════════════════════════════════════════════════════════════════════


class TestDAGValidatorCycleDetection:
    """Tests for cycle detection in DAG validation."""

    def test_no_cycle_linear(self) -> None:
        """A linear DAG should pass cycle detection."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
            MissionStep(id="c", dependencies=("b",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid
        assert not result.errors

    def test_no_cycle_diamond(self) -> None:
        """A diamond DAG should pass cycle detection."""
        steps = (
            MissionStep(id="root"),
            MissionStep(id="left", dependencies=("root",)),
            MissionStep(id="right", dependencies=("root",)),
            MissionStep(id="merge", dependencies=("left", "right")),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_direct_cycle(self) -> None:
        """A direct cycle (A → B → A) should be detected."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "CYCLE_DETECTED" for e in result.errors)

    def test_self_cycle(self) -> None:
        """A self-cycle (A → A) should be detected."""
        steps = (
            MissionStep(id="a", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "CYCLE_DETECTED" for e in result.errors)

    def test_indirect_cycle(self) -> None:
        """An indirect cycle (A → B → C → A) should be detected."""
        steps = (
            MissionStep(id="a", dependencies=("c",)),
            MissionStep(id="b", dependencies=("a",)),
            MissionStep(id="c", dependencies=("b",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "CYCLE_DETECTED" for e in result.errors)

    def test_cycle_error_message_is_actionable(self) -> None:
        """Cycle error messages should include the cycle path."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        cycle_error = next(e for e in result.errors if e.code == "CYCLE_DETECTED")
        assert "→" in cycle_error.message  # Should show the cycle path
        assert cycle_error.details  # Should have remediation guidance


class TestDAGValidatorDependencyCompleteness:
    """Tests for dependency completeness checking."""

    def test_all_dependencies_resolve(self) -> None:
        """All dependencies should resolve to existing steps."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_missing_dependency(self) -> None:
        """A missing dependency should be detected."""
        steps = (
            MissionStep(id="a", dependencies=("nonexistent",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "MISSING_DEPENDENCY" for e in result.errors)

    def test_missing_dependency_error_message(self) -> None:
        """Missing dependency error should include remediation."""
        steps = (
            MissionStep(id="a", dependencies=("ghost",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        missing_error = next(e for e in result.errors if e.code == "MISSING_DEPENDENCY")
        assert "ghost" in missing_error.message
        assert missing_error.step_id == "a"
        assert missing_error.details  # Should have remediation


class TestDAGValidatorTimeout:
    """Tests for timeout validation."""

    def test_timeout_within_bounds(self) -> None:
        """Timeouts within maximum should pass."""
        step = MissionStep(id="a", timeout=300.0, target_runtime=RuntimeTarget.MINDROOM)
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_timeout_exceeds_maximum(self) -> None:
        """Timeouts exceeding maximum should fail."""
        step = MissionStep(
            id="a",
            timeout=999999.0,
            target_runtime=RuntimeTarget.MINDROOM,
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "TIMEOUT_EXCEEDS_MAXIMUM" for e in result.errors)

    def test_short_timeout_warning(self) -> None:
        """Very short timeouts should produce a warning."""
        step = MissionStep(id="a", timeout=5.0)
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid  # Still valid, just a warning
        assert any(w.code == "SHORT_TIMEOUT" for w in result.warnings)

    def test_openclaw_timeout_limit(self) -> None:
        """OpenClaw runtime should have a 30-minute timeout limit."""
        step = MissionStep(
            id="a",
            timeout=2000.0,  # > 1800s
            target_runtime=RuntimeTarget.OPENCLAW,
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "TIMEOUT_EXCEEDS_MAXIMUM" for e in result.errors)

    def test_hermes_timeout_limit(self) -> None:
        """Hermes runtime should have a 15-minute timeout limit."""
        step = MissionStep(
            id="a",
            timeout=1000.0,  # > 900s
            target_runtime=RuntimeTarget.HERMES,
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "TIMEOUT_EXCEEDS_MAXIMUM" for e in result.errors)


class TestDAGValidatorRetryPolicy:
    """Tests for retry policy validation."""

    def test_valid_retry_policy(self) -> None:
        """A valid retry policy should pass."""
        step = MissionStep(
            id="a",
            retry=RetryPolicy(max_attempts=3),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_retry_policy_overlap_warning(self) -> None:
        """Overlapping retry_on and no_retry_on should produce a warning."""
        step = MissionStep(
            id="a",
            retry=RetryPolicy(
                retry_on=("timeout", "worker_error"),
                no_retry_on=("timeout",),
            ),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid  # Still valid
        assert any(w.code == "RETRY_POLICY_OVERLAP" for w in result.warnings)

    def test_high_retry_count_warning(self) -> None:
        """A high retry count should produce a warning."""
        step = MissionStep(
            id="a",
            retry=RetryPolicy(max_attempts=15),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=(step,)),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid
        assert any(w.code == "HIGH_RETRY_COUNT" for w in result.warnings)


class TestDAGValidatorRuntimeCompatibility:
    """Tests for cross-runtime compatibility checking."""

    def test_same_runtime_compatible(self) -> None:
        """Steps on the same runtime should always be compatible."""
        steps = (
            MissionStep(id="a", target_runtime=RuntimeTarget.OPENCLAW),
            MissionStep(id="b", dependencies=("a",), target_runtime=RuntimeTarget.OPENCLAW),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_mindroom_to_openclaw_compatible(self) -> None:
        """MindRoom → OpenClaw should be compatible."""
        steps = (
            MissionStep(id="a", target_runtime=RuntimeTarget.MINDROOM),
            MissionStep(id="b", dependencies=("a",), target_runtime=RuntimeTarget.OPENCLAW),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_mindroom_to_hermes_compatible(self) -> None:
        """MindRoom → Hermes should be compatible."""
        steps = (
            MissionStep(id="a", target_runtime=RuntimeTarget.MINDROOM),
            MissionStep(id="b", dependencies=("a",), target_runtime=RuntimeTarget.HERMES),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_openclaw_to_hermes_incompatible(self) -> None:
        """OpenClaw → Hermes should be incompatible."""
        steps = (
            MissionStep(id="a", target_runtime=RuntimeTarget.OPENCLAW),
            MissionStep(id="b", dependencies=("a",), target_runtime=RuntimeTarget.HERMES),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "INCOMPATIBLE_RUNTIME" for e in result.errors)

    def test_hermes_to_openclaw_incompatible(self) -> None:
        """Hermes → OpenClaw should be incompatible."""
        steps = (
            MissionStep(id="a", target_runtime=RuntimeTarget.HERMES),
            MissionStep(id="b", dependencies=("a",), target_runtime=RuntimeTarget.OPENCLAW),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "INCOMPATIBLE_RUNTIME" for e in result.errors)

    def test_check_runtime_compatibility_class_method(self) -> None:
        """The class method should correctly report compatibility."""
        assert DAGValidator.check_runtime_compatibility(
            RuntimeTarget.MINDROOM, RuntimeTarget.OPENCLAW,
        )
        assert DAGValidator.check_runtime_compatibility(
            RuntimeTarget.MINDROOM, RuntimeTarget.HERMES,
        )
        assert not DAGValidator.check_runtime_compatibility(
            RuntimeTarget.OPENCLAW, RuntimeTarget.HERMES,
        )
        assert not DAGValidator.check_runtime_compatibility(
            RuntimeTarget.HERMES, RuntimeTarget.OPENCLAW,
        )


class TestDAGValidatorRollbackReferences:
    """Tests for rollback/saga pattern validation."""

    def test_valid_rollback_reference(self) -> None:
        """A valid rollback reference should pass."""
        steps = (
            MissionStep(id="apply-patches", rollback_step_id="restore-snapshots"),
            MissionStep(id="restore-snapshots", type=StepType.ROLLBACK),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_missing_rollback_step(self) -> None:
        """A missing rollback step should be detected."""
        steps = (
            MissionStep(id="apply-patches", rollback_step_id="nonexistent"),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "MISSING_ROLLBACK_STEP" for e in result.errors)

    def test_rollback_step_not_rollback_type_warning(self) -> None:
        """A rollback reference to a non-rollback type should produce a warning."""
        steps = (
            MissionStep(id="apply-patches", rollback_step_id="restore-snapshots"),
            MissionStep(id="restore-snapshots", type=StepType.TASK),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid  # Still valid
        assert any(w.code == "ROLLBACK_STEP_NOT_ROLLBACK_TYPE" for w in result.warnings)

    def test_rollback_depends_on_target_warning(self) -> None:
        """A rollback depending on its target should produce a warning."""
        steps = (
            MissionStep(id="apply-patches", rollback_step_id="restore-snapshots"),
            MissionStep(
                id="restore-snapshots",
                type=StepType.ROLLBACK,
                dependencies=("apply-patches",),
            ),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid
        assert any(w.code == "ROLLBACK_DEPENDS_ON_TARGET" for w in result.warnings)


class TestDAGValidatorEntryPoints:
    """Tests for entry point validation."""

    def test_empty_dag_error(self) -> None:
        """An empty DAG should produce an error."""
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "EMPTY_DAG" for e in result.errors)

    def test_no_entry_points_error(self) -> None:
        """A DAG with no entry points should produce an error."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        # Should have both CYCLE_DETECTED and NO_ENTRY_POINTS
        assert any(e.code == "NO_ENTRY_POINTS" for e in result.errors)


class TestDAGValidatorInputOutputTypes:
    """Tests for input/output type checking."""

    def test_valid_input_ref(self) -> None:
        """A valid input reference should pass."""
        steps = (
            MissionStep(id="scan", outputs={"hosts": "list[str]"}),
            MissionStep(
                id="process",
                inputs={"targets": "scan.hosts"},
                dependencies=("scan",),
            ),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid

    def test_input_ref_step_not_found(self) -> None:
        """An input reference to a non-existent step should be detected."""
        steps = (
            MissionStep(
                id="process",
                inputs={"targets": "nonexistent.hosts"},
            ),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "INPUT_REF_STEP_NOT_FOUND" for e in result.errors)

    def test_input_ref_output_not_found(self) -> None:
        """An input reference to a non-existent output should be detected."""
        steps = (
            MissionStep(id="scan", outputs={"hosts": "list[str]"}),
            MissionStep(
                id="process",
                inputs={"targets": "scan.nonexistent"},
                dependencies=("scan",),
            ),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "INPUT_REF_OUTPUT_NOT_FOUND" for e in result.errors)

    def test_duplicate_step_ids(self) -> None:
        """Duplicate step IDs should be detected."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="a"),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert any(e.code == "DUPLICATE_STEP_ID" for e in result.errors)


class TestDAGValidatorTopologicalSort:
    """Tests for topological sorting."""

    def test_linear_order(self) -> None:
        """A linear DAG should be sorted correctly."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
            MissionStep(id="c", dependencies=("b",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid
        assert result.topological_order == ("a", "b", "c")

    def test_diamond_order(self) -> None:
        """A diamond DAG should be sorted correctly."""
        steps = (
            MissionStep(id="root"),
            MissionStep(id="left", dependencies=("root",)),
            MissionStep(id="right", dependencies=("root",)),
            MissionStep(id="merge", dependencies=("left", "right")),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert result.valid
        # root must come first, merge must come last
        assert result.topological_order[0] == "root"
        assert result.topological_order[-1] == "merge"
        # left and right must come before merge
        merge_idx = result.topological_order.index("merge")
        left_idx = result.topological_order.index("left")
        right_idx = result.topological_order.index("right")
        assert left_idx < merge_idx
        assert right_idx < merge_idx

    def test_cyclic_dag_no_topological_order(self) -> None:
        """A cyclic DAG should not produce a topological order."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test",
            dag=MissionDAG(steps=steps),
        )
        validator = DAGValidator(spec)
        result = validator.validate()
        assert not result.valid
        assert result.topological_order == ()


# ══════════════════════════════════════════════════════════════════════════
# MissionCompiler Tests
# ══════════════════════════════════════════════════════════════════════════


class TestMissionCompiler:
    """Tests for the MissionCompiler class."""

    def test_compile_valid_spec(self) -> None:
        """A valid spec should compile successfully."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test Mission",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert result.success
        assert result.compiled_dag is not None
        assert len(result.compiled_dag.steps) == 2
        assert result.errors == ()

    def test_compile_invalid_spec(self) -> None:
        """An invalid spec should fail compilation."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test Mission",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert not result.success
        assert result.compiled_dag is None
        assert len(result.errors) > 0

    def test_compile_empty_spec(self) -> None:
        """An empty spec should fail with EMPTY_DAG."""
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test Mission",
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert not result.success
        assert any(e.code == "EMPTY_DAG" for e in result.errors)

    def test_compile_from_spec_dict(self) -> None:
        """Compilation from a dictionary should work."""
        spec_dict = {
            "mission_id": "msn-dict",
            "name": "Dict Mission",
            "dag": {
                "steps": [
                    {"id": "a"},
                    {"id": "b", "dependencies": ["a"]},
                ],
            },
        }
        compiler = MissionCompiler()
        result = compiler.compile_from_spec_dict(spec_dict)
        assert result.success
        assert result.compiled_dag is not None

    def test_compile_from_spec_dict_with_retry(self) -> None:
        """Compilation from a dict with retry policy should work."""
        spec_dict = {
            "mission_id": "msn-dict-retry",
            "name": "Dict Mission Retry",
            "dag": {
                "steps": [
                    {
                        "id": "a",
                        "retry": {
                            "max_attempts": 5,
                            "backoff": "exponential",
                        },
                    },
                ],
            },
        }
        compiler = MissionCompiler()
        result = compiler.compile_from_spec_dict(spec_dict)
        assert result.success
        assert result.compiled_dag is not None
        step_a = result.compiled_dag.get_step("a")
        assert step_a is not None
        assert step_a.retry.max_attempts == 5

    def test_compile_from_spec_dict_with_runtime(self) -> None:
        """Compilation from a dict with runtime target should work."""
        spec_dict = {
            "mission_id": "msn-dict-rt",
            "name": "Dict Mission Runtime",
            "dag": {
                "steps": [
                    {
                        "id": "a",
                        "target_runtime": "openclaw",
                        "capabilities": ["network_scan"],
                    },
                ],
            },
        }
        compiler = MissionCompiler()
        result = compiler.compile_from_spec_dict(spec_dict)
        assert result.success
        step_a = result.compiled_dag.get_step("a")
        assert step_a is not None
        assert step_a.target_runtime == RuntimeTarget.OPENCLAW
        assert step_a.capabilities == ("network_scan",)

    def test_compile_with_warnings(self) -> None:
        """Compilation with warnings should still succeed."""
        steps = (
            MissionStep(id="a", timeout=5.0),  # Short timeout warning
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test Mission",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert result.success
        assert len(result.warnings) > 0

    def test_compiled_dag_is_topologically_sorted(self) -> None:
        """The compiled DAG should be topologically sorted."""
        steps = (
            MissionStep(id="c", dependencies=("a",)),
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-test",
            name="Test Mission",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert result.success
        assert result.compiled_dag is not None
        # a must come before b and c
        step_ids = [s.id for s in result.compiled_dag.steps]
        assert step_ids.index("a") < step_ids.index("b")
        assert step_ids.index("a") < step_ids.index("c")


class TestMissionCompilerIncremental:
    """Tests for incremental compilation support."""

    def test_incremental_unchanged(self) -> None:
        """Re-compiling an unchanged spec should detect no changes."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-incr",
            name="Incremental Test",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()

        # First compilation
        result1 = compiler.compile(spec)
        assert result1.success
        assert result1.changed_step_ids == frozenset({"a", "b"})

        # Second compilation (no changes)
        result2 = compiler.compile(spec)
        assert result2.success
        # Changed step IDs should be empty since nothing changed
        assert result2.changed_step_ids == frozenset()

    def test_incremental_with_changes(self) -> None:
        """Re-compiling with a changed step should detect the change."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-incr2",
            name="Incremental Test 2",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()

        # First compilation
        result1 = compiler.compile(spec)
        assert result1.success

        # Modify step b
        modified_steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",), timeout=600.0),
        )
        modified_spec = MissionSpec(
            mission_id="msn-incr2",
            name="Incremental Test 2",
            dag=MissionDAG(steps=modified_steps),
        )

        # Second compilation
        result2 = compiler.compile(modified_spec)
        assert result2.success
        assert "b" in result2.changed_step_ids
        assert "a" not in result2.changed_step_ids

    def test_incremental_with_explicit_changed_ids(self) -> None:
        """Explicit changed_step_ids should be respected."""
        steps = (
            MissionStep(id="a"),
            MissionStep(id="b", dependencies=("a",)),
        )
        spec = MissionSpec(
            mission_id="msn-incr3",
            name="Incremental Test 3",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()

        result = compiler.compile(
            spec,
            changed_step_ids=frozenset({"b"}),
        )
        assert result.success
        assert result.changed_step_ids == frozenset({"b"})

    def test_incremental_cache_clear(self) -> None:
        """Clearing the cache should force full re-compilation."""
        steps = (
            MissionStep(id="a"),
        )
        spec = MissionSpec(
            mission_id="msn-cache",
            name="Cache Test",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()

        compiler.compile(spec)
        assert compiler.get_cache("msn-cache") is not None

        compiler.clear_cache("msn-cache")
        assert compiler.get_cache("msn-cache") is None

    def test_incremental_clear_all_cache(self) -> None:
        """Clearing all caches should work."""
        spec1 = MissionSpec(
            mission_id="msn-a",
            name="Mission A",
            dag=MissionDAG(steps=(MissionStep(id="a"),)),
        )
        spec2 = MissionSpec(
            mission_id="msn-b",
            name="Mission B",
            dag=MissionDAG(steps=(MissionStep(id="b"),)),
        )
        compiler = MissionCompiler()
        compiler.compile(spec1)
        compiler.compile(spec2)

        compiler.clear_cache()
        assert compiler.get_cache("msn-a") is None
        assert compiler.get_cache("msn-b") is None

    def test_incremental_error_preserves_cache(self) -> None:
        """A failed compilation should still update the cache."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),
            MissionStep(id="b", dependencies=("a",)),  # Cycle
        )
        spec = MissionSpec(
            mission_id="msn-err",
            name="Error Test",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()

        result = compiler.compile(spec)
        assert not result.success
        # Cache should still be updated
        cache = compiler.get_cache("msn-err")
        assert cache is not None
        assert cache.last_result is not None
        assert not cache.last_result.success


class TestMissionCompilerIntegration:
    """Integration tests combining multiple validation checks."""

    def test_complex_mission(self) -> None:
        """A complex, realistic mission should compile successfully."""
        steps = (
            MissionStep(
                id="discover-hosts",
                description="Scan subnet for active hosts",
                capabilities=("network_scan",),
                timeout=120.0,
                retry=RetryPolicy(max_attempts=2),
                target_runtime=RuntimeTarget.OPENCLAW,
                outputs={"hosts": "list[str]"},
            ),
            MissionStep(
                id="enumerate-services",
                description="Identify running services",
                capabilities=("network_scan", "service_discovery"),
                timeout=180.0,
                retry=RetryPolicy(max_attempts=1),
                dependencies=("discover-hosts",),
                inputs={"hosts": "discover-hosts.hosts"},
                target_runtime=RuntimeTarget.OPENCLAW,
                outputs={"services": "dict[str, list[str]]"},
            ),
            MissionStep(
                id="check-vulnerabilities",
                description="Cross-reference services against CVE database",
                capabilities=("vulnerability_db",),
                timeout=300.0,
                retry=RetryPolicy(max_attempts=1),  # No retries (1 = first attempt only)
                dependencies=("enumerate-services",),
                target_runtime=RuntimeTarget.MINDROOM,
                outputs={"vulnerabilities": "list[dict]"},
            ),
            MissionStep(
                id="apply-patches",
                description="Apply critical patches",
                capabilities=("package_manager", "exec"),
                timeout=600.0,
                retry=RetryPolicy(max_attempts=1),
                dependencies=("check-vulnerabilities",),
                target_runtime=RuntimeTarget.OPENCLAW,
                rollback_step_id="restore-snapshots",
                outputs={"patches_applied": "list[str]"},
            ),
            MissionStep(
                id="restore-snapshots",
                type=StepType.ROLLBACK,
                description="Restore snapshots on failure",
                capabilities=("file_system",),
                timeout=300.0,
                target_runtime=RuntimeTarget.OPENCLAW,
            ),
        )
        spec = MissionSpec(
            mission_id="msn-vuln-scan",
            name="Vulnerability Scan & Patch Cycle",
            version="1.0",
            author="main",
            timeout=600.0,
            constraints={"min_workers": 2, "max_workers": 5},
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert result.success, f"Compilation failed: {result.errors}"
        assert result.compiled_dag is not None
        assert len(result.compiled_dag.steps) == 5
        # Verify topological order
        step_ids = [s.id for s in result.compiled_dag.steps]
        assert step_ids.index("discover-hosts") < step_ids.index("enumerate-services")
        assert step_ids.index("enumerate-services") < step_ids.index("check-vulnerabilities")
        assert step_ids.index("check-vulnerabilities") < step_ids.index("apply-patches")

    def test_mission_with_all_errors(self) -> None:
        """A mission with multiple errors should report all of them."""
        steps = (
            MissionStep(id="a", dependencies=("b",)),  # Missing dep
            MissionStep(id="b", dependencies=("a",)),  # Cycle with a
            MissionStep(id="a"),  # Duplicate ID (overwrites the first "a")
            MissionStep(
                id="c",
                dependencies=("a",),
                timeout=999999.0,  # Exceeds max
                target_runtime=RuntimeTarget.HERMES,
                rollback_step_id="ghost",  # Missing rollback
            ),
        )
        spec = MissionSpec(
            mission_id="msn-all-errors",
            name="All Errors",
            dag=MissionDAG(steps=steps),
        )
        compiler = MissionCompiler()
        result = compiler.compile(spec)
        assert not result.success
        # Should have multiple distinct errors.
        # Note: with duplicate IDs, the step_map only keeps the last "a"
        # (which has no dependencies), so the a↔b cycle may not be detected.
        # The duplicate ID is caught first.
        error_codes = {e.code for e in result.errors}
        assert "DUPLICATE_STEP_ID" in error_codes
        assert "TIMEOUT_EXCEEDS_MAXIMUM" in error_codes
        assert "MISSING_ROLLBACK_STEP" in error_codes