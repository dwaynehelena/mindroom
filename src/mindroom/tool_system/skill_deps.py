"""Dependency resolution for skill packages — version constraints, conflict detection, cycle detection.

This is the core resolution engine for the Skill Foundry. It resolves
skill-to-skill dependencies declared in ``SKILL.md`` frontmatter under
``openclaw.depends_on.skills`` using semver range constraints.

Integration point
------------------
Called from ``build_agent_skills()`` in ``skills.py`` after skill roots are
resolved but before skills are loaded into Agno.  If resolution fails the
agent startup fails fast with a clear error.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence


class DependencyError(RuntimeError):
    """A skill dependency could not be satisfied."""


class CircularDependencyError(DependencyError):
    """A cycle was detected in the dependency graph."""


class VersionConflictError(DependencyError):
    """No single version satisfies all constraints on a dependency."""


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SkillDep:
    """One resolved skill dependency."""

    name: str
    version: Version
    path: Path
    root_origin: str  # e.g. "bundled", "user", "plugin", "registry-cache"


@dataclass(frozen=True, slots=True)
class DependencyGraph:
    """The complete resolved dependency graph for one agent's skill set."""

    roots: frozenset[str]  # The skill names that were the entry points
    resolved: dict[str, SkillDep]  # All resolved skills (name → dep)
    adjacency: dict[str, frozenset[str]]  # name → set of dependency names


# ---------------------------------------------------------------------------
# Protocol for finding skills across roots
# ---------------------------------------------------------------------------


class SkillFinder(Protocol):
    """Interface for locating a skill by name across all skill roots."""

    def find(
        self,
        name: str,
        constraint: str,
        *,
        exclude: set[str] | None = None,
    ) -> SkillDep | None:
        """Find the best matching version of *name* satisfying *constraint*.

        Returns ``None`` when no version satisfies the constraint.
        ``exclude`` is a set of skill names to skip (used during conflict
        re-resolution).
        """


# ---------------------------------------------------------------------------
# Default implementation
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _SkillVersion:
    """A single discoverable version of a skill in a root."""

    version: Version
    path: Path
    root_origin: str


def _parse_version(raw: object) -> Version | None:
    """Parse a version string, returning None on failure."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return Version(raw.strip())
    except InvalidVersion:
        return None


def _translate_npm_style(raw: str) -> str:
    """Translate npm-style range syntax to PEP 440 specifiers.

    Handles:
    - ``^1.2.3`` → ``>=1.2.3,<2.0.0``
    - ``~1.2.3`` → ``>=1.2.3,<1.3.0``
    - ``1.x`` or ``1.*`` → ``>=1.0.0,<2.0.0``
    - ``*`` → ``>=0.0.0``
    """
    s = raw.strip()

    # Wildcard / star
    if s == "*":
        return ">=0.0.0"

    # x / X / * wildcard in place of patch or minor
    import re as _re

    def _replace_wildcard(m: _re.Match) -> str:
        prefix = m.group(1)  # e.g. "1." or "1.2."
        parts = prefix.rstrip(".").split(".")
        if len(parts) == 1:
            # 1.x → >=1.0.0,<2.0.0
            major = int(parts[0])
            return f">={major}.0.0,<{major + 1}.0.0"
        # 1.2.x → >=1.2.0,<1.3.0
        major, minor = int(parts[0]), int(parts[1])
        return f">={major}.{minor}.0,<{major}.{minor + 1}.0"

    s = _re.sub(r"^(\d+(?:\.\d+)?)\.[xX*]$", _replace_wildcard, s)

    # Caret ^1.2.3 → >=1.2.3,<2.0.0
    caret_match = _re.match(r"^\^(\d+)\.(\d+)\.(\d+)$", s)
    if caret_match:
        major = int(caret_match.group(1))
        return f">={caret_match.group(0)[1:]},<{major + 1}.0.0"

    # Tilde ~1.2.3 → >=1.2.3,<1.3.0
    tilde_match = _re.match(r"^~(\d+)\.(\d+)\.(\d+)$", s)
    if tilde_match:
        major = int(tilde_match.group(1))
        minor = int(tilde_match.group(2))
        return f">={tilde_match.group(0)[1:]},<{major}.{minor + 1}.0"

    return s


def _parse_constraint(raw: object) -> SpecifierSet | None:
    """Parse a semver range constraint string.

    Supports PEP 440 specifiers (``>=1.0,<2.0``, ``==1.2.3``, ``~=1.2``)
    as well as npm-style syntax (``^1.2.3``, ``~1.2.3``, ``1.x``, ``*``).
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        translated = _translate_npm_style(raw.strip())
        return SpecifierSet(translated)
    except InvalidSpecifier:
        return None


