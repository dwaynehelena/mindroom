"""Skill registry — CRUD operations, versioning, metadata storage backed by SQLite.

The registry stores skills in a local SQLite database at
``~/.openclaw/skill-registry.db`` (configurable).  It tracks:

- Skill metadata (name, description, author, license, tags)
- Version history per skill
- Install state (installed version, install path, source)
- Last-seen index from the remote registry

All operations are idempotent and support rollback on failure via
transaction management.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RegistryError(RuntimeError):
    """Base exception for registry operations."""


class SkillNotFoundError(RegistryError):
    """The requested skill does not exist in the registry."""


class VersionNotFoundError(RegistryError):
    """The requested version of a skill does not exist."""


class VersionConflictError(RegistryError):
    """A version conflict occurred during registration."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    """Immutable metadata for one skill."""

    name: str
    description: str
    author: str
    license: str
    tags: tuple[str, ...] = ()
    homepage: str = ""
    repository: str = ""


@dataclass(frozen=True, slots=True)
class SkillVersionInfo:
    """Information about one published version of a skill."""

    version: Version
    published_at: float  # Unix timestamp
    sha256: str = ""
    ref: str = ""  # e.g. git tag "my-skill@1.2.3"
    changelog: str = ""


@dataclass(frozen=True, slots=True)
class InstalledSkill:
    """An installed skill with its current state."""

    name: str
    version: Version
    source: str  # "registry", "bundled", "user", "plugin"
    install_path: Path
    installed_at: float  # Unix timestamp
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class RegistryIndex:
    """The full registry index, typically fetched from a remote source."""

    format_version: int = 1
    updated: float = 0.0  # Unix timestamp
    skills: dict[str, SkillMetadata] = field(default_factory=dict)
    versions: dict[str, list[SkillVersionInfo]] = field(default_factory=dict)
    latest: dict[str, str] = field(default_factory=dict)  # skill_name → latest version string


# ---------------------------------------------------------------------------
# SQLite-backed registry
# ---------------------------------------------------------------------------

_SCHEMA_VERSION = 1

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS _meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS skills (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    author TEXT NOT NULL DEFAULT '',
    license TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',       -- JSON array of strings
    homepage TEXT NOT NULL DEFAULT '',
    repository TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS skill_versions (
    skill_name TEXT NOT NULL,
    version TEXT NOT NULL,
    published_at REAL NOT NULL DEFAULT 0,
    sha256 TEXT NOT NULL DEFAULT '',
    ref TEXT NOT NULL DEFAULT '',
    changelog TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (skill_name, version),
    FOREIGN KEY (skill_name) REFERENCES skills(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS installed_skills (
    name TEXT PRIMARY KEY,
    version TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'registry',
    install_path TEXT NOT NULL,
    installed_at REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_skill_versions_skill
    ON skill_versions(skill_name);
"""


