"""Tests for Item 0: Agent Mesh config feature-flag schema."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from mindroom.config.mesh import (
    MESH_GATEWAY_MODE_ENV,
    MeshConfig,
    MeshRuntimeMode,
    resolve_mesh_runtime_mode,
)
from mindroom.config.main import Config


class TestMeshConfigSchema:
    """Schema-level tests for the MeshConfig section."""

    def test_default_mode_is_gateway_only(self):
        cfg = MeshConfig()
        assert cfg.mode is MeshRuntimeMode.GATEWAY_ONLY
        assert cfg.mode.value == "gateway_only"

    def test_tool_state_disabled_by_default(self):
        cfg = MeshConfig()
        assert cfg.tool_state.enabled is False

    def test_enrollment_session_cancellation_cursor_defaults_off(self):
        cfg = MeshConfig()
        assert cfg.enrollment.enabled is False
        assert cfg.session_mapping.enabled is False
        assert cfg.cancellation.enabled is False
        assert cfg.cursor.enabled is False

    def test_loop_defaults(self):
        cfg = MeshConfig()
        assert cfg.loop.enabled is False
        assert cfg.loop.max_hops == 8
        assert cfg.loop.ttl_seconds == 300

    def test_extra_forbid_rejects_typo(self):
        with pytest.raises(ValidationError):
            MeshConfig.model_validate({"tool_statee": {"enabled": True}})

    def test_extra_forbid_rejects_unknown_top_level(self):
        with pytest.raises(ValidationError):
            MeshConfig.model_validate({"bogus": 1})

    def test_extra_forbid_rejects_unknown_nested(self):
        with pytest.raises(ValidationError):
            MeshConfig.model_validate({"loop": {"max_hop": 5}})

    def test_explicit_mode_accepted(self):
        cfg = MeshConfig.model_validate({"mode": "full"})
        assert cfg.mode is MeshRuntimeMode.FULL

    def test_sub_flag_overrides(self):
        cfg = MeshConfig.model_validate(
            {
                "tool_state": {"enabled": True},
                "loop": {"enabled": True, "max_hops": 4, "ttl_seconds": 60},
            },
        )
        assert cfg.tool_state.enabled is True
        assert cfg.loop.enabled is True
        assert cfg.loop.max_hops == 4
        assert cfg.loop.ttl_seconds == 60

    def test_wired_into_root_config_default(self):
        config = Config.model_validate({})
        assert isinstance(config.mesh, MeshConfig)
        assert config.mesh.mode is MeshRuntimeMode.GATEWAY_ONLY

    def test_root_config_rejects_mesh_typo(self):
        with pytest.raises(ValidationError):
            Config.model_validate({"mesh": {"lopp": {}}})


class TestResolveMeshRuntimeMode:
    """Precedence tests for the file/env/default resolver."""

    def test_default_is_gateway_only(self):
        cfg = MeshConfig()
        assert resolve_mesh_runtime_mode(cfg, env={}) is MeshRuntimeMode.GATEWAY_ONLY

    def test_env_gateway_only(self):
        cfg = MeshConfig()
        result = resolve_mesh_runtime_mode(cfg, env={MESH_GATEWAY_MODE_ENV: "gateway_only"})
        assert result is MeshRuntimeMode.GATEWAY_ONLY

    def test_env_hyphen_accepted(self):
        cfg = MeshConfig()
        result = resolve_mesh_runtime_mode(cfg, env={MESH_GATEWAY_MODE_ENV: "gateway-only"})
        assert result is MeshRuntimeMode.GATEWAY_ONLY

    def test_env_full(self):
        cfg = MeshConfig()
        result = resolve_mesh_runtime_mode(cfg, env={MESH_GATEWAY_MODE_ENV: "full"})
        assert result is MeshRuntimeMode.FULL

    def test_unknown_env_defaults_gateway_only(self):
        cfg = MeshConfig()
        result = resolve_mesh_runtime_mode(cfg, env={MESH_GATEWAY_MODE_ENV: "bogus"})
        assert result is MeshRuntimeMode.GATEWAY_ONLY

    def test_file_mode_wins_over_env(self):
        cfg = MeshConfig.model_validate({"mode": "gateway_only"})
        result = resolve_mesh_runtime_mode(cfg, env={MESH_GATEWAY_MODE_ENV: "full"})
        assert result is MeshRuntimeMode.GATEWAY_ONLY

    def test_file_full_wins_over_env(self):
        cfg = MeshConfig.model_validate({"mode": "full"})
        result = resolve_mesh_runtime_mode(cfg, env={MESH_GATEWAY_MODE_ENV: "gateway_only"})
        assert result is MeshRuntimeMode.FULL

    def test_accepts_root_config(self):
        config = Config.model_validate({"mesh": {"mode": "full"}})
        result = resolve_mesh_runtime_mode(config, env={MESH_GATEWAY_MODE_ENV: "gateway_only"})
        assert result is MeshRuntimeMode.FULL

    def test_root_config_default_with_env(self):
        config = Config.model_validate({})
        result = resolve_mesh_runtime_mode(config, env={MESH_GATEWAY_MODE_ENV: "gateway_only"})
        assert result is MeshRuntimeMode.GATEWAY_ONLY

    def test_rejects_non_mesh_type(self):
        with pytest.raises(TypeError):
            resolve_mesh_runtime_mode(object(), env={})