def _best_match(
    versions: Sequence[_SkillVersion],
    constraint: SpecifierSet,
) -> _SkillVersion | None:
    """Return the highest version satisfying *constraint*."""
    candidates = [v for v in versions if v.version in constraint]
    if not candidates:
        return None
    return max(candidates, key=lambda v: v.version)


def _collect_versions(
    name: str,
    all_skill_roots: Sequence[Path],
    *,
    read_frontmatter: Any = None,
) -> list[_SkillVersion]:
    """Collect all discoverable versions of *name* across *all_skill_roots*.

    *read_frontmatter* is a callable ``(skill_dir) -> dict | None`` that
    returns the parsed YAML frontmatter for a skill directory.  When not
    provided the function falls back to a simple YAML parse.
    """
    from mindroom import yaml_io

    if read_frontmatter is None:

        def _default_read(skill_dir: Path) -> dict[str, Any] | None:
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

        read_frontmatter = _default_read

    versions: list[_SkillVersion] = []
    seen_paths: set[Path] = set()

    for root in all_skill_roots:
        resolved_root = root.expanduser().resolve()
        if not resolved_root.exists():
            continue

        # Determine root origin
        origin = _classify_root(resolved_root)

        # Check if the root itself is a skill directory
        if (resolved_root / "SKILL.md").exists():
            dirs_to_check = [resolved_root]
        else:
            dirs_to_check = sorted(
                p
                for p in resolved_root.iterdir()
                if p.is_dir() and not p.name.startswith(".") and (p / "SKILL.md").exists()
            )

        for skill_dir in dirs_to_check:
            if skill_dir.name != name and skill_dir.name.lower() != name.lower():
                continue
            resolved_dir = skill_dir.resolve()
            if resolved_dir in seen_paths:
                continue
            seen_paths.add(resolved_dir)

            fm = read_frontmatter(skill_dir)
            if fm is None:
                continue

            ver = _parse_version(fm.get("version"))
            if ver is None:
                ver = Version("0.0.0-dev")

            versions.append(
                _SkillVersion(
                    version=ver,
                    path=resolved_dir,
                    root_origin=origin,
                )
            )

    return versions


def _classify_root(root: Path) -> str:
    """Classify a skill root into a human-readable origin string."""
    home = Path.home().expanduser().resolve()
    root_str = str(root)
    if ".openclaw/skill-cache" in root_str:
        return "registry-cache"
    if ".openclaw/skills" in root_str or ".mindroom/skills" in root_str:
        return "user"
    if "plugin" in root_str.lower():
        return "plugin"
    if "bundled" in root_str.lower() or "skills" in root_str and "workspace" not in root_str:
        return "bundled"
    if "workspace" in root_str:
        return "workspace"
    return "custom"


# ---------------------------------------------------------------------------
# Default SkillFinder
# ---------------------------------------------------------------------------


