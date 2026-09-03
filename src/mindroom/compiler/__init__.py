"""P4 Federated Mission Compiler: core compiler models, DAG validation, and compilation pipeline.

Phase 1 provides the foundational compiler infrastructure:
- Core data models (MissionDAG, MissionStep, MissionSpec, CompilationResult)
- DAG validation (cycle detection, dependency completeness, type checking, runtime compatibility)
- MissionCompiler class with incremental compilation support
"""

from __future__ import annotations

from mindroom.compiler.compiler import MissionCompiler
from mindroom.compiler.models import (
    CompilationError,
    CompilationResult,
    CompilationWarning,
    MissionDAG,
    MissionSpec,
    MissionStep,
    RetryPolicy,
    RuntimeTarget,
    StepType,
)
from mindroom.compiler.validation import (
    DAGValidationError,
    DAGValidator,
    ValidationResult,
)

__all__ = [
    "CompilationError",
    "CompilationResult",
    "CompilationWarning",
    "DAGValidationError",
    "DAGValidator",
    "MissionCompiler",
    "MissionDAG",
    "MissionSpec",
    "MissionStep",
    "RetryPolicy",
    "RuntimeTarget",
    "StepType",
    "ValidationResult",
]