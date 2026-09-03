"""Skill installer — validates, resolves dependencies, and deploys to OpenClaw worker environment.

The installer handles:

1. **Validation** — verifies SKILL.md exists with valid frontmatter, checks
   version format, validates dependency declarations
2. **Dependency resolution** — resolves all transitive dependencies before
   installing
3. **Deployment** — copies skill files to the target ``skills/`` directory
   in the worker workspace
4. **Rollback** — on any failure, the installation is rolled back to the
   previous state

All operations are idempotent.  Installing the same skill+version twice
is a no-op.  Re-installing a different version replaces the previous one.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from packaging.version import InvalidVersion, Version

from mindroom.tool_system.skill_deps import (
    DependencyError,
    DependencyGraph,
    SkillDep,
    resolve_dependencies,
)
from mindroom.tool_system.registry import (
    InstalledSkill,
    RegistryError,
    SkillMetadata,
    SkillRegistry,
    SkillVersionInfo,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class InstallError(RuntimeError):
    """A skill installation failed."""


class ValidationError(InstallError):
    """Skill validation failed."""


class RollbackError(InstallError):
    """Installation was rolled back due to a failure."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InstallPlan:
    """A resolved installation plan for one or more skills."""

    primary: SkillDep  # The skill being installed
    dependencies: tuple[SkillDep, ...]  # All transitive dependencies
    target_dir: Path  # Where the primary skill will be installed
    dep_target_dirs: dict[str, Path]  # dependency_name → target dir


@dataclass(frozen=True, slots=True)
class InstallResult:
    """The result of a successful installation."""

    name: str
    version: Version
    install_path: Path
    dependencies: tuple[str, ...]
    installed_at: float


# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------


def _default_skills_dir() -> Path:
    """Return the default user-installed skills directory."""
    return Path.home() / ".openclaw" / "skills"


def _default_registry_db() -> Path:
    """Return the default registry database path."""
    return Path.home() / ".openclaw" / "skill-registry.db"


def _default_installed_index() -> Path:
    """Return the default installed-skills.json path."""
    return Path.home() / ".openclaw" / "installed-skills.json"


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------


def _read_skill_frontmatter(skill_dir: Path) -> dict[str, Any] | None:
    """Read and parse SKILL.md frontmatter from a skill directory."""
    from mindroom import yaml_io

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        return None
    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    import re

    m = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
    if not m:
        return None
    try:
        fm = yaml_io.safe_load(m.group(1))
    except Exception:
        return None
    return fm if isinstance(fm, dict) else None


def validate_skill_dir(skill_dir: Path) -> dict[str, Any]:
    """Validate a skill directory and return its parsed frontmatter.

    Raises ``ValidationError`` with a descriptive message on any issue.
    """
    if not skill_dir.exists():
        raise ValidationError(f"Skill directory does not exist: {skill_dir}")
    if not skill_dir.is_dir():
        raise ValidationError(f"Skill path is not a directory: {skill_dir}")

    skill_file = skill_dir / "SKILL.md"
    if not skill_file.exists():
        raise ValidationError(f"Missing SKILL.md in {skill_dir}")
    if not skill_file.is_file():
        raise ValidationError(f"SKILL.md is not a file in {skill_dir}")

    fm = _read_skill_frontmatter(skill_dir)
    if fm is None:
        raise ValidationError(f"Invalid or missing frontmatter in {skill_file}")

    name = fm.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError(f"Skill name is missing or invalid in {skill_file}")

    # Version is optional; default to 0.0.0-dev
    version_raw = fm.get("version")
    if version_raw is not None:
        if not isinstance(version_raw, str) or not version_raw.strip():
            raise ValidationError(f"Invalid version field in {skill_file}: {version_raw!r}")
        try:
            Version(version_raw.strip())
        except InvalidVersion as exc:
            raise ValidationError(f"Invalid version {version_raw!r} in {skill_file}: {exc}") from exc

    return fm


# ---------------------------------------------------------------------------
# Installer
# ---------------------------------------------------------------------------


