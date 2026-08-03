"""Receipt-bound filesystem canary adapters for governed learning proposals."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from contextlib import suppress
from typing import TYPE_CHECKING

from mindroom.learning_loop import LearningLoopError

if TYPE_CHECKING:
    from pathlib import Path

    from mindroom.learning_loop import LearningProposal

_CANARY_DIRECTORY = ".mindroom-learning-canary"


class LearningFilesystemPublisher:
    """Publish approved proposals into a non-executable runtime canary namespace."""

    def __init__(self, root: Path, runtime: str) -> None:
        if runtime not in {"openclaw", "hermes"}:
            message = "learning canary runtime must be openclaw or hermes"
            raise ValueError(message)
        expanded = root.expanduser()
        if not expanded.is_absolute() or not expanded.is_dir() or expanded.is_symlink():
            message = "learning canary root must be an existing absolute non-symlink directory"
            raise ValueError(message)
        self._root = expanded.resolve(strict=True)
        self._runtime = runtime

    async def publish(self, proposal: LearningProposal) -> str:
        """Atomically publish and read back one approved exact proposal."""
        payload = _payload(proposal, self._runtime)
        receipt = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        directory = self._root / _CANARY_DIRECTORY
        _ensure_real_directory(directory)
        artifact = self._artifact(proposal)
        if artifact.exists():
            if artifact.is_symlink() or artifact.read_bytes() != payload:
                message = "learning canary proposal equivocation denied"
                raise LearningLoopError(message)
        else:
            temporary = artifact.with_name(f".{artifact.name}.{os.getpid()}.tmp")
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(artifact)
                _fsync_directory(directory)
            finally:
                temporary.unlink(missing_ok=True)
        if artifact.is_symlink() or f"sha256:{hashlib.sha256(artifact.read_bytes()).hexdigest()}" != receipt:
            message = "learning canary readback verification failed"
            raise LearningLoopError(message)
        return receipt

    async def rollback(self, proposal: LearningProposal, receipt: str) -> None:
        """Remove only an artifact whose exact bytes match the publication receipt."""
        artifact = self._artifact(proposal)
        if not artifact.exists():
            return
        if artifact.is_symlink() or not receipt.startswith("sha256:"):
            message = "learning canary rollback receipt is invalid"
            raise LearningLoopError(message)
        actual = f"sha256:{hashlib.sha256(artifact.read_bytes()).hexdigest()}"
        if actual != receipt:
            message = "learning canary rollback receipt does not match the artifact"
            raise LearningLoopError(message)
        artifact.unlink()
        _fsync_directory(artifact.parent)

    def _artifact(self, proposal: LearningProposal) -> Path:
        filename = hashlib.sha256(proposal.proposal_id.encode()).hexdigest() + ".json"
        return self._root / _CANARY_DIRECTORY / filename


def _payload(proposal: LearningProposal, runtime: str) -> bytes:
    artifact_json = json.dumps(
        proposal.artifact,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )
    if (
        proposal.stage != "approved"
        or proposal.evaluation is None
        or not proposal.evaluation.passed
        or not proposal.reviewed_by
        or not proposal.review_reason
        or hashlib.sha256(artifact_json.encode()).hexdigest() != proposal.artifact_digest
    ):
        message = "learning canary requires an exact evaluated and human-approved proposal"
        raise LearningLoopError(message)
    return json.dumps(
        {
            "artifact": proposal.artifact,
            "artifact_digest": proposal.artifact_digest,
            "kind": proposal.kind,
            "proposal_id": proposal.proposal_id,
            "review_reason": proposal.review_reason,
            "reviewed_by": proposal.reviewed_by,
            "runtime": runtime,
            "source_run_id": proposal.source_run_id,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode()


def _ensure_real_directory(directory: Path) -> None:
    with suppress(FileExistsError):
        directory.mkdir(mode=0o700)
    mode = os.lstat(directory).st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        message = "learning canary path must be a real directory"
        raise LearningLoopError(message)


def _fsync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
