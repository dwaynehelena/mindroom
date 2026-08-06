"""Searchable index for discovering available skills.

The index module provides:

1. **Local index** — reads/writes ``installed-skills.json`` for tracking
   what's currently installed
2. **Registry index** — fetches and caches the remote ``index.json`` from
   the configured registry URL
3. **Search** — filters skills by name, description, author, tags, and
   version constraints
4. **Discovery** — lists all available skills from the registry, with
   install status overlays

The index is designed to work both online (fetching from a remote registry)
and offline (using cached data).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from mindroom.tool_system.registry import (
    InstalledSkill,
    RegistryIndex,
    SkillMetadata,
    SkillRegistry,
    SkillVersionInfo,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class IndexError(RuntimeError):
    """Base exception for index operations."""


class FetchError(IndexError):
    """Failed to fetch the registry index."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillSearchResult:
    """A skill as presented in search results, with install status."""

    name: str
    description: str
    author: str
    license: str
    tags: tuple[str, ...]
    latest_version: str
    all_versions: tuple[str, ...]
    installed_version: str | None  # None = not installed
    installed_path: Path | None
    is_up_to_date: bool = True


@dataclass(frozen=True, slots=True)
class IndexConfig:
    """Configuration for the skill index."""

    registry_url: str = "https://skills.openclaw.ai"
    cache_path: Path = field(default_factory=lambda: Path.home() / ".openclaw" / "skill-cache")
    index_cache_file: str = "index.json"
    installed_index_file: str = "installed-skills.json"
    cache_ttl_seconds: float = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------


def _default_cache_dir() -> Path:
    return Path.home() / ".openclaw" / "skill-cache"


def _default_installed_index() -> Path:
    return Path.home() / ".openclaw" / "installed-skills.json"


# ---------------------------------------------------------------------------
# Index reader/writer
# ---------------------------------------------------------------------------


class InstalledSkillsIndex:
    """Read and write the installed-skills.json file.

    This is a lightweight JSON-based index that mirrors the SQLite registry
    for quick CLI and UI access without needing to open the database.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path).expanduser().resolve() if path else _default_installed_index()

    def read(self) -> dict[str, dict[str, Any]]:
        """Read the installed skills index.

        Returns a dict mapping skill name → skill info dict.
        """
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        skills = data.get("skills", {})
        if not isinstance(skills, dict):
            return {}
        return {k: v for k, v in skills.items() if isinstance(v, dict)}

    def write(self, skills: dict[str, dict[str, Any]]) -> None:
        """Write the installed skills index."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        data: dict[str, Any] = {
            "skills": skills,
        }
        self._path.write_text(
            json.dumps(data, indent=2, separators=(",", ": "), sort_keys=True),
            encoding="utf-8",
        )

    def get(self, name: str) -> dict[str, Any] | None:
        """Get info for one installed skill."""
        return self.read().get(name)

    def add(
        self,
        name: str,
        version: str,
        source: str = "registry",
        install_path: str = "",
    ) -> None:
        """Add or update an installed skill entry."""
        skills = self.read()
        skills[name] = {
            "name": name,
            "version": version,
            "source": source,
            "install_path": install_path,
            "installed_at": time.time(),
        }
        self.write(skills)

    def remove(self, name: str) -> bool:
        """Remove an installed skill entry.  Returns True if removed."""
        skills = self.read()
        if name not in skills:
            return False
        del skills[name]
        self.write(skills)
        return True

    def list_installed(self) -> list[dict[str, Any]]:
        """Return all installed skills as a list of dicts."""
        return list(self.read().values())


# ---------------------------------------------------------------------------
# Registry index fetcher
# ---------------------------------------------------------------------------


