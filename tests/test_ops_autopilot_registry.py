"""Unit tests for the ops-autopilot collector registry."""

from __future__ import annotations

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult
from mindroom.ops_autopilot.collectors.registry import CollectorRegistry, build_default_registry


class _FakeCollector(BaseCollector):
    def __init__(self, name: str, result: CollectResult) -> None:
        self.name = name
        self._result = result

    def collect(self) -> CollectResult:
        return self._result


def test_empty_registry_runs_to_empty_list() -> None:
    reg = CollectorRegistry()
    assert reg.run_all() == []


def test_add_then_run_preserves_order() -> None:
    a = _FakeCollector("a", CollectResult("a", True))
    b = _FakeCollector("b", CollectResult("b", False, error="nope"))
    reg = CollectorRegistry()
    reg.add(a)
    reg.add(b)
    results = reg.run_all()
    assert [r.source for r in results] == ["a", "b"]
    assert results[0].ok is True
    assert results[1].ok is False


def test_registry_from_list() -> None:
    a = _FakeCollector("a", CollectResult("a", True))
    reg = CollectorRegistry([a])
    assert len(reg.run_all()) == 1


def test_default_registry_contains_git_and_scheduler() -> None:
    reg = build_default_registry()
    names = [c.name for c in reg._collectors]
    assert "git" in names
    assert "scheduler" in names


def test_default_registry_contains_deferred_mail_and_calendar() -> None:
    reg = build_default_registry()
    names = [c.name for c in reg._collectors]
    assert "mail" in names
    assert "calendar" in names
    # Deferred sources are wired after the live git/scheduler signals.
    assert names.index("mail") > names.index("scheduler")
    assert names.index("calendar") > names.index("mail")


def test_registry_keeps_fail_soft_results_in_order() -> None:
    reg = CollectorRegistry(
        [
            _FakeCollector("ok", CollectResult("ok", True)),
            _FakeCollector("bad", CollectResult("bad", False, error="boom")),
        ],
    )
    results = reg.run_all()
    assert [r.source for r in results] == ["ok", "bad"]
    # The failing collector produced a fail-soft result that did not abort the run.
    assert results[0].ok is True
    assert results[1].ok is False