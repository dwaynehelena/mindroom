"""Typed federated mission plans with durable checkpoints and compensation."""

# Transaction bodies validate before their shared rollback handler.
# ruff: noqa: TRY301

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, model_validator

from mindroom.logging_config import get_logger

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

logger = get_logger(__name__)

RuntimeRole = Literal["mindroom", "hermes", "openclaw"]
NodeStatus = Literal["pending", "running", "succeeded", "failed", "compensated"]
MissionAdapter = Callable[["MissionNode", dict[str, object], "MissionExecutionContext"], Awaitable[dict[str, object]]]


class MissionError(RuntimeError):
    """A mission graph, checkpoint, adapter, or compensation invariant failed."""


class MissionNode(BaseModel):
    """One typed, runtime-placed unit in a federated mission DAG."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    node_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    runtime: RuntimeRole
    action: str = Field(min_length=1, max_length=256)
    inputs: dict[str, object] = Field(default_factory=dict)
    depends_on: tuple[str, ...] = ()
    retry_limit: int = Field(default=0, ge=0, le=10)
    idempotent: bool = False
    compensation_action: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_retry_safety(self) -> MissionNode:
        """Permit automatic retries only when the node declares idempotency."""
        if self.retry_limit and not self.idempotent:
            message = "mission retries require an explicitly idempotent node"
            raise ValueError(message)
        return self


class MissionPlan(BaseModel):
    """A validated portable mission graph."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    mission_id: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    goal: str = Field(min_length=1, max_length=4000)
    nodes: tuple[MissionNode, ...] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def validate_graph(self) -> MissionPlan:
        """Require unique known dependencies and an acyclic graph."""
        identifiers = [node.node_id for node in self.nodes]
        if len(identifiers) != len(set(identifiers)):
            message = "mission node identifiers must be unique"
            raise ValueError(message)
        known = set(identifiers)
        for node in self.nodes:
            if node.node_id in node.depends_on or any(dependency not in known for dependency in node.depends_on):
                message = f"mission node {node.node_id!r} has an invalid dependency"
                raise ValueError(message)
        _topological_nodes(self.nodes)
        return self


@dataclass(frozen=True, slots=True)
class MissionCheckpoint:
    """One persisted unit result and attempt count."""

    node_id: str
    status: NodeStatus
    attempts: int
    output: dict[str, object] | None
    failure: str | None


@dataclass(frozen=True, slots=True)
class MissionResult:
    """Terminal mission state with content-bounded checkpoint evidence."""

    mission_id: str
    status: Literal["succeeded", "failed"]
    checkpoints: tuple[MissionCheckpoint, ...]


@dataclass(frozen=True, slots=True)
class MissionExecutionContext:
    """Stable invocation identity for one exact mission node attempt."""

    mission_id: str
    attempt: int
    compensation: bool = False