class RegistryIndexFetcher:
    """Fetch and cache the remote registry index.

    Supports both HTTP and local file URLs for development.
    """

    def __init__(
        self,
        config: IndexConfig | None = None,
        *,
        http_fetcher: Callable[[str], bytes] | None = None,
    ) -> None:
        self._config = config or IndexConfig()
        self._http_fetcher = http_fetcher
        self._cached_index: RegistryIndex | None = None
        self._last_fetch: float = 0.0

    def fetch(self, *, force: bool = False) -> RegistryIndex:
        """Fetch the registry index, using cache if fresh.

        Parameters
        ----------
        force:
            If True, bypass the cache TTL and fetch fresh data.

        Returns
        -------
        ``RegistryIndex`` with the parsed index data.
        """
        now = time.time()

        # Use cached in-memory index if still fresh
        if not force and self._cached_index is not None:
            if now - self._last_fetch < self._config.cache_ttl_seconds:
                return self._cached_index

        # Try to fetch from remote
        try:
            raw = self._do_fetch()
            index = self._parse_index(raw)
            self._cached_index = index
            self._last_fetch = now
            self._cache_to_disk(raw)
            return index
        except FetchError:
            pass

        # Fall back to disk cache
        cached = self._read_disk_cache()
        if cached is not None:
            self._cached_index = cached
            self._last_fetch = now
            return cached

        # Return empty index as last resort
        return RegistryIndex()

    def _do_fetch(self) -> bytes:
        """Actually fetch the index from the configured URL."""
        url = self._config.registry_url.rstrip("/") + "/" + self._config.index_cache_file

        if self._http_fetcher is not None:
            try:
                return self._http_fetcher(url)
            except Exception as exc:
                raise FetchError(f"Failed to fetch registry index from {url}: {exc}") from exc

        # Use urllib for the default HTTP fetch
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=10) as response:
                return response.read()
        except Exception as exc:
            raise FetchError(f"Failed to fetch registry index from {url}: {exc}") from exc

    def _parse_index(self, raw: bytes) -> RegistryIndex:
        """Parse raw JSON bytes into a RegistryIndex."""
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise FetchError(f"Invalid registry index JSON: {exc}") from exc

        format_version = data.get("formatVersion", 1)
        updated_str = data.get("updated", "")
        updated: float = 0.0
        if updated_str:
            try:
                from datetime import datetime, timezone

                dt = datetime.fromisoformat(updated_str)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                updated = dt.timestamp()
            except (ValueError, TypeError):
                updated = time.time()

        skills: dict[str, SkillMetadata] = {}
        versions: dict[str, list[SkillVersionInfo]] = {}
        latest: dict[str, str] = {}

        raw_skills = data.get("skills", {})
        if isinstance(raw_skills, dict):
            for sk_name, sk_data in raw_skills.items():
                if not isinstance(sk_data, dict):
                    continue
                meta = SkillMetadata(
                    name=str(sk_data.get("name", sk_name)),
                    description=str(sk_data.get("description", "")),
                    author=str(sk_data.get("author", "")),
                    license=str(sk_data.get("license", "")),
                    tags=tuple(sk_data.get("tags", [])),
                )
                skills[sk_name] = meta

                # Parse versions
                raw_versions = sk_data.get("versions", {})
                if isinstance(raw_versions, dict):
                    ver_list: list[SkillVersionInfo] = []
                    for ver_str, ver_data in raw_versions.items():
                        if not isinstance(ver_data, dict):
                            continue
                        try:
                            parsed_ver = Version(ver_str)
                        except InvalidVersion:
                            continue
                        published_str = ver_data.get("published", "")
                        published_ts: float = 0.0
                        if published_str:
                            try:
                                from datetime import datetime, timezone

                                dt = datetime.fromisoformat(published_str)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                published_ts = dt.timestamp()
                            except (ValueError, TypeError):
                                published_ts = 0.0
                        ver_list.append(
                            SkillVersionInfo(
                                version=parsed_ver,
                                published_at=published_ts,
                                sha256=str(ver_data.get("sha256", "")),
                                ref=str(ver_data.get("ref", "")),
                            )
                        )
                    if ver_list:
                        versions[sk_name] = ver_list

                # Latest version
                latest_str = sk_data.get("latest")
                if isinstance(latest_str, str):
                    latest[sk_name] = latest_str

        return RegistryIndex(
            format_version=format_version,
            updated=updated,
            skills=skills,
            versions=versions,
            latest=latest,
        )

    def _cache_to_disk(self, raw: bytes) -> None:
        """Write the raw index to the disk cache."""
        cache_dir = self._config.cache_path
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file = cache_dir / self._config.index_cache_file
        try:
            cache_file.write_bytes(raw)
        except OSError:
            pass

    def _read_disk_cache(self) -> RegistryIndex | None:
        """Read the cached index from disk."""
        cache_file = self._config.cache_path / self._config.index_cache_file
        if not cache_file.exists():
            return None
        try:
            raw = cache_file.read_bytes()
        except OSError:
            return None
        try:
            return self._parse_index(raw)
        except FetchError:
            return None

    def clear_cache(self) -> None:
        """Clear the in-memory and disk cache."""
        self._cached_index = None
        self._last_fetch = 0.0
        cache_file = self._config.cache_path / self._config.index_cache_file
        if cache_file.exists():
            try:
                cache_file.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Search engine
# ---------------------------------------------------------------------------


