"""Unit tests for P8 Phase 2 gate semantics.

Verifies that the ops-autopilot gate semantics are correctly reflected in the
runtime ``~/.mindroom/config.yaml`` tool_approval rules:

* brief delivery is UNGATED (default ``auto_approve``; no rule forces approval
  on the delivery path),
* suggested-action outbound writes are ARIP-gated (``ops_autopilot.gh_action``
  requires approval by ``@dwayne:localhost``) and the gate fails closed.
"""

from __future__ import annotations

from pathlib import Path

from mindroom.config.main import load_config
from mindroom.constants import resolve_runtime_paths

RUNTIME_CONFIG = Path.home() / ".mindroom" / "config.yaml"
SUGGESTED_ACTION_TOOL = "ops_autopilot.gh_action"


def _runtime_config() -> object:
    paths = resolve_runtime_paths(
        config_path=RUNTIME_CONFIG,
        storage_path=Path.home() / ".mindroom" / "mindroom_data",
        process_env={},
    )
    return load_config(paths)


def test_brief_delivery_is_ungated() -> None:
    """The delivery path must not be forced through approval (default auto_approve)."""
    config = _runtime_config()
    assert config.tool_approval.default == "auto_approve"
    # No rule should force approval on the delivery tool itself.
    delivery_matches = [r for r in config.tool_approval.rules if "deliver" in r.match]
    assert delivery_matches == []


def test_suggested_action_is_arip_gated() -> None:
    """The suggested-action tool must require approval."""
    config = _runtime_config()
    rule = next((r for r in config.tool_approval.rules if r.match == SUGGESTED_ACTION_TOOL), None)
    assert rule is not None
    assert rule.action == "require_approval"
    assert rule.timeout_days is not None and rule.timeout_days > 0


def test_suggested_action_glob_is_gated() -> None:
    """The broader gh_* glob also requires approval (defense in depth)."""
    config = _runtime_config()
    glob_rule = next((r for r in config.tool_approval.rules if r.match == "gh_*"), None)
    assert glob_rule is not None
    assert glob_rule.action == "require_approval"


def test_gate_fails_closed_without_live_store() -> None:
    """The ARIP gate must fail closed (deny) when no live approval store exists."""
    import asyncio
    from unittest.mock import patch

    from mindroom.ops_autopilot.approval.gate import ApprovalGate

    async def _run() -> object:
        with patch("mindroom.ops_autopilot.approval.gate.get_approval_store", return_value=None):
            return await ApprovalGate(tool_name=SUGGESTED_ACTION_TOOL).gate("brief")

    outcome = asyncio.run(_run())
    assert outcome.approved is False
    assert outcome.status == "denied"