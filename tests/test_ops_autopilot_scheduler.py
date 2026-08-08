"""Unit tests for the ops-autopilot scheduler collector (fail-soft state read)."""

from __future__ import annotations

import json
from pathlib import Path

from mindroom.ops_autopilot.collectors.scheduler import AUTOPILOT_CRON, SchedulerCollector


def test_default_cron_is_0730() -> None:
    assert AUTOPILOT_CRON == "30 7 * * *"


def test_collect_without_state_reports_not_registered(tmp_path: Path) -> None:
    state_path = tmp_path / "nope.json"
    col = SchedulerCollector(state_path=state_path)
    result = col.collect()
    assert result.ok is True
    assert result.data["cron"] == AUTOPILOT_CRON
    assert result.data["registered"] is False
    assert result.data["task_id"] is None


def test_collect_reads_registered_task(tmp_path: Path) -> None:
    state_path = tmp_path / "schedule.json"
    state_path.write_text(json.dumps({"cron": AUTOPILOT_CRON, "task_id": "abc123"}), encoding="utf-8")
    col = SchedulerCollector(state_path=state_path)
    result = col.collect()
    assert result.ok is True
    assert result.data["task_id"] == "abc123"
    assert result.data["registered"] is True


def test_collect_corrupt_state_is_fail_soft(tmp_path: Path) -> None:
    state_path = tmp_path / "schedule.json"
    state_path.write_text("not json {{{", encoding="utf-8")
    col = SchedulerCollector(state_path=state_path)
    result = col.collect()
    assert result.ok is True  # collector itself never fails
    assert result.data["registered"] is False
    assert result.data["task_id"] is None


def test_collect_state_with_empty_task_id_not_registered(tmp_path: Path) -> None:
    state_path = tmp_path / "schedule.json"
    state_path.write_text(json.dumps({"cron": AUTOPILOT_CRON, "task_id": ""}), encoding="utf-8")
    col = SchedulerCollector(state_path=state_path)
    result = col.collect()
    assert result.data["registered"] is False


def test_collect_state_with_non_dict_is_fail_soft(tmp_path: Path) -> None:
    state_path = tmp_path / "schedule.json"
    state_path.write_text(json.dumps(["a", "b"]), encoding="utf-8")
    col = SchedulerCollector(state_path=state_path)
    result = col.collect()
    assert result.ok is True
    assert result.data["registered"] is False


def test_collect_uses_explicit_cron(tmp_path: Path) -> None:
    col = SchedulerCollector(cron="0 9 * * *", state_path=tmp_path / "s.json")
    assert col.collect().data["cron"] == "0 9 * * *"