"""Contract tests for the non-live AI-DLC agent-team reference configuration."""

from __future__ import annotations

from pathlib import Path

import yaml

from mindroom.config.main import Config

REFERENCE_CONFIG = Path("docs/dev/aidlc-agent-team/reference-config.yaml")
EXPECTED_MEMBERS = {
    "aidlc_product",
    "aidlc_design",
    "aidlc_delivery",
    "aidlc_architect",
    "aidlc_aws_platform",
    "aidlc_compliance",
    "aidlc_devsecops",
    "aidlc_developer",
    "aidlc_quality",
    "aidlc_pipeline_deploy",
    "aidlc_operations",
    "aidlc_product_lead",
    "aidlc_architecture_reviewer",
    "aidlc_composer",
}


def _load_reference() -> tuple[dict[str, object], Config]:
    authored = yaml.safe_load(REFERENCE_CONFIG.read_text(encoding="utf-8"))
    return authored, Config.model_validate(authored)


def test_reference_config_is_schema_valid_and_team_members_are_exact() -> None:
    """The milestone must remain loadable and retain the approved specialist coverage."""
    authored, config = _load_reference()

    assert set(config.agents) == EXPECTED_MEMBERS
    assert set(config.teams["aidlc_team"].agents) == EXPECTED_MEMBERS
    assert config.teams["aidlc_team"].mode == "coordinate"
    assert authored["teams"]["aidlc_team"]["rooms"] == ["aidlc"]


def test_reference_config_preserves_human_control_boundaries() -> None:
    """Consequential actions and workflow changes stay behind explicit approval language."""
    authored, _config = _load_reference()
    rendered = yaml.safe_dump(authored, sort_keys=True).lower()

    assert "explicit human approval" in rendered
    assert "do not deploy" in rendered
    assert "live-configuration" in rendered
    assert "destructive actions" in rendered
    assert "external changes" in rendered
    assert "propose workflow changes for human approval before applying them" in rendered