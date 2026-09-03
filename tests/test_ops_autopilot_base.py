"""Unit tests for the ops-autopilot base collector contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mindroom.ops_autopilot.collectors.base import BaseCollector, CollectResult


def test_collect_result_defaults_to_utc_timestamp() -> None:
    result = CollectResult(source="x", ok=True)
    assert result.source == "x"
    assert result.ok is True
    assert result.data is None
    assert result.error is None
    assert result.collected_at.tzinfo is UTC


def test_collect_result_render_ok() -> None:
    result = CollectResult(source="git", ok=True)
    assert result.render() == "• git: OK"


def test_collect_result_render_error_includes_message() -> None:
    result = CollectResult(source="git", ok=False, error="boom")
    assert result.render() == "• git: ERROR — boom"


def test_collect_result_render_error_without_message_uses_default() -> None:
    result = CollectResult(source="git", ok=False)
    assert result.render() == "• git: ERROR — collect failed"


def test_collect_result_accepts_data_payload() -> None:
    result = CollectResult(source="git", ok=True, data={"branch": "main"})
    assert result.data == {"branch": "main"}


def test_base_collector_is_abstract() -> None:
    # Cannot instantiate an abstract collector.
    with pytest.raises(TypeError):
        BaseCollector()  # type: ignore[abstract]


def test_concrete_collector_subclass_runs() -> None:
    class Fake(BaseCollector):
        name = "fake"

        def collect(self) -> CollectResult:
            return CollectResult(self.name, True, data=123)

    assert Fake().collect().data == 123


def test_base_collector_requires_name_and_collect() -> None:
    # A subclass missing `name` or `collect` must still be abstract.
    class Broken(BaseCollector):  # type: ignore[misc]
        pass

    with pytest.raises(TypeError):
        Broken()  # type: ignore[abstract]


def test_collected_at_is_monotonic() -> None:
    a = CollectResult("x", True).collected_at
    b = CollectResult("x", True).collected_at
    assert b >= a


def test_collect_result_datetime_is_timezone_aware() -> None:
    result = CollectResult("x", True)
    assert result.collected_at.utcoffset() is not None