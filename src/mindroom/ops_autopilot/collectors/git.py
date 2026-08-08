"""Git repository signal collector.

Uses the GitHub CLI (``gh``) for remote/pr signals and the native ``git`` binary
for local working-tree state (branch, ahead/behind, dirty, recent commits)
against ``/Users/dwayne/mindroom``. All reads are read-only and bounded.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult

_GIT_REPO = Path("/Users/dwayne/mindroom")


@dataclass(slots=True)
class GitSummary:
    """Bounded git state for one repo."""

    branch: str = "unknown"
    ahead: int = 0
    behind: int = 0
    dirty: bool = False
    recent_commits: list[str] = field(default_factory=list)
    prs: list[str] = field(default_factory=list)


class GitCollector(BaseCollector):
    """Collect read-only git/gh state for the autopilot repo."""

    name = "git"

    def __init__(self, repo: Path = _GIT_REPO, *, use_gh: bool | None = None) -> None:
        self._repo = repo
        if use_gh is None:
            use_gh = shutil.which("gh") is not None
        self._use_gh = use_gh

    def _run(self, argv: list[str], *, timeout: float = 10.0) -> str | None:
        try:
            proc = subprocess.run(  # noqa: S603 - local read-only repo inspection only
                argv,
                cwd=self._repo,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    def collect(self) -> CollectResult:
        if not self._repo.exists():
            return CollectResult(self.name, False, error=f"repo not found: {self._repo}")
        try:
            summary = GitSummary()

            branch = self._run(["git", "branch", "--show-current"]) or self._run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"]
            )
            if branch:
                summary.branch = branch

            ahead_behind = self._run(
                ["git", "rev-list", "--left-right", "--count", "HEAD...@{upstream}"]
            )
            if ahead_behind:
                parts = ahead_behind.split()
                if len(parts) == 2:
                    try:
                        summary.ahead, summary.behind = int(parts[0]), int(parts[1])
                    except ValueError:
                        pass

            status = self._run(["git", "status", "--porcelain"])
            summary.dirty = bool(status)

            commits = self._run(["git", "log", "--oneline", "-n", "5"])
            if commits:
                summary.recent_commits = [line for line in commits.splitlines() if line.strip()][:5]

            if self._use_gh:
                prs = self._run(["gh", "pr", "status", "--json", "currentBranch,status"])
                if prs:
                    summary.prs = [line.strip() for line in prs.splitlines() if line.strip()][:3]

            return CollectResult(self.name, True, data=summary)
        except Exception as exc:  # noqa: BLE001 - collect must never raise
            return CollectResult(self.name, False, error=f"{type(exc).__name__}: {exc}")