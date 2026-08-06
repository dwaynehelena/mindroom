"""Tests for the skill installer (installer.py)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mindroom.tool_system.installer import (
    InstallError,
    InstallResult,
    SkillInstaller,
    ValidationError,
    validate_skill_dir,
)
from mindroom.tool_system.registry import (
    InstalledSkill,
    SkillMetadata,
    SkillRegistry,
    SkillVersionInfo,
)
from packaging.version import Version

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill(
    tmp_path: Path,
    name: str,
    version: str = "1.0.0",
    deps: dict[str, str] | None = None,
    extra_lines: list[str] | None = None,
    dest: Path | None = None,
) -> Path:
    """Create a skill directory with SKILL.md."""
    skill_dir = (dest or tmp_path) / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "---",
        f"name: {name}",
        f"description: A test skill",
        f"version: {version}",
    ]

    if deps:
        lines.append("openclaw:")
        lines.append("  depends_on:")
        lines.append("    skills:")
        for dep_name, constraint in deps.items():
            lines.append(f"      {dep_name}: {constraint!r}")

    if extra_lines:
        lines.extend(extra_lines)

    lines.append("---")
    lines.append("")
    lines.append("# Body")

    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")

    # Add a scripts directory for realism
    scripts_dir = skill_dir / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "run.sh").write_text("#!/bin/sh\necho hello", encoding="utf-8")

    return skill_dir


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    db_path = tmp_path / "test-registry.db"
    return SkillRegistry(db_path)


@pytest.fixture
def installer(tmp_path: Path, registry: SkillRegistry) -> SkillInstaller:
    skills_dir = tmp_path / "skills"
    installed_index = tmp_path / "installed.json"
    return SkillInstaller(registry, skills_dir=skills_dir, installed_index_path=installed_index)


# ---------------------------------------------------------------------------
# validate_skill_dir
# ---------------------------------------------------------------------------


class TestValidateSkillDir:
    def test_valid_skill(self, tmp_path: Path) -> None:
        skill_dir = _make_skill(tmp_path, "my-skill", "1.0.0")
        fm = validate_skill_dir(skill_dir)
        assert fm["name"] == "my-skill"
        assert fm["version"] == "1.0.0"

    def test_missing_directory(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError, match="does not exist"):
            validate_skill_dir(tmp_path / "nonexistent")

    def test_not_a_directory(self, tmp_path: Path) -> None:
        f = tmp_path / "file.txt"
        f.write_text("not a dir", encoding="utf-8")
        with pytest.raises(ValidationError, match="not a directory"):
            validate_skill_dir(f)

    def test_missing_skill_md(self, tmp_path: Path) -> None:
        d = tmp_path / "no-skill-md"
        d.mkdir()
        with pytest.raises(ValidationError, match="Missing SKILL.md"):
            validate_skill_dir(d)

    def test_invalid_frontmatter(self, tmp_path: Path) -> None:
        d = tmp_path / "bad-fm"
        d.mkdir()
        (d / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
        with pytest.raises(ValidationError, match="Invalid or missing frontmatter"):
            validate_skill_dir(d)

    def test_missing_name(self, tmp_path: Path) -> None:
        d = tmp_path / "no-name"
        d.mkdir()
        (d / "SKILL.md").write_text("---\ndescription: test\n---\n\nBody", encoding="utf-8")
        with pytest.raises(ValidationError, match="name is missing"):
            validate_skill_dir(d)

    def test_invalid_version(self, tmp_path: Path) -> None:
        d = tmp_path / "bad-ver"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: bad-ver\ndescription: test\nversion: not-a-version\n---\n\nBody",
            encoding="utf-8",
        )
        with pytest.raises(ValidationError, match="Invalid version"):
            validate_skill_dir(d)

    def test_version_optional(self, tmp_path: Path) -> None:
        d = tmp_path / "no-ver"
        d.mkdir()
        (d / "SKILL.md").write_text(
            "---\nname: no-ver\ndescription: test\n---\n\nBody",
            encoding="utf-8",
        )
        fm = validate_skill_dir(d)
        assert fm["name"] == "no-ver"
        assert "version" not in fm


# ---------------------------------------------------------------------------
# SkillInstaller.install
# ---------------------------------------------------------------------------


class TestInstall:
    def test_install_basic(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        result = installer.install(source)
        assert isinstance(result, InstallResult)
        assert result.name == "my-skill"
        assert result.version == Version("1.0.0")
        assert result.install_path.exists()
        assert (result.install_path / "SKILL.md").exists()
        assert (result.install_path / "scripts" / "run.sh").exists()

    def test_install_idempotent(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        result1 = installer.install(source)
        result2 = installer.install(source)
        assert result1.install_path == result2.install_path
        assert result1.version == result2.version

    def test_install_with_deps(self, installer: SkillInstaller, tmp_path: Path) -> None:
        # Create dependency skills in the skills dir (so they're in all_skill_roots)
        dep_source = _make_skill(tmp_path, "base-utils", "1.0.0")
        installer.install(dep_source)

        # Create a skill that depends on base-utils
        main_source = _make_skill(tmp_path, "advanced", "2.0.0", deps={"base-utils": ">=1.0.0"})
        result = installer.install(main_source)
        assert result.name == "advanced"
        assert result.version == Version("2.0.0")

    def test_install_dry_run(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        result = installer.install(source, dry_run=True)
        assert result.name == "my-skill"
        # Files should NOT have been copied
        assert not result.install_path.exists()

    def test_install_force(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        installer.install(source)
        # Install again with force
        result = installer.install(source, force=True)
        assert result.name == "my-skill"

    def test_install_invalid_skill(self, installer: SkillInstaller, tmp_path: Path) -> None:
        d = tmp_path / "invalid"
        d.mkdir()
        (d / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
        with pytest.raises(ValidationError):
            installer.install(d)

    def test_install_registers_in_db(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        installer.install(source)
        installed = installer._registry.get_installed("my-skill")
        assert installed is not None
        assert installed.version == Version("1.0.0")

    def test_install_updates_index_file(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        installer.install(source)
        index_path = installer._installed_index_path
        assert index_path.exists()
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert "my-skill" in data.get("skills", {})


# ---------------------------------------------------------------------------
# SkillInstaller.uninstall
# ---------------------------------------------------------------------------


class TestUninstall:
    def test_uninstall_removes_files(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        result = installer.install(source)
        assert result.install_path.exists()

        uninstalled = installer.uninstall("my-skill")
        assert uninstalled is True
        assert not result.install_path.exists()

    def test_uninstall_not_installed(self, installer: SkillInstaller) -> None:
        uninstalled = installer.uninstall("nonexistent")
        assert uninstalled is False

    def test_uninstall_dry_run(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        result = installer.install(source)
        uninstalled = installer.uninstall("my-skill", dry_run=True)
        assert uninstalled is True
        assert result.install_path.exists()  # Files should still be there

    def test_uninstall_clears_registry(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        installer.install(source)
        installer.uninstall("my-skill")
        assert installer._registry.get_installed("my-skill") is None


# ---------------------------------------------------------------------------
# SkillInstaller.update
# ---------------------------------------------------------------------------


class TestUpdate:
    def test_update_to_latest(self, installer: SkillInstaller, registry: SkillRegistry, tmp_path: Path) -> None:
        # Register two versions in the registry
        registry.register_skill(SkillMetadata(name="updatable", description="", author="", license=""))
        registry.add_version("updatable", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))
        registry.add_version("updatable", SkillVersionInfo(version=Version("2.0.0"), published_at=200.0))

        # Create the cache directory for v2 so install_from_registry can find it
        cache_dir = Path.home() / ".openclaw" / "skill-cache" / "updatable" / "2.0.0"
        cache_dir.mkdir(parents=True, exist_ok=True)
        # Write SKILL.md directly in the cache dir (not in a subdir)
        (cache_dir / "SKILL.md").write_text(
            "---\nname: updatable\ndescription: test\nversion: 2.0.0\n---\n\nBody",
            encoding="utf-8",
        )

        # Install v1 from a local source
        source = _make_skill(tmp_path, "updatable", "1.0.0")
        installer.install(source)

        # Update should find v2.0.0 as latest
        result = installer.update("updatable")
        assert result is not None
        assert result.version == Version("2.0.0")

    def test_update_already_latest(self, installer: SkillInstaller, registry: SkillRegistry, tmp_path: Path) -> None:
        registry.register_skill(SkillMetadata(name="latest", description="", author="", license=""))
        registry.add_version("latest", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))

        source = _make_skill(tmp_path, "latest", "1.0.0")
        installer.install(source)
        result = installer.update("latest")
        assert result is None  # Already up to date

    def test_update_not_installed(self, installer: SkillInstaller) -> None:
        with pytest.raises(InstallError, match="not installed"):
            installer.update("nonexistent")


# ---------------------------------------------------------------------------
# SkillInstaller.verify_installation
# ---------------------------------------------------------------------------


class TestVerifyInstallation:
    def test_verify_ok(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        installer.install(source)
        issues = installer.verify_installation("my-skill")
        assert issues == []

    def test_verify_not_installed(self, installer: SkillInstaller) -> None:
        issues = installer.verify_installation("nonexistent")
        assert len(issues) > 0
        assert "not registered" in issues[0]

    def test_verify_missing_files(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill", "1.0.0")
        result = installer.install(source)
        # Remove the install directory
        import shutil
        shutil.rmtree(result.install_path)
        issues = installer.verify_installation("my-skill")
        assert len(issues) > 0
        assert "does not exist" in issues[0]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_install_with_special_chars(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source = _make_skill(tmp_path, "my-skill_v2.0-beta", "2.0.0b1")
        result = installer.install(source)
        assert result.name == "my-skill_v2.0-beta"
        assert result.version == Version("2.0.0b1")

    def test_install_twice_different_version(self, installer: SkillInstaller, tmp_path: Path) -> None:
        source_v1 = _make_skill(tmp_path, "my-skill", "1.0.0")
        installer.install(source_v1)

        source_v2 = _make_skill(tmp_path, "my-skill", "2.0.0")
        result = installer.install(source_v2, force=True)
        assert result.version == Version("2.0.0")