"""Tests for strict Personal Ops configuration and owned service lifecycle."""

# ruff: noqa: ANN001, ANN003, ANN202, D103

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from pydantic import ValidationError

from mindroom.config.main import Config
from mindroom.config.personal_ops import PersonalOpsConfig
from mindroom.constants import RuntimePaths, matrix_state_file
from mindroom.orchestrator import _MultiAgentOrchestrator
from mindroom.personal_ops import PersonalOpsError
from mindroom.personal_ops_service import PersonalOpsService

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


def _runtime_paths(tmp_path: Path) -> RuntimePaths:
    return RuntimePaths(
        config_path=tmp_path / "config.yaml",
        config_dir=tmp_path,
        env_path=tmp_path / ".env",
        storage_root=tmp_path / "data",
    )


def _enabled_config(**updates) -> PersonalOpsConfig:
    values = {
        "executor_agent": "ops",
        "mindroom_write_tools": ("gmail.send",),
        "openclaw_gateway_url": "ws://127.0.0.1:18789",
        "openclaw_write_methods": ("send",),
    }
    values.update(updates)
    return PersonalOpsConfig(
        enabled=True,
        github_repository="owner/repository",
        room_id="!briefs:localhost",
        **values,
    )


async def _invoker(tool_name, function_name, arguments):
    del tool_name, function_name, arguments
    return ""


async def _executor(action, idempotency_key):
    del action, idempotency_key
    return "receipt"


async def test_config_is_disabled_by_default_and_null_normalizes() -> None:
    assert Config().personal_ops.enabled is False
    assert Config.model_validate({"personal_ops": None}).personal_ops == PersonalOpsConfig()


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": True, "room_id": "!briefs:localhost"},
        {"enabled": True, "github_repository": "owner/repository"},
        {"enabled": True, "github_repository": "not-scoped", "room_id": "!briefs:localhost"},
        {"timezone": "Not/AZone"},
    ],
)
async def test_invalid_or_incomplete_configuration_is_rejected(values) -> None:
    with pytest.raises(ValidationError):
        PersonalOpsConfig.model_validate(values)


async def test_enabled_service_fails_closed_without_runtime_dependencies(tmp_path) -> None:
    service = PersonalOpsService(runtime_paths=_runtime_paths(tmp_path), tool_invoker=None, executors=None)
    with pytest.raises(PersonalOpsError, match="read-tool invoker"):
        await service.reload(_enabled_config())
    assert service.enabled is False


async def test_reload_owns_resources_and_disable_closes_generation(tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    state_path = matrix_state_file(runtime_paths)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        yaml.safe_dump({"accounts": {"agent_user": {"access_token": "secret-token"}}}),
        encoding="utf-8",
    )
    service = PersonalOpsService(
        runtime_paths=runtime_paths,
        tool_invoker=_invoker,
        executors={"mindroom": _executor, "openclaw": _executor},
    )

    await service.reload(_enabled_config())
    assert service.enabled is True
    assert (runtime_paths.storage_root / "personal_ops" / "actions.sqlite3").is_file()
    assert (runtime_paths.storage_root / "personal_ops" / "approvals.sqlite3").is_file()
    assert (runtime_paths.storage_root / "personal_ops" / "briefs.sqlite3").is_file()

    await service.reload(PersonalOpsConfig())
    assert service.enabled is False
    await service.close()


async def test_failed_replacement_preserves_active_generation(tmp_path) -> None:
    runtime_paths = _runtime_paths(tmp_path)
    state_path = matrix_state_file(runtime_paths)
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        yaml.safe_dump({"accounts": {"agent_user": {"access_token": "secret-token"}}}),
        encoding="utf-8",
    )
    service = PersonalOpsService(
        runtime_paths=runtime_paths,
        tool_invoker=_invoker,
        executors={"mindroom": _executor, "openclaw": _executor},
    )
    await service.reload(_enabled_config())
    state_path.unlink()

    with pytest.raises(PersonalOpsError, match="access token"):
        await service.reload(_enabled_config(hour=9))
    assert service.enabled is True
    await service.close()


async def test_orchestrator_sync_binds_personal_ops_config(tmp_path) -> None:
    orchestrator = _MultiAgentOrchestrator(runtime_paths=_runtime_paths(tmp_path))
    config = Config()
    service = AsyncMock(spec=PersonalOpsService)
    orchestrator._personal_ops_service = service

    with (
        patch.object(orchestrator._knowledge_source_watcher, "sync", new=AsyncMock()),
        patch.object(orchestrator, "_sync_event_cache_service", new=AsyncMock()),
        patch.object(orchestrator, "_configure_approval_store_transport"),
        patch.object(orchestrator, "_sync_memory_auto_flush_worker", new=AsyncMock()),
        patch("mindroom.orchestrator.ensure_default_agent_workspaces"),
    ):
        await orchestrator._sync_runtime_support_services(config, start_watcher=False)

    service.reload.assert_awaited_once_with(config.personal_ops)