class MissionCheckpointStore:
    """Transactional plan and node-state persistence for restart-safe execution."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the mission database."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS mission_plan (
              mission_id TEXT PRIMARY KEY,
              plan_json TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS mission_checkpoint (
              mission_id TEXT NOT NULL,
              node_id TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('pending','running','succeeded','failed','compensated')),
              attempts INTEGER NOT NULL,
              output_json TEXT,
              failure TEXT,
              PRIMARY KEY(mission_id,node_id)
            );
            """,
        )
        await self._db.commit()

    async def close(self) -> None:
        """Close the mission database."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def initialize(self, plan: MissionPlan) -> None:
        """Idempotently persist one exact plan and its pending checkpoints."""
        plan_json = plan.model_dump_json()
        async with self._lock:
            db = self._required_db()
            await db.execute("BEGIN IMMEDIATE")
            try:
                row = await (
                    await db.execute("SELECT plan_json FROM mission_plan WHERE mission_id=?", (plan.mission_id,))
                ).fetchone()
                if row is None:
                    await db.execute("INSERT INTO mission_plan VALUES(?,?)", (plan.mission_id, plan_json))
                elif row[0] != plan_json:
                    message = "mission identifier was reused with a different plan"
                    raise MissionError(message)
                for node in plan.nodes:
                    await db.execute(
                        "INSERT OR IGNORE INTO mission_checkpoint VALUES(?,?,'pending',0,NULL,NULL)",
                        (plan.mission_id, node.node_id),
                    )
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    async def transition(
        self,
        mission_id: str,
        node_id: str,
        *,
        expected: tuple[NodeStatus, ...],
        status: NodeStatus,
        attempts: int,
        output: dict[str, object] | None = None,
        failure: str | None = None,
    ) -> None:
        """Apply one compare-and-swap checkpoint transition."""
        placeholders = ",".join("?" for _ in expected)
        cursor = await self._required_db().execute(
            f"UPDATE mission_checkpoint SET status=?,attempts=?,output_json=?,failure=? "  # noqa: S608 - placeholders only
            f"WHERE mission_id=? AND node_id=? AND status IN ({placeholders})",
            (
                status,
                attempts,
                _json(output) if output is not None else None,
                failure,
                mission_id,
                node_id,
                *expected,
            ),
        )
        if cursor.rowcount != 1:
            message = f"invalid checkpoint transition for {node_id!r}"
            raise MissionError(message)
        await self._required_db().commit()

    async def checkpoints(self, mission_id: str) -> tuple[MissionCheckpoint, ...]:
        """Read checkpoints in plan order."""
        plan_row = await (
            await self._required_db().execute("SELECT plan_json FROM mission_plan WHERE mission_id=?", (mission_id,))
        ).fetchone()
        if plan_row is None:
            message = "mission plan not found"
            raise MissionError(message)
        plan = MissionPlan.model_validate_json(plan_row[0])
        rows = await (
            await self._required_db().execute(
                "SELECT node_id,status,attempts,output_json,failure FROM mission_checkpoint WHERE mission_id=?",
                (mission_id,),
            )
        ).fetchall()
        by_id = {row[0]: _row_checkpoint(row) for row in rows}
        return tuple(by_id[node.node_id] for node in plan.nodes)

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "mission checkpoint store is not open"
            raise RuntimeError(message)
        return self._db


class MissionExecutor:
    """Execute a typed plan with checkpoints, retries, resume and compensation."""

    def __init__(self, store: MissionCheckpointStore, adapters: Mapping[RuntimeRole, MissionAdapter]) -> None:
        missing = {"mindroom", "hermes", "openclaw"} - set(adapters)
        if missing:
            message = f"mission adapters are missing: {', '.join(sorted(missing))}"
            raise MissionError(message)
        self._store = store
        self._adapters = dict(adapters)

    async def execute(self, plan: MissionPlan) -> MissionResult:
        """Execute or resume a plan and compensate completed nodes after terminal failure."""
        await self._store.initialize(plan)
        checkpoints = {checkpoint.node_id: checkpoint for checkpoint in await self._store.checkpoints(plan.mission_id)}
        completed: list[MissionNode] = []
        for node in _topological_nodes(plan.nodes):
            checkpoint = checkpoints[node.node_id]
            if checkpoint.status == "succeeded":
                completed.append(node)
                continue
            if checkpoint.status == "compensated":
                continue
            if checkpoint.status == "running":
                await self._store.transition(
                    plan.mission_id,
                    node.node_id,
                    expected=("running",),
                    status="pending",
                    attempts=checkpoint.attempts,
                    failure="Recovered an interrupted attempt; explicit retry required.",
                )
                checkpoint = MissionCheckpoint(node.node_id, "pending", checkpoint.attempts, None, None)
            dependency_outputs = {
                dependency: checkpoints[dependency].output or {} for dependency in node.depends_on
            }
            succeeded = False
            for attempt in range(checkpoint.attempts + 1, node.retry_limit + 2):
                await self._store.transition(
                    plan.mission_id,
                    node.node_id,
                    expected=("pending", "failed"),
                    status="running",
                    attempts=attempt,
                )
                try:
                    context = MissionExecutionContext(plan.mission_id, attempt)
                    output = await self._adapters[node.runtime](node, dependency_outputs, context)
                except Exception as exc:
                    failure = f"{type(exc).__module__}.{type(exc).__qualname__}"
                    await self._store.transition(
                        plan.mission_id,
                        node.node_id,
                        expected=("running",),
                        status="failed",
                        attempts=attempt,
                        failure=failure,
                    )
                    if attempt <= node.retry_limit:
                        continue
                    await self._compensate(plan.mission_id, reversed(completed))
                    return MissionResult(plan.mission_id, "failed", await self._store.checkpoints(plan.mission_id))
                await self._store.transition(
                    plan.mission_id,
                    node.node_id,
                    expected=("running",),
                    status="succeeded",
                    attempts=attempt,
                    output=output,
                )
                checkpoints[node.node_id] = MissionCheckpoint(node.node_id, "succeeded", attempt, output, None)
                completed.append(node)
                succeeded = True
                break
            if not succeeded:
                message = f"mission node {node.node_id!r} exhausted without terminal state"
                raise MissionError(message)
        return MissionResult(plan.mission_id, "succeeded", await self._store.checkpoints(plan.mission_id))

    async def _compensate(self, mission_id: str, nodes: Iterable[MissionNode]) -> None:
        for node in nodes:
            if node.compensation_action is None:
                continue
            compensation = node.model_copy(update={"action": node.compensation_action})
            checkpoint = next(
                item for item in await self._store.checkpoints(mission_id) if item.node_id == node.node_id
            )
            try:
                await self._adapters[node.runtime](
                    compensation,
                    {},
                    MissionExecutionContext(mission_id, checkpoint.attempts, compensation=True),
                )
            except Exception:
                logger.exception(
                    "Mission compensation failed",
                    mission_id=mission_id,
                    node_id=node.node_id,
                    runtime=node.runtime,
                )
                continue
            await self._store.transition(
                mission_id,
                node.node_id,
                expected=("succeeded",),
                status="compensated",
                attempts=checkpoint.attempts,
                output=checkpoint.output,
            )


def compile_federated_mission(
    *,
    mission_id: str,
    goal: str,
    research_action: str,
    device_action: str,
    review_action: str,
    device_compensation: str | None = None,
) -> MissionPlan:
    """Compile the canonical research → act → review cross-runtime mission shape."""
    return MissionPlan(
        mission_id=mission_id,
        goal=goal,
        nodes=(
            MissionNode(
                node_id="research",
                runtime="hermes",
                action=research_action,
                retry_limit=1,
                idempotent=True,
            ),
            MissionNode(
                node_id="act",
                runtime="openclaw",
                action=device_action,
                depends_on=("research",),
                compensation_action=device_compensation,
            ),
            MissionNode(node_id="review", runtime="mindroom", action=review_action, depends_on=("act",)),
        ),
    )


def _topological_nodes(nodes: tuple[MissionNode, ...]) -> tuple[MissionNode, ...]:
    by_id = {node.node_id: node for node in nodes}
    remaining = set(by_id)
    ordered: list[MissionNode] = []
    while remaining:
        ready = sorted(node_id for node_id in remaining if set(by_id[node_id].depends_on) <= {n.node_id for n in ordered})
        if not ready:
            message = "mission graph contains a dependency cycle"
            raise ValueError(message)
        for node_id in ready:
            ordered.append(by_id[node_id])
            remaining.remove(node_id)
    return tuple(ordered)


def _row_checkpoint(row: tuple[object, ...]) -> MissionCheckpoint:
    return MissionCheckpoint(
        node_id=str(row[0]),
        status=str(row[1]),  # type: ignore[arg-type]
        attempts=int(row[2]),
        output=json.loads(str(row[3])) if row[3] is not None else None,
        failure=str(row[4]) if row[4] is not None else None,
    )


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)