class SkillInstaller:
    """Install, update, and uninstall skills with rollback support.

    Parameters
    ----------
    registry:
        The ``SkillRegistry`` instance for tracking install state.
    skills_dir:
        The target directory where skills are installed (default:
        ``~/.openclaw/skills/``).
    all_skill_roots:
        All skill search roots for dependency resolution (in precedence
        order).  The *skills_dir* should be included at the appropriate
        priority level.
    """

    def __init__(
        self,
        registry: SkillRegistry,
        skills_dir: str | Path | None = None,
        all_skill_roots: Sequence[Path] | None = None,
        installed_index_path: str | Path | None = None,
    ) -> None:
        self._registry = registry
        self._skills_dir = Path(skills_dir).expanduser().resolve() if skills_dir else _default_skills_dir()
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        self._all_skill_roots = list(all_skill_roots) if all_skill_roots else [self._skills_dir]
        self._installed_index_path = Path(installed_index_path).expanduser().resolve() if installed_index_path else _default_installed_index()

    # ------------------------------------------------------------------
    # Install
    # ------------------------------------------------------------------

    def install(
        self,
        source_path: str | Path,
        *,
        version: str | None = None,
        force: bool = False,
        dry_run: bool = False,
    ) -> InstallResult:
        """Install a skill from a source directory.

        Parameters
        ----------
        source_path:
            Path to the skill directory (must contain ``SKILL.md``).
        version:
            Optional version constraint.  If not provided, the version
            from SKILL.md frontmatter is used.
        force:
            If True, re-install even if the same version is already installed.
        dry_run:
            If True, validate and resolve but do not copy files.

        Returns
        -------
        ``InstallResult`` with installation details.

        Raises
        ------
        ValidationError
            If the skill directory is invalid.
        DependencyError
            If dependencies cannot be resolved.
        InstallError
            If the installation fails.
        """
        source = Path(source_path).expanduser().resolve()
        fm = validate_skill_dir(source)

        skill_name = str(fm["name"])
        skill_version = version or fm.get("version", "0.0.0-dev")
        parsed_version = Version(skill_version.strip() if isinstance(skill_version, str) else "0.0.0-dev")

        # Check if already installed
        if not force:
            existing = self._registry.get_installed(skill_name)
            if existing is not None and existing.version == parsed_version:
                return InstallResult(
                    name=skill_name,
                    version=parsed_version,
                    install_path=existing.install_path,
                    dependencies=(),
                    installed_at=existing.installed_at,
                )

        # Resolve dependencies — include the source path in roots so the
        # skill being installed can be found during resolution
        resolve_roots = [source.parent] + self._all_skill_roots
        dep_graph = self._resolve_skill_deps(skill_name, fm, extra_roots=resolve_roots)

        if dry_run:
            return InstallResult(
                name=skill_name,
                version=parsed_version,
                install_path=self._skills_dir / skill_name,
                dependencies=tuple(sorted(dep_graph.resolved.keys())),
                installed_at=time.time(),
            )

        # Perform installation with rollback
        return self._do_install(source, skill_name, parsed_version, dep_graph)

    def install_from_registry(
        self,
        skill_name: str,
        version: str | None = None,
        *,
        force: bool = False,
        dry_run: bool = False,
    ) -> InstallResult:
        """Install a skill from the registry by name.

        The skill must already be registered in the ``SkillRegistry``
        database.  The source files are expected at a known cache path
        (``~/.openclaw/skill-cache/<name>/<version>/``).

        Parameters
        ----------
        skill_name:
            The name of the skill to install.
        version:
            Optional version constraint (e.g. ``"^1.0.0"``, ``">=1.2,<2.0"``).
            If not provided, the latest version is installed.
        force:
            If True, re-install even if the same version is already installed.
        dry_run:
            If True, validate and resolve but do not copy files.

        Returns
        -------
        ``InstallResult`` with installation details.
        """
        # Resolve the version
        if version:
            from packaging.specifiers import SpecifierSet

            # If version is a plain version (no specifier operators), treat as exact match
            if not any(op in version for op in (">=", "<=", "==", "!=", "~=", ">", "<", "^", "~", "*", "x")):
                spec = SpecifierSet(f"=={version}")
            else:
                spec = SpecifierSet(version)
            all_versions = self._registry.get_versions(skill_name)
            candidates = [v for v in all_versions if v.version in spec]
            if not candidates:
                raise InstallError(
                    f"No version of {skill_name!r} satisfies constraint {version!r}"
                )
            target_version = max(candidates, key=lambda v: v.version)
        else:
            latest = self._registry.get_latest_version(skill_name)
            if latest is None:
                raise InstallError(f"Skill {skill_name!r} has no published versions")
            target_version = latest

        # Locate the cached skill directory
        cache_root = Path.home() / ".openclaw" / "skill-cache" / skill_name / str(target_version.version)
        if not cache_root.exists():
            raise InstallError(
                f"Cached skill {skill_name}@{target_version.version} not found at {cache_root}. "
                f"Run 'openclaw skill fetch {skill_name}@{target_version.version}' first."
            )

        return self.install(
            cache_root,
            version=str(target_version.version),
            force=force,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------
    # Uninstall
    # ------------------------------------------------------------------

    def uninstall(
        self,
        skill_name: str,
        *,
        dry_run: bool = False,
    ) -> bool:
        """Uninstall a skill, removing its files and registry entry.

        Returns True if the skill was uninstalled, False if it wasn't
        installed.
        """
        installed = self._registry.get_installed(skill_name)
        if installed is None:
            return False

        target = installed.install_path

        if dry_run:
            return True

        # Remove files
        if target.exists():
            shutil.rmtree(target)

        # Remove parent directory if empty
        parent = target.parent
        if parent.exists() and not any(parent.iterdir()):
            try:
                parent.rmdir()
            except OSError:
                pass

        # Update registry
        self._registry.mark_uninstalled(skill_name)

        # Update installed-skills.json
        self._update_installed_index(skill_name, remove=True)

        return True

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(
        self,
        skill_name: str,
        *,
        dry_run: bool = False,
    ) -> InstallResult | None:
        """Update a skill to the latest available version.

        Returns ``InstallResult`` if an update was performed, or None if
        already at the latest version.
        """
        installed = self._registry.get_installed(skill_name)
        if installed is None:
            raise InstallError(f"Skill {skill_name!r} is not installed")

        latest = self._registry.get_latest_version(skill_name)
        if latest is None:
            raise InstallError(f"Skill {skill_name!r} has no published versions")

        if latest.version <= installed.version:
            return None  # Already up to date

        return self.install_from_registry(
            skill_name,
            version=str(latest.version),
            force=True,
            dry_run=dry_run,
        )

    # ------------------------------------------------------------------
    # Internal: dependency resolution
    # ------------------------------------------------------------------

    def _resolve_skill_deps(
        self,
        skill_name: str,
        frontmatter: Mapping[str, Any],
        *,
        extra_roots: list[Path] | None = None,
    ) -> DependencyGraph:
        """Resolve dependencies for a skill."""
        from mindroom.tool_system.skill_deps import _extract_skill_deps

        skill_deps = _extract_skill_deps(frontmatter)
        entry_points = [skill_name]
        entry_points.extend(skill_deps.keys())

        roots = extra_roots if extra_roots is not None else self._all_skill_roots

        return resolve_dependencies(
            entry_points,
            roots,
        )

    # ------------------------------------------------------------------
    # Internal: actual installation
    # ------------------------------------------------------------------

    def _do_install(
        self,
        source: Path,
        skill_name: str,
        version: Version,
        dep_graph: DependencyGraph,
    ) -> InstallResult:
        """Perform the actual file copy with rollback support."""
        target_dir = self._skills_dir / skill_name

        # Create a temporary staging directory
        staging_parent = self._skills_dir / f".install-staging-{skill_name}"
        staging_dir = staging_parent / skill_name

        try:
            # Clean any previous staging
            if staging_parent.exists():
                shutil.rmtree(staging_parent)

            # Copy to staging
            staging_parent.mkdir(parents=True, exist_ok=True)
            self._copy_skill(source, staging_dir)

            # Also stage dependencies that aren't already installed
            dep_targets: dict[str, Path] = {}
            for dep_name, dep in dep_graph.resolved.items():
                if dep_name == skill_name:
                    continue
                dep_target = self._skills_dir / dep_name
                dep_targets[dep_name] = dep_target
                if not dep_target.exists():
                    dep_target.parent.mkdir(parents=True, exist_ok=True)
                    self._copy_skill(dep.path, dep_target)

            # Atomic rename: staging → target
            if target_dir.exists():
                backup_dir = self._skills_dir / f".install-backup-{skill_name}"
                if backup_dir.exists():
                    shutil.rmtree(backup_dir)
                target_dir.rename(backup_dir)

            try:
                staging_dir.rename(target_dir)
            except OSError:
                # Rename across filesystems — fall back to copy + delete
                shutil.copytree(staging_dir, target_dir, dirs_exist_ok=True)
                shutil.rmtree(staging_dir)

            # Clean up backup
            backup_dir = self._skills_dir / f".install-backup-{skill_name}"
            if backup_dir.exists():
                shutil.rmtree(backup_dir)

            # Clean staging parent
            if staging_parent.exists():
                shutil.rmtree(staging_parent)

        except Exception as exc:
            # Rollback: restore from backup if it exists
            backup_dir = self._skills_dir / f".install-backup-{skill_name}"
            if backup_dir.exists():
                if target_dir.exists():
                    shutil.rmtree(target_dir)
                backup_dir.rename(target_dir)

            # Clean up staging
            if staging_parent.exists():
                shutil.rmtree(staging_parent)

            raise InstallError(f"Installation failed, rolled back: {exc}") from exc

        # Register in the database
        now = time.time()
        installed = InstalledSkill(
            name=skill_name,
            version=version,
            source="registry",
            install_path=target_dir,
            installed_at=now,
            enabled=True,
        )
        self._registry.mark_installed(installed)

        # Update installed-skills.json
        self._update_installed_index(skill_name, installed=installed)

        return InstallResult(
            name=skill_name,
            version=version,
            install_path=target_dir,
            dependencies=tuple(sorted(dep_graph.resolved.keys())),
            installed_at=now,
        )

    # ------------------------------------------------------------------
    # Internal: file copy
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_skill(source: Path, target: Path) -> None:
        """Copy a skill directory, preserving structure."""
        target.mkdir(parents=True, exist_ok=True)
        for item in source.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest, dirs_exist_ok=True, symlinks=True)
            else:
                shutil.copy2(item, dest)

    # ------------------------------------------------------------------
    # Internal: installed index
    # ------------------------------------------------------------------

    def _update_installed_index(
        self,
        skill_name: str,
        *,
        installed: InstalledSkill | None = None,
        remove: bool = False,
    ) -> None:
        """Update the installed-skills.json file."""
        index_path = self._installed_index_path
        index: dict[str, Any] = {"skills": {}}

        if index_path.exists():
            try:
                index = json.loads(index_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                index = {"skills": {}}

        if remove:
            index["skills"].pop(skill_name, None)
        elif installed is not None:
            index["skills"][skill_name] = {
                "name": installed.name,
                "version": str(installed.version),
                "source": installed.source,
                "install_path": str(installed.install_path),
                "installed_at": installed.installed_at,
            }

        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(index, indent=2, separators=(",", ": "), sort_keys=True),
            encoding="utf-8",
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify_installation(self, skill_name: str) -> list[str]:
        """Verify that a skill's installation is complete and valid.

        Returns a list of issues (empty = all good).
        """
        issues: list[str] = []

        installed = self._registry.get_installed(skill_name)
        if installed is None:
            issues.append(f"Skill {skill_name!r} is not registered as installed")
            return issues

        if not installed.install_path.exists():
            issues.append(f"Install path does not exist: {installed.install_path}")
            return issues

        try:
            fm = validate_skill_dir(installed.install_path)
        except ValidationError as exc:
            issues.append(str(exc))
            return issues

        # Verify version matches
        fm_version = fm.get("version", "0.0.0-dev")
        try:
            parsed = Version(fm_version.strip() if isinstance(fm_version, str) else "0.0.0-dev")
            if parsed != installed.version:
                issues.append(
                    f"Version mismatch: installed={installed.version}, SKILL.md says={parsed}"
                )
        except InvalidVersion:
            issues.append(f"Invalid version in SKILL.md: {fm_version!r}")

        # Verify dependencies are installed
        from mindroom.tool_system.skill_deps import _extract_skill_deps

        skill_deps = _extract_skill_deps(fm)
        for dep_name in skill_deps:
            dep_installed = self._registry.get_installed(dep_name)
            if dep_installed is None:
                issues.append(f"Missing dependency: {dep_name}")
            elif not dep_installed.install_path.exists():
                issues.append(f"Dependency {dep_name!r} install path missing")

        return issues