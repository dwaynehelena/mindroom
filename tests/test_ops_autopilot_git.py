"""Unit tests for the ops-autopilot git collector (fail-soft, bounded)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from mindroom.ops_autopilot.collectors.base import CollectResult
from mindroom.ops_autopilot.collectors.git import GitCollector, GitSummary


def _mk(repo: Path, monkeypatch: pytest.MonkeyPatch, runs: dict[list[str], str]) -> GitCollector:
    monkeypatch.setattr("shutil.which", lambda _name: "/usr/local/bin/gh")
    return GitCollector(repo=repo, use_gh=True)


def test_collect_repo_missing_is_fail_soft(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    col = GitCollector(repo=missing)
    result = col.collect()
    assert isinstance(result, CollectResult)
    assert result.ok is False
    assert result.error is not None and "repo not found" in result.error


def test_collect_success_populates_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    col = _mk(tmp_path, monkeypatch, {})

    def fake_run(argv, *, timeout=10.0):
        cmd = argv[0]
        if cmd == "git" and "branch" in argv:
            return "feature/x"
        if cmd == "git" and "rev-list" in argv:
            return "2\t1"
        if cmd == "git" and "status" in argv:
            return " M file.py"
        if cmd == "git" and "log" in argv:
            return "abc123 commit one\ndef456 commit two"
        if cmd == "gh":
            return "PR-1  #1 open"
        return None

    monkeypatch.setattr(col, "_run", fake_run)
    result = col.collect()
    assert result.ok is True
    summary = result.data
    assert isinstance(summary, GitSummary)
    assert summary.branch == "feature/x"
    assert summary.ahead == 2
    assert summary.behind == 1
    assert summary.dirty is True
    assert summary.recent_commits == ["abc123 commit one", "def456 commit two"]
    assert summary.prs == ["PR-1  #1 open"]


def test_collect_clean_status_means_not_dirty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    col = _mk(tmp_path, monkeypatch, {})

    def fake_run(argv, *, timeout=10.0):
        if "status" in argv:
            return ""
        return None

    monkeypatch.setattr(col, "_run", fake_run)
    result = col.collect()
    assert result.ok is True
    assert result.data.dirty is False


def test_collect_malformed_ahead_behind_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    col = _mk(tmp_path, monkeypatch, {})

    def fake_run(argv, *, timeout=10.0):
        if "rev-list" in argv:
            return "not-an-int"
        return None

    monkeypatch.setattr(col, "_run", fake_run)
    result = col.collect()
    assert result.ok is True
    assert result.data.ahead == 0
    assert result.data.behind == 0


def test_collect_fallback_branch_via_rev_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    col = _mk(tmp_path, monkeypatch, {})

    def fake_run(argv, *, timeout=10.0):
        if "--show-current" in argv:
            return None  # first fails
        if any("abbrev-ref" in a for a in argv):
            return "main"
        return None

    monkeypatch.setattr(col, "_run", fake_run)
    result = col.collect()
    assert result.data.branch == "main"


def test_collect_internal_exception_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    col = _mk(tmp_path, monkeypatch, {})

    def boom(argv, *, timeout=10.0):  # noqa: ARG001
        raise RuntimeError("unexpected git crash")

    monkeypatch.setattr(col, "_run", boom)
    result = col.collect()
    assert result.ok is False
    assert "RuntimeError" in (result.error or "")


def test_run_handles_timeout_and_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    col = GitCollector(repo=tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(subprocess.TimeoutExpired("git", 10)),
    )
    assert col._run(["git", "status"]) is None

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(OSError("no git")),
    )
    assert col._run(["git", "status"]) is None


def test_run_returns_none_on_nonzero_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess
    from types import SimpleNamespace

    col = GitCollector(repo=tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=128, stdout="", stderr="fatal"),
    )
    assert col._run(["git", "status"]) is None


def test_git_summary_defaults() -> None:
    s = GitSummary()
    assert s.branch == "unknown"
    assert s.ahead == 0 and s.behind == 0
    assert s.dirty is False
    assert s.recent_commits == []
    assert s.prs == []