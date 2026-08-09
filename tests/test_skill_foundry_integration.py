"""Tests for the Skill Foundry integration layer.

Covers the ``skill_foundry`` config section, the registry-cache skill root,
dependency validation inside ``build_agent_skills``, the ``WorkerSpec``
``skill_mounts`` field, and the local worker skill-mount projection.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

import mindroom.tool_system.skills as skills_module
from mindroom.config.agent import AgentConfig
from mindroom.config.main import Config
from mindroom.constants import RuntimePaths, resolve_runtime_paths
from mindroom.tool_system.skills import build_agent_skills
from mindroom.workers.backends.local import _project_skill_mounts, local_worker_state_paths_for_root
from mindroom.workers.models import WorkerSpec


def _runtime_paths(storage_path: Path, *, config_path: Path | None = None) -> RuntimePaths:
    return resolve_runtime_paths(
        config_path=config_path or storage_path / "config.yaml",
        storage_path=storage_path,
    )


def _write_skill(
    root: Path,
    name: str,
    description: str,
    *,
    version: str = "1.0.0",
    deps: dict[str, str] | None = None,
) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}", f"version: {version}"]
    if deps:
        lines.append("openclaw:")
        lines.append("  depends_on:")
        lines.append("    skills:")
        for dep_name, constraint in deps.items():
            lines.append(f"      {dep_name}: {constraint!r}")
    lines.append("---")
    lines.append("")
    lines.append("# Body")
    skill_path = skill_dir / "SKILL.md"
    skill_path.write_text("\n".join(lines), encoding="utf-8")
    return skill_path


def _base_config(skills: list[str]) -> Config:
    return Config(
        agents={
            "code": AgentConfig(display_name="Code", role="", tools=["file"], skills=skills),
        },
    )


def _skill_names(skills: object | None) -> list[str]:
    return skills.get_skill_names() if skills is not None else []


# ---------------------------------------------------------------------------
# skill_foundry config section
# ---------------------------------------------------------------------------


class TestSkillFoundryConfig:
    def test_defaults(self) -> None:
        config = Config()
        assert config.skill_foundry.enabled is True
        assert config.skill_foundry.registry.url == "https://skills.openclaw.ai"
        assert config.skill_foundry.auto_update is True
        assert config.skill_foundry.update_interval_minutes == 60

    def test_authored_section(self) -> None:
        config = Config.model_validate(
            {
                "skill_foundry": {
                    "enabled": False,
                    "registry": {"url": "https://example.com/registry"},
                    "auto_update": False,
                    "update_interval_minutes": 30,
                    "cache_root": "/tmp/custom-cache",
                },
            },
        )
        assert config.skill_foundry.enabled is False
        assert config.skill_foundry.registry.url == "https://example.com/registry"
        assert config.skill_foundry.auto_update is False
        assert config.skill_foundry.update_interval_minutes == 30
        assert config.skill_foundry.cache_root == Path("/tmp/custom-cache")

    def test_rejects_non_http_registry_url(self) -> None:
        with pytest.raises(ValidationError):
            Config.model_validate({"skill_foundry": {"registry": {"url": "not-a-url"}}})


# ---------------------------------------------------------------------------
# registry-cache skill root
# ---------------------------------------------------------------------------


class TestRegistryCacheSkillRoot:
    def test_root_origin_classification(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            skills_module,
            "get_registry_cache_dir",
            lambda: tmp_path / "skills-cache",
        )
        cache_root = tmp_path / "skills-cache"
        cache_root.mkdir()
        _write_skill(cache_root, "cached", "A cached skill")

        listings = skills_module.list_skill_listings([cache_root])
        assert len(listings) == 1
        assert listings[0].name == "cached"
        assert listings[0].origin == "registry-cache"

    def test_registry_cache_appended_lowest_priority(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cache_root = tmp_path / "skills-cache"
        cache_root.mkdir()
        _write_skill(cache_root, "shared", "cached v1")

        user_root = tmp_path / "user"
        user_root.mkdir()
        _write_skill(user_root, "shared", "user v2")

        monkeypatch.setattr(skills_module, "get_user_skills_dir", lambda: user_root)
        monkeypatch.setattr(skills_module, "get_registry_cache_dir", lambda: cache_root)
        # Suppress bundled + plugin roots for deterministic precedence.
        monkeypatch.setattr(skills_module, "_get_bundled_skills_dir", lambda: tmp_path / "bundled")
        monkeypatch.setattr(skills_module, "_PLUGIN_SKILL_ROOTS", [])

        config = _base_config(["shared"])
        skills = build_agent_skills(
            "code",
            config,
            _runtime_paths(tmp_path / "storage"),
            # Explicit roots are caller-ordered with later roots winning;
            # place the lowest-priority cache root first so user wins.
            skill_roots=[cache_root, user_root],
            env_vars={},
            credential_keys=set(),
        )
        assert _skill_names(skills) == ["shared"]
        assert skills.get_skill("shared").description == "user v2"


# ---------------------------------------------------------------------------
# dependency validation in build_agent_skills
# ---------------------------------------------------------------------------


class TestBuildAgentSkillDependencies:
    def test_missing_declared_dependency_fails_fast(self, tmp_path: Path) -> None:
        root = tmp_path / "roots"
        root.mkdir()
        _write_skill(root, "consumer", "consumer", deps={"missing-lib": ">=1.0.0"})

        from mindroom.tool_system.skill_deps import DependencyError

        config = _base_config(["consumer"])
        with pytest.raises(DependencyError, match="missing-lib"):
            build_agent_skills(
                "code",
                config,
                _runtime_paths(tmp_path / "storage"),
                skill_roots=[root],
                env_vars={},
                credential_keys=set(),
            )

    def test_satisfied_dependency_builds(self, tmp_path: Path) -> None:
        root = tmp_path / "roots"
        root.mkdir()
        _write_skill(root, "lib", "lib", version="1.2.0")
        _write_skill(root, "consumer", "consumer", deps={"lib": ">=1.0.0"})

        config = _base_config(["consumer"])
        skills = build_agent_skills(
            "code",
            config,
            _runtime_paths(tmp_path / "storage"),
            skill_roots=[root],
            env_vars={},
            credential_keys=set(),
        )
        assert _skill_names(skills) == ["consumer"]

    def test_workspace_only_entry_skill_is_not_rejected(self, tmp_path: Path) -> None:
        storage = tmp_path / "storage"
        root = tmp_path / "roots"
        root.mkdir()
        # Entry skill only exists in the workspace loader.
        workspace = storage / "workspace"
        _write_skill(workspace, "ws-skill", "workspace only")

        from mindroom.tool_system.worker_routing import agent_workspace_root_path

        ws_root = agent_workspace_root_path(storage, "code") / "skills"
        _write_skill(ws_root, "ws-skill", "workspace only")

        config = _base_config(["ws-skill"])
        skills = build_agent_skills(
            "code",
            config,
            _runtime_paths(storage),
            skill_roots=[root],
            env_vars={},
            credential_keys=set(),
        )
        assert _skill_names(skills) == ["ws-skill"]

    def test_skill_foundry_disabled_skips_validation(self, tmp_path: Path) -> None:
        root = tmp_path / "roots"
        root.mkdir()
        _write_skill(root, "consumer", "consumer", deps={"missing-lib": ">=1.0.0"})

        config = _base_config(["consumer"])
        config.skill_foundry.enabled = False

        skills = build_agent_skills(
            "code",
            config,
            _runtime_paths(tmp_path / "storage"),
            skill_roots=[root],
            env_vars={},
            credential_keys=set(),
        )
        # Without the foundry gate the skill loads even though its declared
        # dependency is unsatisfiable.
        assert _skill_names(skills) == ["consumer"]


# ---------------------------------------------------------------------------
# WorkerSpec.skill_mounts + local projection
# ---------------------------------------------------------------------------


class TestWorkerSkillMounts:
    def test_worker_spec_accepts_skill_mounts(self, tmp_path: Path) -> None:
        spec = WorkerSpec("k", skill_mounts={"alpha": tmp_path / "alpha"})
        assert spec.skill_mounts == {"alpha": tmp_path / "alpha"}
        assert spec.worker_key == "k"

    def test_local_projection_mounts_real_skills(self, tmp_path: Path) -> None:
        root = tmp_path / "worker"
        paths = local_worker_state_paths_for_root(root)
        paths.workspace.mkdir(parents=True)

        source = tmp_path / "src" / "alpha"
        source.mkdir(parents=True)
        (source / "SKILL.md").write_text("# alpha", encoding="utf-8")
        (source / "scripts").mkdir()

        _project_skill_mounts(paths, {"alpha": source, "bad": tmp_path / "missing"})

        link = paths.workspace / "skills" / "alpha"
        assert link.is_symlink()
        assert link.resolve() == source.resolve()
        assert not (paths.workspace / "skills" / "bad").exists()

    def test_local_projection_idempotent(self, tmp_path: Path) -> None:
        root = tmp_path / "worker"
        paths = local_worker_state_paths_for_root(root)
        paths.workspace.mkdir(parents=True)

        source = tmp_path / "src" / "alpha"
        source.mkdir(parents=True)

        _project_skill_mounts(paths, {"alpha": source})
        first_link = paths.workspace / "skills" / "alpha"
        assert first_link.is_symlink()
        inode_before = first_link.lstat().st_ino

        _project_skill_mounts(paths, {"alpha": source})
        assert first_link.is_symlink()
        assert first_link.lstat().st_ino == inode_before