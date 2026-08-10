"""Tests for the skill index (index.py)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mindroom.tool_system.index import (
    IndexConfig,
    IndexError,
    InstalledSkillsIndex,
    RegistryIndexFetcher,
    SkillIndex,
    SkillSearchResult,
)
from mindroom.tool_system.registry import (
    RegistryIndex,
    SkillMetadata,
    SkillRegistry,
    SkillVersionInfo,
)
from packaging.version import Version

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    db_path = tmp_path / "test-registry.db"
    return SkillRegistry(db_path)


@pytest.fixture
def registry_with_data(registry: SkillRegistry) -> SkillRegistry:
    """Populate the registry with sample skills."""
    registry.register_skill(
        SkillMetadata(name="data-processor", description="Processes data files", author="alice",
                      license="MIT", tags=("utility", "data"))
    )
    registry.add_version("data-processor", SkillVersionInfo(version=Version("1.0.0"), published_at=100.0))
    registry.add_version("data-processor", SkillVersionInfo(version=Version("1.5.0"), published_at=150.0))

    registry.register_skill(
        SkillMetadata(name="web-scraper", description="Scrapes websites", author="bob",
                      license="Apache", tags=("web", "scraping"))
    )
    registry.add_version("web-scraper", SkillVersionInfo(version=Version("2.0.0"), published_at=200.0))

    registry.register_skill(
        SkillMetadata(name="image-tool", description="Image manipulation", author="alice",
                      license="MIT", tags=("media", "image"))
    )
    registry.add_version("image-tool", SkillVersionInfo(version=Version("0.5.0"), published_at=50.0))

    return registry


# ---------------------------------------------------------------------------
# InstalledSkillsIndex
# ---------------------------------------------------------------------------


class TestInstalledSkillsIndex:
    def test_read_empty(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        assert index.read() == {}

    def test_add_and_read(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        index.add("my-skill", "1.0.0", source="registry", install_path="/tmp/skills/my-skill")
        data = index.read()
        assert "my-skill" in data
        assert data["my-skill"]["version"] == "1.0.0"

    def test_remove(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        index.add("my-skill", "1.0.0")
        removed = index.remove("my-skill")
        assert removed is True
        assert index.read() == {}

    def test_remove_nonexistent(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        removed = index.remove("nonexistent")
        assert removed is False

    def test_get(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        index.add("my-skill", "1.0.0")
        info = index.get("my-skill")
        assert info is not None
        assert info["version"] == "1.0.0"

    def test_get_nonexistent(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        assert index.get("nonexistent") is None

    def test_list_installed(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        index.add("a", "1.0.0")
        index.add("b", "2.0.0")
        items = index.list_installed()
        assert len(items) == 2

    def test_write_preserves_structure(self, tmp_path: Path) -> None:
        index = InstalledSkillsIndex(tmp_path / "installed.json")
        index.add("my-skill", "1.0.0")
        raw = json.loads((tmp_path / "installed.json").read_text(encoding="utf-8"))
        assert "skills" in raw
        assert "my-skill" in raw["skills"]


# ---------------------------------------------------------------------------
# RegistryIndexFetcher
# ---------------------------------------------------------------------------


class TestRegistryIndexFetcher:
    def test_fetch_from_http(self) -> None:
        """Test with a mock HTTP fetcher."""
        index_data = {
            "formatVersion": 1,
            "updated": "2026-08-04T12:00:00Z",
            "skills": {
                "my-skill": {
                    "name": "my-skill",
                    "description": "A test skill",
                    "author": "test",
                    "license": "MIT",
                    "tags": ["utility"],
                    "versions": {
                        "1.0.0": {"ref": "my-skill@1.0.0", "sha256": "abc", "published": "2026-08-01T10:00:00Z"},
                    },
                    "latest": "1.0.0",
                }
            },
        }

        def mock_fetch(url: str) -> bytes:
            return json.dumps(index_data).encode("utf-8")

        config = IndexConfig(registry_url="http://localhost:9999", cache_ttl_seconds=0)
        fetcher = RegistryIndexFetcher(config, http_fetcher=mock_fetch)
        index = fetcher.fetch()
        assert index.format_version == 1
        assert "my-skill" in index.skills
        assert index.skills["my-skill"].name == "my-skill"
        assert "my-skill" in index.versions
        assert len(index.versions["my-skill"]) == 1
        assert index.latest["my-skill"] == "1.0.0"

    def test_fetch_fallback_to_cache(self, tmp_path: Path) -> None:
        """When HTTP fails, should fall back to disk cache."""
        # Write a cache file
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        cache_file = cache_dir / "index.json"
        cache_data = {
            "formatVersion": 1,
            "updated": "2026-08-04T12:00:00Z",
            "skills": {
                "cached-skill": {
                    "name": "cached-skill",
                    "description": "From cache",
                    "author": "cache",
                    "license": "MIT",
                    "versions": {},
                    "latest": "0.0.0",
                }
            },
        }
        cache_file.write_text(json.dumps(cache_data), encoding="utf-8")

        config = IndexConfig(
            registry_url="http://localhost:1",
            cache_path=cache_dir,
            cache_ttl_seconds=0,
        )

        def failing_fetch(url: str) -> bytes:
            raise ConnectionError("Connection refused")

        fetcher = RegistryIndexFetcher(config, http_fetcher=failing_fetch)
        index = fetcher.fetch()
        assert "cached-skill" in index.skills

    def test_fetch_empty_on_all_failures(self, tmp_path: Path) -> None:
        """When both HTTP and disk cache fail, return empty index."""
        config = IndexConfig(
            registry_url="http://localhost:1",
            cache_path=tmp_path / "nonexistent",
            cache_ttl_seconds=0,
        )

        def failing_fetch(url: str) -> bytes:
            raise ConnectionError("Connection refused")

        fetcher = RegistryIndexFetcher(config, http_fetcher=failing_fetch)
        index = fetcher.fetch()
        assert index.skills == {}

    def test_clear_cache(self, tmp_path: Path) -> None:
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir(parents=True)
        cache_file = cache_dir / "index.json"
        cache_file.write_text("{}", encoding="utf-8")

        config = IndexConfig(cache_path=cache_dir)
        fetcher = RegistryIndexFetcher(config)
        fetcher.clear_cache()
        assert not cache_file.exists()


# ---------------------------------------------------------------------------
# SkillIndex
# ---------------------------------------------------------------------------


class TestSkillIndex:
    def _make_index(
        self,
        registry: SkillRegistry,
        tmp_path: Path,
        installed_index: InstalledSkillsIndex | None = None,
    ) -> SkillIndex:
        """Build a ``SkillIndex`` fully scoped to the fixture's ``tmp_path``.

        ``SkillIndex`` defaults its ``index_fetcher`` to a
        ``RegistryIndexFetcher()`` that reads the user's real home cache
        (``~/.openclaw/skill-cache``) and its ``installed_index`` to the user's
        real installed-skills file.  That leaks whatever skills exist in the
        developer's home directory into ``search()`` results, so the tests must
        scope both the fetcher and the installed index to the isolated fixture
        directory.  This helper is the single place that does so.
        """
        from mindroom.tool_system.index import IndexConfig, RegistryIndexFetcher

        fetcher = RegistryIndexFetcher(
            IndexConfig(
                cache_path=tmp_path / "skill-cache",
                cache_ttl_seconds=0,
            ),
        )
        return SkillIndex(
            registry,
            index_fetcher=fetcher,
            installed_index=installed_index or InstalledSkillsIndex(tmp_path / "installed.json"),
        )

    def test_search_all(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        """Search with no query returns all skills."""
        index = self._make_index(registry_with_data, tmp_path)
        results = index.search()
        assert len(results) == 3

    def test_search_by_name(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        results = index.search(query="data")
        assert len(results) >= 1
        assert all("data" in r.name.lower() or "data" in r.description.lower() for r in results)

    def test_search_by_author(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        results = index.search(author="bob")
        assert len(results) == 1
        assert results[0].name == "web-scraper"

    def test_search_by_tag(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        results = index.search(tags=["utility"])
        assert len(results) == 1
        assert results[0].name == "data-processor"

    def test_search_installed_only(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        # No skills are installed yet
        results = index.search(include_installed_only=True)
        assert results == []

    def test_search_with_installed(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        """Test that installed status is reflected in results."""
        installed_index_path = tmp_path / "installed.json"
        installed_index = InstalledSkillsIndex(installed_index_path)
        installed_index.add("data-processor", "1.0.0", install_path="/tmp/skills/data-processor")

        index = self._make_index(registry_with_data, tmp_path, installed_index=installed_index)
        results = index.search()
        dp = next(r for r in results if r.name == "data-processor")
        assert dp.installed_version == "1.0.0"
        assert dp.installed_path == Path("/tmp/skills/data-processor")

    def test_list_installed(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        installed_index = InstalledSkillsIndex(tmp_path / "installed.json")
        installed_index.add("data-processor", "1.0.0")

        index = self._make_index(registry_with_data, tmp_path, installed_index=installed_index)
        results = index.list_installed()
        assert len(results) == 1
        assert results[0].name == "data-processor"

    def test_list_updatable(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        installed_index = InstalledSkillsIndex(tmp_path / "installed.json")
        # Install an older version
        installed_index.add("data-processor", "0.5.0")

        index = self._make_index(registry_with_data, tmp_path, installed_index=installed_index)
        results = index.list_updatable()
        assert len(results) == 1
        assert results[0].name == "data-processor"
        assert results[0].is_up_to_date is False

    def test_get_detail(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        detail = index.get_detail("data-processor")
        assert detail is not None
        assert detail.name == "data-processor"
        assert detail.latest_version == "1.5.0"
        assert len(detail.all_versions) == 2

    def test_get_detail_not_found(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        assert index.get_detail("nonexistent") is None

    def test_refresh(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        result = index.refresh()
        assert isinstance(result, RegistryIndex)

    def test_clear_cache(self, registry_with_data: SkillRegistry, tmp_path: Path) -> None:
        index = self._make_index(registry_with_data, tmp_path)
        # Should not raise
        index.clear_cache()


# ---------------------------------------------------------------------------
# SkillSearchResult
# ---------------------------------------------------------------------------


class TestSkillSearchResult:
    def test_defaults(self) -> None:
        result = SkillSearchResult(
            name="test",
            description="desc",
            author="me",
            license="MIT",
            tags=(),
            latest_version="1.0.0",
            all_versions=("1.0.0",),
            installed_version=None,
            installed_path=None,
        )
        assert result.name == "test"
        assert result.installed_version is None
        assert result.is_up_to_date is True

    def test_not_up_to_date(self) -> None:
        result = SkillSearchResult(
            name="test",
            description="desc",
            author="me",
            license="MIT",
            tags=(),
            latest_version="2.0.0",
            all_versions=("1.0.0", "2.0.0"),
            installed_version="1.0.0",
            installed_path=Path("/p"),
            is_up_to_date=False,
        )
        assert result.is_up_to_date is False
        assert result.installed_version == "1.0.0"