"""Tests for fail-fast validation of an empty ``agents:`` config at boot.

The empty-agents check lives in the orchestrator startup/boot path (not in
``load_config`` and not on the ``Config`` model) so that config-management
tools, which legitimately operate on empty configs to create the first agent,
remain unaffected.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from mindroom.config.main import Config, ConfigRuntimeValidationError
from mindroom.constants import ROUTER_AGENT_NAME
from mindroom.orchestrator import _MultiAgentOrchestrator
from tests.conftest import orchestrator_runtime_paths


class _FakeUser:
    """Minimal stand-in for an AgentMatrixUser in boot tests."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id


def _empty_agents_config(runtime_paths) -> Config:
    """Build a valid Config with no agents defined."""
    return Config.validate_with_runtime(
        {
            "router": {"model": "default"},
            "models": {"default": {"provider": "openai", "id": "gpt-4"}},
            "agents": {},
        },
        runtime_paths,
    )


@pytest.mark.asyncio
async def test_boot_fails_fast_when_agents_section_is_empty(tmp_path: Path) -> None:
    """Booting the runtime with an empty ``agents:`` config must fail fast."""
    runtime_paths = orchestrator_runtime_paths(tmp_path)
    orchestrator = _MultiAgentOrchestrator(runtime_paths=runtime_paths)

    with (
        patch("mindroom.orchestrator.load_config", return_value=_empty_agents_config(runtime_paths)),
        pytest.raises(ConfigRuntimeValidationError, match="no agents"),
    ):
        await orchestrator.initialize()


@pytest.mark.asyncio
async def test_boot_succeeds_with_at_least_one_agent(tmp_path: Path) -> None:
    """Booting the runtime with a real agent must not raise the empty-agents error."""
    runtime_paths = orchestrator_runtime_paths(tmp_path)
    orchestrator = _MultiAgentOrchestrator(runtime_paths=runtime_paths)

    config = Config.validate_with_runtime(
        {
            "router": {"model": "default"},
            "models": {"default": {"provider": "openai", "id": "gpt-4"}},
            "agents": {"general": {"display_name": "General"}},
        },
        runtime_paths,
    )

    with (
        patch("mindroom.orchestrator.load_config", return_value=config),
        patch("mindroom.orchestrator._MultiAgentOrchestrator._build_hook_registry"),
        patch("mindroom.orchestrator._MultiAgentOrchestrator._preflight_account_provisioning"),
        patch(
            "mindroom.orchestrator._MultiAgentOrchestrator._prepare_user_account",
            new=AsyncMock(),
        ),
        patch(
            "mindroom.orchestrator._MultiAgentOrchestrator._prepare_entity_accounts",
            new=AsyncMock(
                return_value={
                    ROUTER_AGENT_NAME: _FakeUser("@router:localhost"),
                    "general": _FakeUser("@general:localhost"),
                }
            ),
        ),
        patch(
            "mindroom.orchestrator._MultiAgentOrchestrator._sync_mcp_manager",
            new=AsyncMock(return_value=set()),
        ),
        patch("mindroom.orchestrator._MultiAgentOrchestrator._configure_approval_store_transport"),
        patch("mindroom.orchestrator._MultiAgentOrchestrator._activate_hook_registry"),
        patch("mindroom.orchestrator._MultiAgentOrchestrator._create_managed_bot"),
    ):
        await orchestrator.initialize()

    assert orchestrator.config is config