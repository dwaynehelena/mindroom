"""Tests for the skill registry (registry.py)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mindroom.tool_system.registry import (
    InstalledSkill,
    RegistryError,
    RegistryIndex,
    SkillMetadata,
    SkillNotFoundError,
    SkillRegistry,
    SkillVersionInfo,
    VersionNotFoundError,
)
from packaging.version import Version

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    """Create a fresh registry in a temp directory."""
    db_path = tmp_path / "test-registry.db"
    return SkillRegistry(db_path)


# ---------------------------------------------------------------------------
# Skill CRUD
# ---------------------------------------------------------------------------


class TestSkillCRUD:
    def test_register_and_get(self, registry: SkillRegistry) -> None:
        meta = SkillMetadata(
            name="test-skill",
            description="A test skill",
            author="test-author",
            license="MIT",
            tags=("utility", "data"),
        )
        created = registry.register_skill(meta)
        assert created is True

        retrieved = registry.get_skill("test-skill")
        assert retrieved is not None
        assert retrieved.name == "test-skill"
        assert retrieved.description == "A test skill"
        assert retrieved.author == "test-author"
        assert retrieved.license == "MIT"
        assert retrieved.tags == ("utility", "data")

    def test_register_idempotent(self, registry: SkillRegistry) -> None:
        meta = SkillMetadata(name="dup", description="original", author="me", license="MIT")
        registry.register_skill(meta)
        created = registry.register_skill(meta)
        assert created is False  # Updated, not created

    def test_get_nonexistent(self, registry: SkillRegistry) -> None:
        assert registry.get_skill("nonexistent") is None

    def test_delete(self, registry: SkillRegistry) -> None:
        meta = SkillMetadata(name="delete-me", description="gone", author="me", license="MIT")
        registry.register_skill(meta)
        assert registry.get_skill("delete-me") is not None
        deleted = registry.delete_skill("delete-me")
        assert deleted is True
        assert registry.get_skill("delete-me") is None

    def test_delete_nonexistent(self, registry: SkillRegistry) -> None:
        deleted = registry.delete_skill("nonexistent")
        assert deleted is False

    def test_list_skills(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="a", description="", author="", license=""))
        registry.register_skill(SkillMetadata(name="b", description="", author="", license=""))
        skills = registry.list_skills()
        assert len(skills) == 2
        assert [s.name for s in skills] == ["a", "b"]

    def test_search_skills(self, registry: SkillRegistry) -> None:
        registry.register_skill(
            SkillMetadata(name="data-processor", description="Processes data", author="alice", license="MIT")
        )
        registry.register_skill(
            SkillMetadata(name="web-scraper", description="Scrapes websites", author="bob", license="Apache")
        )
        results = registry.search_skills("data")
        assert len(results) == 1
        assert results[0].name == "data-processor"

        results = registry.search_skills("scrapes")
        assert len(results) == 1
        assert results[0].name == "web-scraper"


# ---------------------------------------------------------------------------
# Version management
# ---------------------------------------------------------------------------


class TestVersionManagement:
    def test_add_version(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="my-skill", description="", author="", license=""))
        info = SkillVersionInfo(version=Version("1.2.3"), published_at=time.time())
        created = registry.add_version("my-skill", info)
        assert created is True

        versions = registry.get_versions("my-skill")
        assert len(versions) == 1
        assert versions[0].version == Version("1.2.3")

    def test_add_version_nonexistent_skill(self, registry: SkillRegistry) -> None:
        info = SkillVersionInfo(version=Version("1.0.0"), published_at=time.time())
        with pytest.raises(SkillNotFoundError):
            registry.add_version("nonexistent", info)

    def test_add_version_idempotent(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="my-skill", description="", author="", license=""))
        info = SkillVersionInfo(version=Version("1.0.0"), published_at=time.time())
        registry.add_version("my-skill", info)
        created = registry.add_version("my-skill", info)
        assert created is False

    def test_multiple_versions(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="my-skill", description="", author="", license=""))
        registry.add_version("my-skill", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))
        registry.add_version("my-skill", SkillVersionInfo(version=Version("2.0.0"), published_at=200.0))
        registry.add_version("my-skill", SkillVersionInfo(version=Version("1.5.0"), published_at=150.0))

        versions = registry.get_versions("my-skill")
        assert len(versions) == 3
        # Should be ordered newest first
        assert [str(v.version) for v in versions] == ["2.0.0", "1.5.0", "1.0.0"]

    def test_get_latest_version(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="my-skill", description="", author="", license=""))
        registry.add_version("my-skill", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))
        registry.add_version("my-skill", SkillVersionInfo(version=Version("2.0.0"), published_at=200.0))

        latest = registry.get_latest_version("my-skill")
        assert latest is not None
        assert latest.version == Version("2.0.0")

    def test_get_latest_no_versions(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="empty", description="", author="", license=""))
        assert registry.get_latest_version("empty") is None

    def test_delete_version(self, registry: SkillRegistry) -> None:
        registry.register_skill(SkillMetadata(name="my-skill", description="", author="", license=""))
        registry.add_version("my-skill", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))
        deleted = registry.delete_version("my-skill", "1.0.0")
        assert deleted is True
        assert registry.get_versions("my-skill") == []

    def test_delete_version_nonexistent(self, registry: SkillRegistry) -> None:
        deleted = registry.delete_version("my-skill", "1.0.0")
        assert deleted is False


# ---------------------------------------------------------------------------
# Install tracking
# ---------------------------------------------------------------------------


class TestInstallTracking:
    def test_mark_installed(self, registry: SkillRegistry) -> None:
        skill = InstalledSkill(
            name="my-skill",
            version=Version("1.2.3"),
            source="registry",
            install_path=Path("/tmp/skills/my-skill"),
            installed_at=time.time(),
        )
        created = registry.mark_installed(skill)
        assert created is True

        retrieved = registry.get_installed("my-skill")
        assert retrieved is not None
        assert retrieved.name == "my-skill"
        assert retrieved.version == Version("1.2.3")
        assert retrieved.source == "registry"

    def test_mark_installed_idempotent(self, registry: SkillRegistry) -> None:
        skill = InstalledSkill(
            name="my-skill",
            version=Version("1.0.0"),
            source="registry",
            install_path=Path("/tmp/skills/my-skill"),
            installed_at=time.time(),
        )
        registry.mark_installed(skill)
        created = registry.mark_installed(skill)
        assert created is False

    def test_mark_uninstalled(self, registry: SkillRegistry) -> None:
        skill = InstalledSkill(
            name="my-skill",
            version=Version("1.0.0"),
            source="registry",
            install_path=Path("/tmp/skills/my-skill"),
            installed_at=time.time(),
        )
        registry.mark_installed(skill)
        removed = registry.mark_uninstalled("my-skill")
        assert removed is True
        assert registry.get_installed("my-skill") is None

    def test_list_installed(self, registry: SkillRegistry) -> None:
        registry.mark_installed(
            InstalledSkill(name="a", version=Version("1.0.0"), source="registry",
                           install_path=Path("/a"), installed_at=1.0)
        )
        registry.mark_installed(
            InstalledSkill(name="b", version=Version("2.0.0"), source="user",
                           install_path=Path("/b"), installed_at=2.0)
        )
        installed = registry.list_installed()
        assert len(installed) == 2
        assert [s.name for s in installed] == ["a", "b"]

    def test_set_enabled(self, registry: SkillRegistry) -> None:
        registry.mark_installed(
            InstalledSkill(name="my-skill", version=Version("1.0.0"), source="registry",
                           install_path=Path("/p"), installed_at=1.0, enabled=True)
        )
        changed = registry.set_enabled("my-skill", False)
        assert changed is True
        retrieved = registry.get_installed("my-skill")
        assert retrieved is not None
        assert retrieved.enabled is False


# ---------------------------------------------------------------------------
# Index import/export
# ---------------------------------------------------------------------------


class TestIndexImportExport:
    def test_import_index(self, registry: SkillRegistry) -> None:
        index = RegistryIndex(
            format_version=1,
            updated=time.time(),
            skills={
                "skill-a": SkillMetadata(name="skill-a", description="A", author="me", license="MIT"),
                "skill-b": SkillMetadata(name="skill-b", description="B", author="you", license="Apache"),
            },
            versions={
                "skill-a": [SkillVersionInfo(version=Version("1.0.0"), published_at=100.0)],
                "skill-b": [SkillVersionInfo(version=Version("2.0.0"), published_at=200.0)],
            },
            latest={"skill-a": "1.0.0", "skill-b": "2.0.0"},
        )
        count = registry.import_index(index)
        assert count == 2

        assert registry.get_skill("skill-a") is not None
        assert registry.get_skill("skill-b") is not None
        assert len(registry.get_versions("skill-a")) == 1

    def test_export_index(self, registry: SkillRegistry) -> None:
        registry.register_skill(
            SkillMetadata(name="test", description="desc", author="me", license="MIT", tags=("tag1",))
        )
        registry.add_version("test", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))

        exported = registry.export_index()
        assert exported.format_version == 1
        assert "test" in exported.skills
        assert "test" in exported.versions
        assert exported.latest.get("test") == "1.0.0"


# ---------------------------------------------------------------------------
# Transaction support
# ---------------------------------------------------------------------------


class TestTransaction:
    def test_transaction_commit(self, registry: SkillRegistry) -> None:
        with registry.transaction() as tx:
            tx.register_skill(SkillMetadata(name="tx-skill", description="", author="", license=""))

        assert registry.get_skill("tx-skill") is not None

    def test_transaction_rollback(self, registry: SkillRegistry) -> None:
        try:
            with registry.transaction() as tx:
                tx.register_skill(SkillMetadata(name="rollback-skill", description="", author="", license=""))
                raise RuntimeError("force rollback")
        except RuntimeError:
            pass

        assert registry.get_skill("rollback-skill") is None

    def test_transaction_nested_operations(self, registry: SkillRegistry) -> None:
        with registry.transaction() as tx:
            tx.register_skill(SkillMetadata(name="parent", description="", author="", license=""))
            tx.add_version("parent", SkillVersionInfo(version=Version("1.0.0"), published_at=1.0))
            tx.mark_installed(
                InstalledSkill(name="parent", version=Version("1.0.0"), source="registry",
                               install_path=Path("/p"), installed_at=1.0)
            )

        assert registry.get_skill("parent") is not None
        assert len(registry.get_versions("parent")) == 1
        assert registry.get_installed("parent") is not None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_tags(self, registry: SkillRegistry) -> None:
        meta = SkillMetadata(name="no-tags", description="", author="", license="")
        registry.register_skill(meta)
        retrieved = registry.get_skill("no-tags")
        assert retrieved is not None
        assert retrieved.tags == ()

    def test_special_chars_in_name(self, registry: SkillRegistry) -> None:
        meta = SkillMetadata(name="my-skill_v2.0", description="", author="", license="")
        registry.register_skill(meta)
        assert registry.get_skill("my-skill_v2.0") is not None

    def test_vacuum(self, registry: SkillRegistry) -> None:
        # Vacuum should not raise
        registry.vacuum()

    def test_close(self, registry: SkillRegistry) -> None:
        # Close should not raise
        registry.close()