class DefaultSkillFinder:
    """Default implementation that searches all skill roots for matching skills."""

    def __init__(
        self,
        all_skill_roots: Sequence[Path],
        *,
        read_frontmatter: Any = None,
    ) -> None:
        self._all_skill_roots = list(all_skill_roots)
        self._read_frontmatter = read_frontmatter

    def find(
        self,
        name: str,
        constraint: str,
        *,
        exclude: set[str] | None = None,
    ) -> SkillDep | None:
        if exclude and name in exclude:
            return None

        spec = _parse_constraint(constraint)
        if spec is None:
            raise DependencyError(f"Invalid version constraint {constraint!r} for skill {name!r}")

        versions = _collect_versions(name, self._all_skill_roots, read_frontmatter=self._read_frontmatter)
        if not versions:
            return None

        best = _best_match(versions, spec)
        if best is None:
            return None

        return SkillDep(
            name=name,
            version=best.version,
            path=best.path,
            root_origin=best.root_origin,
        )


# ---------------------------------------------------------------------------
# Resolution algorithm
# ---------------------------------------------------------------------------


def _extract_skill_deps(frontmatter: Mapping[str, Any]) -> dict[str, str]:
    """Extract ``{skill_name: constraint}`` from parsed frontmatter.

    Looks under ``openclaw.depends_on.skills``.
    """
    openclaw = frontmatter.get("openclaw")
    if not isinstance(openclaw, dict):
        return {}
    depends_on = openclaw.get("depends_on")
    if not isinstance(depends_on, dict):
        return {}
    skills = depends_on.get("skills")
    if not isinstance(skills, dict):
        return {}
    result: dict[str, str] = {}
    for sk_name, sk_constraint in skills.items():
        if isinstance(sk_name, str) and isinstance(sk_constraint, str):
            result[sk_name] = sk_constraint
    return result


def _extract_tool_deps(frontmatter: Mapping[str, Any]) -> list[str]:
    """Extract tool dependency names from frontmatter.

    Looks under ``openclaw.depends_on.tools``.
    """
    openclaw = frontmatter.get("openclaw")
    if not isinstance(openclaw, dict):
        return []
    depends_on = openclaw.get("depends_on")
    if not isinstance(depends_on, dict):
        return []
    tools = depends_on.get("tools")
    if not isinstance(tools, list):
        return []
    return [t for t in tools if isinstance(t, str)]


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