class SkillRegistry:
    """SQLite-backed skill registry with CRUD, versioning, and install tracking.

    Thread-safe: uses ``check_same_thread=False`` and relies on SQLite's
    built-in locking.  For concurrent write access from multiple processes,
    use a separate registry per process or a server-backed registry.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = Path(db_path).expanduser().resolve()
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        """Create tables if they don't exist, with schema version check."""
        self._conn.executescript(_CREATE_TABLES)
        row = self._conn.execute("SELECT value FROM _meta WHERE key = 'schema_version'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO _meta (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()
        elif int(row["value"]) < _SCHEMA_VERSION:
            # Future: run migrations here
            self._conn.execute(
                "UPDATE _meta SET value = ? WHERE key = 'schema_version'",
                (str(_SCHEMA_VERSION),),
            )
            self._conn.commit()

    # ------------------------------------------------------------------
    # Transaction context manager
    # ------------------------------------------------------------------

    def transaction(self) -> _RegistryTransaction:
        """Return a context manager for atomic batch operations.

        Usage::

            with registry.transaction() as tx:
                tx.register_skill(...)
                tx.add_version(...)
                # Auto-commits on success, rolls back on exception
        """
        return _RegistryTransaction(self._conn)

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    def register_skill(
        self,
        metadata: SkillMetadata,
        *,
        commit: bool = True,
    ) -> bool:
        """Register or update a skill's metadata.

        Returns True if a new skill was created, False if updated.
        Idempotent: calling with the same metadata is a no-op.
        """
        now = time.time()
        existing = self._conn.execute(
            "SELECT name FROM skills WHERE name = ?",
            (metadata.name,),
        ).fetchone()

        tags_json = json.dumps(list(metadata.tags), separators=(",", ":"))

        if existing is None:
            self._conn.execute(
                """INSERT INTO skills (name, description, author, license, tags,
                   homepage, repository, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    metadata.name,
                    metadata.description,
                    metadata.author,
                    metadata.license,
                    tags_json,
                    metadata.homepage,
                    metadata.repository,
                    now,
                    now,
                ),
            )
            created = True
        else:
            self._conn.execute(
                """UPDATE skills SET description=?, author=?, license=?, tags=?,
                   homepage=?, repository=?, updated_at=?
                   WHERE name=?""",
                (
                    metadata.description,
                    metadata.author,
                    metadata.license,
                    tags_json,
                    metadata.homepage,
                    metadata.repository,
                    now,
                    metadata.name,
                ),
            )
            created = False

        if commit:
            self._conn.commit()
        return created

    def get_skill(self, name: str) -> SkillMetadata | None:
        """Look up a skill by name.  Returns None if not found."""
        row = self._conn.execute(
            "SELECT * FROM skills WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_metadata(row)

    def delete_skill(self, name: str, *, commit: bool = True) -> bool:
        """Delete a skill and all its versions.  Returns True if deleted."""
        cursor = self._conn.execute("DELETE FROM skills WHERE name = ?", (name,))
        if commit:
            self._conn.commit()
        return cursor.rowcount > 0

    def list_skills(self) -> list[SkillMetadata]:
        """Return all registered skills."""
        rows = self._conn.execute("SELECT * FROM skills ORDER BY name").fetchall()
        return [self._row_to_metadata(row) for row in rows]

    def search_skills(self, query: str) -> list[SkillMetadata]:
        """Search skills by name, description, author, or tags."""
        like = f"%{query}%"
        rows = self._conn.execute(
            """SELECT * FROM skills
               WHERE name LIKE ? OR description LIKE ? OR author LIKE ? OR tags LIKE ?
               ORDER BY name""",
            (like, like, like, like),
        ).fetchall()
        return [self._row_to_metadata(row) for row in rows]

    # ------------------------------------------------------------------
    # Version management
    # ------------------------------------------------------------------

    def add_version(
        self,
        skill_name: str,
        info: SkillVersionInfo,
        *,
        commit: bool = True,
    ) -> bool:
        """Register a version for a skill.

        Returns True if new, False if already exists (idempotent).
        Raises ``SkillNotFoundError`` if the skill is not registered.
        """
        if self.get_skill(skill_name) is None:
            raise SkillNotFoundError(f"Skill {skill_name!r} is not registered")

        existing = self._conn.execute(
            "SELECT skill_name FROM skill_versions WHERE skill_name = ? AND version = ?",
            (skill_name, str(info.version)),
        ).fetchone()

        if existing is not None:
            return False

        self._conn.execute(
            """INSERT INTO skill_versions
               (skill_name, version, published_at, sha256, ref, changelog)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                skill_name,
                str(info.version),
                info.published_at,
                info.sha256,
                info.ref,
                info.changelog,
            ),
        )
        if commit:
            self._conn.commit()
        return True

    def get_versions(self, skill_name: str) -> list[SkillVersionInfo]:
        """Return all registered versions for a skill, newest first."""
        rows = self._conn.execute(
            """SELECT * FROM skill_versions
               WHERE skill_name = ?
               ORDER BY version DESC""",
            (skill_name,),
        ).fetchall()
        return [self._row_to_version_info(row) for row in rows]

    def get_latest_version(self, skill_name: str) -> SkillVersionInfo | None:
        """Return the highest registered version for a skill."""
        rows = self._conn.execute(
            """SELECT * FROM skill_versions
               WHERE skill_name = ?
               ORDER BY version DESC LIMIT 1""",
            (skill_name,),
        ).fetchall()
        if not rows:
            return None
        return self._row_to_version_info(rows[0])

    def delete_version(self, skill_name: str, version: str, *, commit: bool = True) -> bool:
        """Remove a specific version.  Returns True if deleted."""
        cursor = self._conn.execute(
            "DELETE FROM skill_versions WHERE skill_name = ? AND version = ?",
            (skill_name, version),
        )
        if commit:
            self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Install tracking
    # ------------------------------------------------------------------

    def mark_installed(
        self,
        skill: InstalledSkill,
        *,
        commit: bool = True,
    ) -> bool:
        """Record a skill as installed (upsert).

        Returns True if new, False if updated.
        """
        existing = self._conn.execute(
            "SELECT name FROM installed_skills WHERE name = ?",
            (skill.name,),
        ).fetchone()

        if existing is None:
            self._conn.execute(
                """INSERT INTO installed_skills
                   (name, version, source, install_path, installed_at, enabled)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    skill.name,
                    str(skill.version),
                    skill.source,
                    str(skill.install_path),
                    skill.installed_at,
                    1 if skill.enabled else 0,
                ),
            )
            created = True
        else:
            self._conn.execute(
                """UPDATE installed_skills
                   SET version=?, source=?, install_path=?, installed_at=?, enabled=?
                   WHERE name=?""",
                (
                    str(skill.version),
                    skill.source,
                    str(skill.install_path),
                    skill.installed_at,
                    1 if skill.enabled else 0,
                    skill.name,
                ),
            )
            created = False

        if commit:
            self._conn.commit()
        return created

    def mark_uninstalled(self, name: str, *, commit: bool = True) -> bool:
        """Remove an install record.  Returns True if removed."""
        cursor = self._conn.execute(
            "DELETE FROM installed_skills WHERE name = ?",
            (name,),
        )
        if commit:
            self._conn.commit()
        return cursor.rowcount > 0

    def get_installed(self, name: str) -> InstalledSkill | None:
        """Look up an installed skill by name."""
        row = self._conn.execute(
            "SELECT * FROM installed_skills WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_installed(row)

    def list_installed(self) -> list[InstalledSkill]:
        """Return all installed skills."""
        rows = self._conn.execute(
            "SELECT * FROM installed_skills ORDER BY name"
        ).fetchall()
        return [self._row_to_installed(row) for row in rows]

    def set_enabled(self, name: str, enabled: bool, *, commit: bool = True) -> bool:
        """Enable or disable an installed skill.  Returns True if changed."""
        cursor = self._conn.execute(
            "UPDATE installed_skills SET enabled = ? WHERE name = ?",
            (1 if enabled else 0, name),
        )
        if commit:
            self._conn.commit()
        return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Index import
    # ------------------------------------------------------------------

    def import_index(self, index: RegistryIndex, *, commit: bool = True) -> int:
        """Import a registry index, registering new skills and versions.

        Returns the number of new skills registered.
        """
        count = 0
        for skill_name, meta in index.skills.items():
            if self.register_skill(meta, commit=False):
                count += 1
            for ver_info in index.versions.get(skill_name, []):
                self.add_version(skill_name, ver_info, commit=False)
        if commit:
            self._conn.commit()
        return count

    def export_index(self) -> RegistryIndex:
        """Export the local registry as a ``RegistryIndex``."""
        skills = self.list_skills()
        skill_dict: dict[str, SkillMetadata] = {s.name: s for s in skills}
        versions_dict: dict[str, list[SkillVersionInfo]] = {}
        latest_dict: dict[str, str] = {}
        for s in skills:
            vers = self.get_versions(s.name)
            if vers:
                versions_dict[s.name] = vers
                latest_dict[s.name] = str(vers[0].version)
        return RegistryIndex(
            format_version=1,
            updated=time.time(),
            skills=skill_dict,
            versions=versions_dict,
            latest=latest_dict,
        )

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def vacuum(self) -> None:
        """Reclaim disk space.  Safe to call periodically."""
        self._conn.execute("VACUUM")

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_metadata(row: sqlite3.Row) -> SkillMetadata:
        tags: list[str] = []
        try:
            tags = json.loads(row["tags"]) if row["tags"] else []
        except (json.JSONDecodeError, TypeError):
            pass
        return SkillMetadata(
            name=row["name"],
            description=row["description"],
            author=row["author"],
            license=row["license"],
            tags=tuple(tags),
            homepage=row["homepage"],
            repository=row["repository"],
        )

    @staticmethod
    def _row_to_version_info(row: sqlite3.Row) -> SkillVersionInfo:
        ver = Version("0.0.0")
        try:
            ver = Version(row["version"])
        except InvalidVersion:
            pass
        return SkillVersionInfo(
            version=ver,
            published_at=row["published_at"],
            sha256=row["sha256"],
            ref=row["ref"],
            changelog=row["changelog"],
        )

    @staticmethod
    def _row_to_installed(row: sqlite3.Row) -> InstalledSkill:
        ver = Version("0.0.0")
        try:
            ver = Version(row["version"])
        except InvalidVersion:
            pass
        return InstalledSkill(
            name=row["name"],
            version=ver,
            source=row["source"],
            install_path=Path(row["install_path"]),
            installed_at=row["installed_at"],
            enabled=bool(row["enabled"]),
        )


# ---------------------------------------------------------------------------
# Transaction helper
# ---------------------------------------------------------------------------


class _RegistryTransaction:
    """Context manager for atomic registry operations."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._active = False

    def __enter__(self) -> SkillRegistry:
        self._conn.execute("BEGIN")
        self._active = True
        # Return a proxy that uses the same connection but skips auto-commit
        return _TransactionalProxy(self._conn)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        self._active = False
        if exc_type is None:
            self._conn.commit()
        else:
            self._conn.rollback()


class _TransactionalProxy:
    """A SkillRegistry-like proxy that suppresses auto-commit."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def register_skill(self, metadata: SkillMetadata) -> bool:
        reg = SkillRegistry.__new__(SkillRegistry)
        reg._conn = self._conn  # type: ignore[attr-defined]
        return reg.register_skill(metadata, commit=False)

    def add_version(self, skill_name: str, info: SkillVersionInfo) -> bool:
        reg = SkillRegistry.__new__(SkillRegistry)
        reg._conn = self._conn  # type: ignore[attr-defined]
        return reg.add_version(skill_name, info, commit=False)

    def mark_installed(self, skill: InstalledSkill) -> bool:
        reg = SkillRegistry.__new__(SkillRegistry)
        reg._conn = self._conn  # type: ignore[attr-defined]
        return reg.mark_installed(skill, commit=False)

    def mark_uninstalled(self, name: str) -> bool:
        reg = SkillRegistry.__new__(SkillRegistry)
        reg._conn = self._conn  # type: ignore[attr-defined]
        return reg.mark_uninstalled(name, commit=False)

    def set_enabled(self, name: str, enabled: bool) -> bool:
        reg = SkillRegistry.__new__(SkillRegistry)
        reg._conn = self._conn  # type: ignore[attr-defined]
        return reg.set_enabled(name, enabled, commit=False)