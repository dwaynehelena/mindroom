"""Governed run-to-proposal evaluation, review, canary publication and rollback."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal

import aiosqlite

if TYPE_CHECKING:
    from pathlib import Path

ArtifactKind = Literal["skill", "memory"]
LearningStage = Literal[
    "proposed",
    "evaluated",
    "approved",
    "rejected",
    "canary",
    "stable",
    "rolled_back",
    "uncertain",
]
RuntimePublisher = Callable[["LearningProposal"], Awaitable[str]]
RuntimeRollback = Callable[["LearningProposal", str], Awaitable[None]]


class LearningLoopError(RuntimeError):
    """A proposal, evaluation, review, publication, or rollback invariant failed."""


@dataclass(frozen=True, slots=True)
class EvaluationEvidence:
    """Deterministic regression evidence for one exact artifact digest."""

    suite_id: str
    artifact_digest: str
    tests_run: int
    tests_passed: int
    baseline_score: int
    candidate_score: int

    @property
    def passed(self) -> bool:
        """Return whether every test passed without a score regression."""
        return (
            self.tests_run > 0
            and self.tests_passed == self.tests_run
            and self.candidate_score >= self.baseline_score
        )


@dataclass(frozen=True, slots=True)
class LearningProposal:
    """One immutable proposed skill or memory improvement."""

    proposal_id: str
    source_run_id: str
    kind: ArtifactKind
    artifact: dict[str, object]
    artifact_digest: str
    stage: LearningStage
    proposed_at: datetime
    evaluation: EvaluationEvidence | None = None
    reviewed_by: str | None = None
    review_reason: str | None = None


class LearningLoopStore:
    """Transactional governance state and per-runtime canary receipts."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._db: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        """Open the governance database."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self._path)
        await self._db.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS learning_proposal (
              proposal_id TEXT PRIMARY KEY,
              source_run_id TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('skill','memory')),
              artifact_json TEXT NOT NULL,
              artifact_digest TEXT NOT NULL,
              stage TEXT NOT NULL CHECK(stage IN ('proposed','evaluated','approved','rejected','canary','stable','rolled_back','uncertain')),
              proposed_at TEXT NOT NULL,
              evaluation_json TEXT,
              reviewed_by TEXT,
              review_reason TEXT
            );
            CREATE TABLE IF NOT EXISTS learning_publication (
              proposal_id TEXT NOT NULL,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw','hermes')),
              receipt TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('canary','stable','rolled_back','uncertain')),
              PRIMARY KEY(proposal_id,runtime)
            );
            """,
        )
        await self._migrate_uncertain_states()
        await self._db.commit()

    async def close(self) -> None:
        """Close the governance database."""
        if self._db is not None:
            await self._db.close()
            self._db = None

    async def propose(
        self,
        *,
        proposal_id: str,
        source_run_id: str,
        kind: ArtifactKind,
        artifact: dict[str, object],
        proposed_at: datetime,
    ) -> LearningProposal:
        """Persist an immutable proposal derived from one successful source run."""
        if not proposal_id or not source_run_id or not artifact:
            message = "learning proposal identity, source run, and artifact are required"
            raise LearningLoopError(message)
        timestamp = _utc(proposed_at)
        artifact_json = _json_text(artifact)
        digest = hashlib.sha256(artifact_json.encode()).hexdigest()
        values = (proposal_id, source_run_id, kind, artifact_json, digest, "proposed", timestamp.isoformat())
        cursor = await self._required_db().execute(
            "INSERT OR IGNORE INTO learning_proposal VALUES(?,?,?,?,?,?,?,NULL,NULL,NULL)",
            values,
        )
        if cursor.rowcount != 1:
            row = await (
                await self._required_db().execute(
                    "SELECT proposal_id,source_run_id,kind,artifact_json,artifact_digest,stage,proposed_at "
                    "FROM learning_proposal WHERE proposal_id=?",
                    (proposal_id,),
                )
            ).fetchone()
            if tuple(row or ()) != values:
                message = "learning proposal identity equivocation denied"
                raise LearningLoopError(message)
        await self._required_db().commit()
        return await self.get(proposal_id)

    async def record_evaluation(self, proposal_id: str, evidence: EvaluationEvidence) -> LearningProposal:
        """Advance only exact artifacts with complete non-regressing test evidence."""
        proposal = await self.get(proposal_id)
        if proposal.stage not in {"proposed", "evaluated"}:
            message = "learning proposal is not awaiting evaluation"
            raise LearningLoopError(message)
        if evidence.artifact_digest != proposal.artifact_digest or not evidence.passed or not evidence.suite_id:
            message = "learning evaluation does not prove a passing non-regression"
            raise LearningLoopError(message)
        evidence_json = _json_text(
            {
                "artifact_digest": evidence.artifact_digest,
                "baseline_score": evidence.baseline_score,
                "candidate_score": evidence.candidate_score,
                "suite_id": evidence.suite_id,
                "tests_passed": evidence.tests_passed,
                "tests_run": evidence.tests_run,
            },
        )
        await self._transition(
            proposal_id,
            expected=("proposed", "evaluated"),
            stage="evaluated",
            evaluation_json=evidence_json,
        )
        return await self.get(proposal_id)

    async def review(
        self,
        proposal_id: str,
        *,
        reviewer_id: str,
        approved: bool,
        reason: str,
    ) -> LearningProposal:
        """Record one explicit human review after successful evaluation."""
        if not reviewer_id or not reason.strip():
            message = "learning review requires canonical reviewer and reason"
            raise LearningLoopError(message)
        await self._transition(
            proposal_id,
            expected=("evaluated",),
            stage="approved" if approved else "rejected",
            reviewed_by=reviewer_id,
            review_reason=reason.strip(),
        )
        return await self.get(proposal_id)

    async def set_stage(self, proposal_id: str, *, expected: tuple[LearningStage, ...], stage: LearningStage) -> None:
        """Apply one exact governance-stage transition."""
        await self._transition(proposal_id, expected=expected, stage=stage)

    async def publication(
        self,
        proposal_id: str,
        runtime: Literal["openclaw", "hermes"],
        *,
        receipt: str,
        status: Literal["canary", "stable", "rolled_back", "uncertain"],
    ) -> None:
        """Persist an exact runtime publication receipt."""
        if not receipt:
            message = "learning publication receipt is blank"
            raise LearningLoopError(message)
        await self._required_db().execute(
            "INSERT INTO learning_publication VALUES(?,?,?,?) "
            "ON CONFLICT(proposal_id,runtime) DO UPDATE SET receipt=excluded.receipt,status=excluded.status",
            (proposal_id, runtime, receipt, status),
        )
        await self._required_db().commit()

    async def receipts(self, proposal_id: str) -> dict[str, tuple[str, str]]:
        """Return runtime publication status and receipt."""
        rows = await (
            await self._required_db().execute(
                "SELECT runtime,status,receipt FROM learning_publication WHERE proposal_id=?",
                (proposal_id,),
            )
        ).fetchall()
        return {row[0]: (row[1], row[2]) for row in rows}

    async def get(self, proposal_id: str) -> LearningProposal:
        """Load one proposal."""
        row = await (
            await self._required_db().execute("SELECT * FROM learning_proposal WHERE proposal_id=?", (proposal_id,))
        ).fetchone()
        if row is None:
            message = "learning proposal not found"
            raise LearningLoopError(message)
        evaluation_value = json.loads(row[7]) if row[7] else None
        evaluation = EvaluationEvidence(**evaluation_value) if evaluation_value else None
        return LearningProposal(
            proposal_id=row[0],
            source_run_id=row[1],
            kind=row[2],
            artifact=json.loads(row[3]),
            artifact_digest=row[4],
            stage=row[5],
            proposed_at=datetime.fromisoformat(row[6]),
            evaluation=evaluation,
            reviewed_by=row[8],
            review_reason=row[9],
        )

    async def _transition(
        self,
        proposal_id: str,
        *,
        expected: tuple[LearningStage, ...],
        stage: LearningStage,
        evaluation_json: str | None = None,
        reviewed_by: str | None = None,
        review_reason: str | None = None,
    ) -> None:
        placeholders = ",".join("?" for _ in expected)
        cursor = await self._required_db().execute(
            f"UPDATE learning_proposal SET stage=?,evaluation_json=COALESCE(?,evaluation_json),"  # noqa: S608
            f"reviewed_by=COALESCE(?,reviewed_by),review_reason=COALESCE(?,review_reason) "
            f"WHERE proposal_id=? AND stage IN ({placeholders})",
            (stage, evaluation_json, reviewed_by, review_reason, proposal_id, *expected),
        )
        if cursor.rowcount != 1:
            message = "learning governance transition is invalid"
            raise LearningLoopError(message)
        await self._required_db().commit()

    def _required_db(self) -> aiosqlite.Connection:
        if self._db is None:
            message = "learning loop store is not open"
            raise RuntimeError(message)
        return self._db

    async def _migrate_uncertain_states(self) -> None:
        """Add fail-closed uncertainty states to an existing version-one ledger."""
        proposal_row = await (
            await self._required_db().execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_proposal'",
            )
        ).fetchone()
        publication_row = await (
            await self._required_db().execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='learning_publication'",
            )
        ).fetchone()
        if proposal_row is None or publication_row is None:
            message = "learning loop schema initialization failed"
            raise LearningLoopError(message)
        if "'uncertain'" in str(proposal_row[0]) and "'uncertain'" in str(publication_row[0]):
            return
        await self._required_db().executescript(
            """
            ALTER TABLE learning_proposal RENAME TO learning_proposal_v1;
            ALTER TABLE learning_publication RENAME TO learning_publication_v1;
            CREATE TABLE learning_proposal (
              proposal_id TEXT PRIMARY KEY,
              source_run_id TEXT NOT NULL,
              kind TEXT NOT NULL CHECK(kind IN ('skill','memory')),
              artifact_json TEXT NOT NULL,
              artifact_digest TEXT NOT NULL,
              stage TEXT NOT NULL CHECK(stage IN ('proposed','evaluated','approved','rejected','canary','stable','rolled_back','uncertain')),
              proposed_at TEXT NOT NULL,
              evaluation_json TEXT,
              reviewed_by TEXT,
              review_reason TEXT
            );
            CREATE TABLE learning_publication (
              proposal_id TEXT NOT NULL,
              runtime TEXT NOT NULL CHECK(runtime IN ('openclaw','hermes')),
              receipt TEXT NOT NULL,
              status TEXT NOT NULL CHECK(status IN ('canary','stable','rolled_back','uncertain')),
              PRIMARY KEY(proposal_id,runtime)
            );
            INSERT INTO learning_proposal SELECT * FROM learning_proposal_v1;
            INSERT INTO learning_publication SELECT * FROM learning_publication_v1;
            DROP TABLE learning_publication_v1;
            DROP TABLE learning_proposal_v1;
            """,
        )


class GovernedPublisher:
    """Canary-publish to both runtimes and roll back all partial success on failure."""

    def __init__(
        self,
        store: LearningLoopStore,
        publishers: Mapping[Literal["openclaw", "hermes"], RuntimePublisher],
        rollbacks: Mapping[Literal["openclaw", "hermes"], RuntimeRollback],
        stable_publishers: Mapping[Literal["openclaw", "hermes"], RuntimePublisher] | None = None,
        stable_rollbacks: Mapping[Literal["openclaw", "hermes"], RuntimeRollback] | None = None,
    ) -> None:
        if set(publishers) != {"openclaw", "hermes"} or set(rollbacks) != {"openclaw", "hermes"}:
            message = "governed publishing requires OpenClaw and Hermes publish/rollback adapters"
            raise LearningLoopError(message)
        if (stable_publishers is None) != (stable_rollbacks is None) or (
            stable_publishers is not None and set(stable_publishers) != {"openclaw", "hermes"}
        ) or (stable_rollbacks is not None and set(stable_rollbacks) != {"openclaw", "hermes"}):
            message = "stable learning publication requires publish/rollback adapters for both runtimes"
            raise LearningLoopError(message)
        self._store = store
        self._publishers = dict(publishers)
        self._rollbacks = dict(rollbacks)
        self._stable_publishers = dict(stable_publishers) if stable_publishers is not None else None
        self._stable_rollbacks = dict(stable_rollbacks) if stable_rollbacks is not None else None
        self._lock = asyncio.Lock()

    async def canary(self, proposal_id: str) -> LearningProposal:
        """Publish an approved exact proposal to both canaries or roll back partial success."""
        async with self._lock:
            proposal = await self._store.get(proposal_id)
            if proposal.stage != "approved":
                message = "only approved learning proposals may enter canary"
                raise LearningLoopError(message)
            published: list[tuple[Literal["openclaw", "hermes"], str]] = []
            try:
                for runtime in ("openclaw", "hermes"):
                    receipt = _required_receipt(runtime, await self._publishers[runtime](proposal))
                    await self._store.publication(proposal_id, runtime, receipt=receipt, status="canary")
                    published.append((runtime, receipt))
            except Exception as exc:
                rollback_uncertain = False
                for runtime, receipt in reversed(published):
                    try:
                        await self._rollbacks[runtime](proposal, receipt)
                    except Exception:
                        rollback_uncertain = True
                        await self._store.publication(proposal_id, runtime, receipt=receipt, status="uncertain")
                    else:
                        await self._store.publication(proposal_id, runtime, receipt=receipt, status="rolled_back")
                await self._store.set_stage(
                    proposal_id,
                    expected=("approved",),
                    stage="uncertain" if rollback_uncertain else "rolled_back",
                )
                if rollback_uncertain:
                    message = "learning canary rollback could not prove cleanup and is uncertain"
                    raise LearningLoopError(message) from exc
                message = "learning canary publication failed and was rolled back"
                raise LearningLoopError(message) from exc
            await self._store.set_stage(proposal_id, expected=("approved",), stage="canary")
            return await self._store.get(proposal_id)

    async def stabilize(self, proposal_id: str) -> LearningProposal:
        """Actively install both runtimes or compensate to the intact canary state."""
        async with self._lock:
            proposal = await self._store.get(proposal_id)
            receipts = await self._store.receipts(proposal_id)
            if proposal.stage != "canary" or set(receipts) != {"openclaw", "hermes"} or any(
                status not in {"canary", "stable"} for status, _receipt in receipts.values()
            ):
                message = "stable promotion requires successful OpenClaw and Hermes canaries"
                raise LearningLoopError(message)
            if self._stable_publishers is None or self._stable_rollbacks is None:
                message = "stable promotion requires active runtime publication adapters"
                raise LearningLoopError(message)
            installed: list[tuple[Literal["openclaw", "hermes"], str, str]] = []
            try:
                for runtime in ("openclaw", "hermes"):
                    if receipts[runtime][0] == "stable":
                        continue
                    canary_receipt = receipts[runtime][1]
                    stable_receipt = _required_receipt(
                        runtime,
                        await self._stable_publishers[runtime](proposal),
                    )
                    installed.append((runtime, stable_receipt, canary_receipt))
                    await self._store.publication(proposal_id, runtime, receipt=stable_receipt, status="stable")
            except Exception as exc:
                rollback_uncertain = False
                for runtime, stable_receipt, canary_receipt in reversed(installed):
                    try:
                        await self._stable_rollbacks[runtime](proposal, stable_receipt)
                    except Exception:
                        rollback_uncertain = True
                        await self._store.publication(
                            proposal_id,
                            runtime,
                            receipt=stable_receipt,
                            status="uncertain",
                        )
                    else:
                        await self._store.publication(
                            proposal_id,
                            runtime,
                            receipt=canary_receipt,
                            status="canary",
                        )
                if rollback_uncertain:
                    await self._store.set_stage(proposal_id, expected=("canary",), stage="uncertain")
                    message = "stable learning rollback could not prove cleanup and is uncertain"
                    raise LearningLoopError(message) from exc
                message = "stable learning publication failed and partial installation was rolled back"
                raise LearningLoopError(message) from exc
            await self._store.set_stage(proposal_id, expected=("canary",), stage="stable")
            return await self._store.get(proposal_id)


def _json_text(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def _required_receipt(runtime: str, receipt: str) -> str:
    if not receipt:
        message = f"{runtime} returned a blank canary receipt"
        raise LearningLoopError(message)
    return receipt


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        message = "learning-loop timestamps must include a timezone"
        raise LearningLoopError(message)
    return value.astimezone(UTC)