class SkillIndex:
    """Searchable index combining registry and installed skill data.

    Provides a unified view of all available skills with install status.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        index_fetcher: RegistryIndexFetcher | None = None,
        installed_index: InstalledSkillsIndex | None = None,
    ) -> None:
        self._registry = registry
        self._fetcher = index_fetcher or RegistryIndexFetcher()
        self._installed_index = installed_index or InstalledSkillsIndex()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str = "",
        *,
        tags: list[str] | None = None,
        author: str | None = None,
        include_installed_only: bool = False,
        include_not_installed: bool = True,
    ) -> list[SkillSearchResult]:
        """Search for skills matching the given criteria.

        Parameters
        ----------
        query:
            Free-text search (matches name, description, author, tags).
        tags:
            Filter by tags (skill must have all specified tags).
        author:
            Filter by author (exact match).
        include_installed_only:
            If True, only return skills that are currently installed.
        include_not_installed:
            If True, include skills that are not installed.

        Returns
        -------
        List of ``SkillSearchResult`` sorted by name.
        """
        # Fetch the registry index (remote or cached)
        index = self._fetcher.fetch()

        # Also get skills from the local SQLite registry
        local_skills = self._registry.list_skills()

        # Get installed skills
        installed = self._installed_index.read()

        # Merge remote and local skills (local takes precedence for metadata)
        merged_skills: dict[str, SkillMetadata] = {}
        merged_versions: dict[str, list[SkillVersionInfo]] = {}
        merged_latest: dict[str, str] = {}

        # Start with remote
        for sk_name, meta in index.skills.items():
            merged_skills[sk_name] = meta
        for sk_name, vers in index.versions.items():
            merged_versions[sk_name] = list(vers)
        for sk_name, lat in index.latest.items():
            merged_latest[sk_name] = lat

        # Overlay local registry data
        for local_meta in local_skills:
            merged_skills[local_meta.name] = local_meta
            local_vers = self._registry.get_versions(local_meta.name)
            if local_vers:
                merged_versions[local_meta.name] = local_vers
                merged_latest[local_meta.name] = str(local_vers[0].version)

        results: list[SkillSearchResult] = []
        query_lower = query.strip().lower()

        for sk_name, meta in merged_skills.items():
            # Apply filters
            if not include_not_installed and sk_name not in installed:
                continue

            if query_lower:
                if (
                    query_lower not in sk_name.lower()
                    and query_lower not in meta.description.lower()
                    and query_lower not in meta.author.lower()
                    and not any(query_lower in tag.lower() for tag in meta.tags)
                ):
                    continue

            if tags:
                if not all(tag in meta.tags for tag in tags):
                    continue

            if author and meta.author.lower() != author.lower():
                continue

            # Get version info
            all_versions = merged_versions.get(sk_name, [])
            version_strings = tuple(str(v.version) for v in all_versions)
            latest_version = merged_latest.get(sk_name, version_strings[0] if version_strings else "0.0.0-dev")

            # Get install status
            installed_info = installed.get(sk_name)
            installed_version: str | None = None
            installed_path: Path | None = None
            is_up_to_date = True

            if installed_info:
                installed_version = str(installed_info.get("version", ""))
                install_path_str = installed_info.get("install_path", "")
                if install_path_str:
                    installed_path = Path(install_path_str)
                is_up_to_date = installed_version == latest_version

            if include_installed_only and installed_version is None:
                continue

            results.append(
                SkillSearchResult(
                    name=sk_name,
                    description=meta.description,
                    author=meta.author,
                    license=meta.license,
                    tags=meta.tags,
                    latest_version=latest_version,
                    all_versions=version_strings,
                    installed_version=installed_version,
                    installed_path=installed_path,
                    is_up_to_date=is_up_to_date,
                )
            )

        results.sort(key=lambda r: r.name.lower())
        return results

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_all(self) -> list[SkillSearchResult]:
        """Return all available skills from the registry."""
        return self.search()

    def list_installed(self) -> list[SkillSearchResult]:
        """Return all installed skills with registry info."""
        return self.search(include_installed_only=True, include_not_installed=False)

    def list_updatable(self) -> list[SkillSearchResult]:
        """Return installed skills that have newer versions available."""
        all_installed = self.search(include_installed_only=True, include_not_installed=False)
        return [s for s in all_installed if not s.is_up_to_date]

    def get_detail(self, name: str) -> SkillSearchResult | None:
        """Get detailed info for one skill by name."""
        results = self.search(query=name)
        for r in results:
            if r.name.lower() == name.lower():
                return r
        return None

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def refresh(self, *, force: bool = False) -> RegistryIndex:
        """Refresh the registry index from the remote source."""
        return self._fetcher.fetch(force=force)

    def clear_cache(self) -> None:
        """Clear all cached index data."""
        self._fetcher.clear_cache()