def resolve_dependencies(
    agent_skill_names: Iterable[str],
    all_skill_roots: Sequence[Path],
    *,
    finder: SkillFinder | None = None,
    read_frontmatter: Any = None,
    tool_registry: set[str] | None = None,
) -> DependencyGraph:
    """Resolve the full dependency graph for a set of agent skills.

    Parameters
    ----------
    agent_skill_names:
        The skill names that are the entry points (e.g. from an agent's
        configured skill list).
    all_skill_roots:
        All skill search roots in precedence order.
    finder:
        Optional custom ``SkillFinder``.  Defaults to ``DefaultSkillFinder``.
    read_frontmatter:
        Optional callable ``(Path) -> dict | None`` for reading SKILL.md
        frontmatter.  Used internally when the finder needs to inspect deps.
    tool_registry:
        Optional set of registered tool names.  When provided, tool
        dependencies are verified during resolution.

    Returns
    -------
    ``DependencyGraph`` with all resolved skills.

    Raises
    ------
    DependencyError
        If any dependency cannot be satisfied.
    CircularDependencyError
        If a cycle is detected.
    VersionConflictError
        If no single version satisfies all constraints on a dependency.
    """
    if finder is None:
        finder = DefaultSkillFinder(all_skill_roots, read_frontmatter=read_frontmatter)

    if read_frontmatter is None:
        read_frontmatter = _read_skill_frontmatter

    resolved: dict[str, SkillDep] = {}
    adjacency: dict[str, set[str]] = {}
    queue: deque[tuple[str, tuple[str, ...]]] = deque()  # (skill_name, path_from_root)
    globally_visited: set[str] = set()

    # Track constraints per dependency for conflict detection
    constraints: dict[str, list[tuple[str, str]]] = {}  # dep_name → [(requirer, constraint)]

    # Seed the queue with entry points
    for name in agent_skill_names:
        queue.append((name, ()))

    while queue:
        skill_name, path = queue.popleft()

        if skill_name in globally_visited:
            continue
        globally_visited.add(skill_name)

        # Find the skill in the roots
        dep = finder.find(skill_name, ">=0.0.0")
        if dep is None:
            raise DependencyError(f"Skill {skill_name!r} not found in any skill root")

        resolved[skill_name] = dep

        # Read frontmatter to get dependencies
        fm = read_frontmatter(dep.path)
        if fm is None:
            continue

        skill_deps = _extract_skill_deps(fm)
        tool_deps = _extract_tool_deps(fm)

        # Verify tool dependencies
        if tool_registry is not None and tool_deps:
            missing_tools = [t for t in tool_deps if t not in tool_registry]
            if missing_tools:
                raise DependencyError(
                    f"Skill {skill_name!r} requires missing tools: {', '.join(missing_tools)}"
                )

        # Track adjacency
        dep_names = set(skill_deps.keys())
        adjacency[skill_name] = dep_names

        # Process each skill dependency
        for dep_name, constraint in skill_deps.items():
            # Track constraints for conflict detection
            if dep_name not in constraints:
                constraints[dep_name] = []
            constraints[dep_name].append((skill_name, constraint))

            # Cycle detection: check if dep_name is in the current path
            if dep_name in path:
                cycle_path = " → ".join(path[path.index(dep_name):] + (dep_name,))
                raise CircularDependencyError(f"Circular dependency detected: {cycle_path}")

            # Self-dependency: a skill that depends on itself
            if dep_name == skill_name:
                cycle_path = f"{skill_name} → {skill_name}"
                raise CircularDependencyError(f"Circular dependency detected: {cycle_path}")

            # Resolve this dependency — do NOT exclude already-visited skills here;
            # we want to find the best version even if it was already resolved,
            # so we can detect version conflicts later.
            dep_result = finder.find(dep_name, constraint)
            if dep_result is None:
                raise DependencyError(
                    f"Skill {skill_name!r} requires {dep_name!r} with constraint {constraint!r}, "
                    f"but no satisfying version was found"
                )

            resolved[dep_name] = dep_result
            if dep_name not in globally_visited:
                queue.append((dep_name, path + (skill_name,)))

    # Conflict detection: verify that all constraints on each dependency are satisfied
    for dep_name, dep_constraints in constraints.items():
        if dep_name not in resolved:
            continue
        resolved_version = resolved[dep_name].version
        for requirer, constraint in dep_constraints:
            spec = _parse_constraint(constraint)
            if spec is not None and resolved_version not in spec:
                raise VersionConflictError(
                    f"Version conflict for {dep_name!r}: "
                    f"resolved to {resolved_version} but {requirer!r} requires {constraint!r}"
                )

    return DependencyGraph(
        roots=frozenset(agent_skill_names),
        resolved=resolved,
        adjacency={k: frozenset(v) for k, v in adjacency.items()},
    )


def find_best_match(
    name: str,
    constraint: str,
    all_skill_roots: Sequence[Path],
    *,
    read_frontmatter: Any = None,
) -> SkillDep | None:
    """Find the best matching version of a skill across all roots.

    This is a convenience wrapper around ``DefaultSkillFinder``.
    """
    finder = DefaultSkillFinder(all_skill_roots, read_frontmatter=read_frontmatter)
    return finder.find(name, constraint)


def verify_tool_dependencies(
    frontmatter: Mapping[str, Any],
    tool_registry: set[str],
) -> list[str]:
    """Verify tool dependencies declared in frontmatter are registered.

    Returns a list of missing tool names (empty = all satisfied).
    """
    tool_deps = _extract_tool_deps(frontmatter)
    return [t for t in tool_deps if t not in tool_registry]