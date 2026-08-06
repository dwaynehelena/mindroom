"""Tests for skill dependency resolution (skill_deps.py)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from mindroom.tool_system.skill_deps import (
    CircularDependencyError,
    DefaultSkillFinder,
    DependencyError,
    DependencyGraph,
    SkillDep,
    VersionConflictError,
    _best_match,
    _collect_versions,
    _extract_skill_deps,
    _extract_tool_deps,
    _parse_constraint,
    _parse_version,
    _read_skill_frontmatter,
    _SkillVersion,
    find_best_match,
    resolve_dependencies,
    verify_tool_dependencies,
)
from packaging.specifiers import SpecifierSet
from packaging.version import Version

if TYPE_CHECKING:
    from collections.abc import Mapping


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_skill_dir(tmp_path: Path, name: str, version: str, deps: dict[str, str] | None = None) -> Path:
    """Create a minimal skill directory with SKILL.md."""
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    lines = ["---", f"name: {name}", f"description: A test skill", f"version: {version}"]

    if deps:
        lines.append("openclaw:")
        lines.append("  depends_on:")
        lines.append("    skills:")
        for dep_name, constraint in deps.items():
            lines.append(f"      {dep_name}: {constraint!r}")

    lines.append("---")
    lines.append("")
    lines.append("# Body")

    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


def _make_skill_dir_with_tools(
    tmp_path: Path, name: str, version: str, tools: list[str] | None = None
) -> Path:
    """Create a skill directory with tool dependencies."""
    skill_dir = tmp_path / name
    skill_dir.mkdir(parents=True, exist_ok=True)

    lines = ["---", f"name: {name}", f"description: A test skill", f"version: {version}"]

    if tools:
        lines.append("openclaw:")
        lines.append("  depends_on:")
        lines.append("    tools:")
        for tool in tools:
            lines.append(f"      - {tool}")

    lines.append("---")
    lines.append("")
    lines.append("# Body")

    (skill_dir / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")
    return skill_dir


# ---------------------------------------------------------------------------
# _parse_version
# ---------------------------------------------------------------------------


class TestParseVersion:
    def test_valid_semver(self) -> None:
        v = _parse_version("1.2.3")
        assert v is not None
        assert v == Version("1.2.3")

    def test_valid_with_pre_release(self) -> None:
        v = _parse_version("2.0.0-alpha.1")
        assert v is not None
        assert v == Version("2.0.0-alpha.1")

    def test_none(self) -> None:
        assert _parse_version(None) is None

    def test_empty_string(self) -> None:
        assert _parse_version("") is None

    def test_invalid(self) -> None:
        assert _parse_version("not-a-version") is None

    def test_whitespace(self) -> None:
        v = _parse_version("  1.0.0  ")
        assert v is not None
        assert v == Version("1.0.0")


# ---------------------------------------------------------------------------
# _parse_constraint
# ---------------------------------------------------------------------------


class TestParseConstraint:
    def test_exact(self) -> None:
        s = _parse_constraint("==1.0.0")
        assert s is not None
        assert Version("1.0.0") in s

    def test_range(self) -> None:
        s = _parse_constraint(">=1.0.0,<2.0.0")
        assert s is not None
        assert Version("1.5.0") in s
        assert Version("2.0.0") not in s

    def test_caret(self) -> None:
        s = _parse_constraint("^1.2.0")
        assert s is not None
        assert Version("1.2.0") in s
        assert Version("1.9.9") in s
        assert Version("2.0.0") not in s

    def test_tilde(self) -> None:
        s = _parse_constraint("~1.2.3")
        assert s is not None
        assert Version("1.2.3") in s
        assert Version("1.2.9") in s
        assert Version("1.3.0") not in s

    def test_wildcard(self) -> None:
        s = _parse_constraint("1.*")
        assert s is not None
        assert Version("1.0.0") in s
        assert Version("1.5.0") in s
        assert Version("2.0.0") not in s

    def test_star(self) -> None:
        s = _parse_constraint("*")
        assert s is not None
        assert Version("99.99.99") in s

    def test_none(self) -> None:
        assert _parse_constraint(None) is None

    def test_invalid(self) -> None:
        assert _parse_constraint("not-a-constraint") is None


# ---------------------------------------------------------------------------
# _best_match
# ---------------------------------------------------------------------------


class TestBestMatch:
    def test_simple(self) -> None:
        versions = [
            _SkillVersion(Version("1.0.0"), Path("/a"), "user"),
            _SkillVersion(Version("1.5.0"), Path("/b"), "user"),
            _SkillVersion(Version("2.0.0"), Path("/c"), "user"),
        ]
        constraint = SpecifierSet(">=1.0.0,<2.0.0")
        best = _best_match(versions, constraint)
        assert best is not None
        assert best.version == Version("1.5.0")

    def test_no_match(self) -> None:
        versions = [
            _SkillVersion(Version("1.0.0"), Path("/a"), "user"),
        ]
        constraint = SpecifierSet(">=2.0.0")
        assert _best_match(versions, constraint) is None

    def test_empty_list(self) -> None:
        constraint = _parse_constraint("*")
        assert constraint is not None
        assert _best_match([], constraint) is None

    def test_returns_highest(self) -> None:
        versions = [
            _SkillVersion(Version("1.0.0"), Path("/a"), "user"),
            _SkillVersion(Version("1.0.1"), Path("/b"), "user"),
            _SkillVersion(Version("1.1.0"), Path("/c"), "user"),
        ]
        constraint = SpecifierSet(">=1.0.0")
        best = _best_match(versions, constraint)
        assert best is not None
        assert best.version == Version("1.1.0")


# ---------------------------------------------------------------------------
# _extract_skill_deps
# ---------------------------------------------------------------------------


class TestExtractSkillDeps:
    def test_empty(self) -> None:
        assert _extract_skill_deps({}) == {}

    def test_no_openclaw(self) -> None:
        assert _extract_skill_deps({"name": "test"}) == {}

    def test_no_depends_on(self) -> None:
        assert _extract_skill_deps({"openclaw": {"always": True}}) == {}

    def test_no_skills(self) -> None:
        assert _extract_skill_deps({"openclaw": {"depends_on": {"tools": ["file_read"]}}}) == {}

    def test_with_deps(self) -> None:
        fm = {
            "openclaw": {
                "depends_on": {
                    "skills": {
                        "base-utils": ">=1.0.0",
                        "web-scraper": "^2.0.0",
                    }
                }
            }
        }
        deps = _extract_skill_deps(fm)
        assert deps == {"base-utils": ">=1.0.0", "web-scraper": "^2.0.0"}

    def test_ignores_non_string_values(self) -> None:
        fm = {
            "openclaw": {
                "depends_on": {
                    "skills": {
                        "good": ">=1.0.0",
                        "bad": 123,
                    }
                }
            }
        }
        deps = _extract_skill_deps(fm)
        assert deps == {"good": ">=1.0.0"}


# ---------------------------------------------------------------------------
# _extract_tool_deps
# ---------------------------------------------------------------------------


class TestExtractToolDeps:
    def test_empty(self) -> None:
        assert _extract_tool_deps({}) == []

    def test_with_tools(self) -> None:
        fm = {
            "openclaw": {
                "depends_on": {
                    "tools": ["file_read", "web_search", "shell_exec"]
                }
            }
        }
        tools = _extract_tool_deps(fm)
        assert tools == ["file_read", "web_search", "shell_exec"]

    def test_ignores_non_strings(self) -> None:
        fm = {
            "openclaw": {
                "depends_on": {
                    "tools": ["file_read", 42, None]
                }
            }
        }
        tools = _extract_tool_deps(fm)
        assert tools == ["file_read"]


# ---------------------------------------------------------------------------
# _collect_versions
# ---------------------------------------------------------------------------


class TestCollectVersions:
    def test_finds_skill_in_root(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "my-skill", "1.2.3")
        versions = _collect_versions("my-skill", [tmp_path])
        assert len(versions) == 1
        assert versions[0].version == Version("1.2.3")

    def test_finds_multiple_roots(self, tmp_path: Path) -> None:
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        root1.mkdir()
        root2.mkdir()
        _make_skill_dir(root1, "my-skill", "1.0.0")
        _make_skill_dir(root2, "my-skill", "2.0.0")
        versions = _collect_versions("my-skill", [root1, root2])
        assert len(versions) == 2

    def test_case_insensitive_match(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "My-Skill", "1.0.0")
        versions = _collect_versions("my-skill", [tmp_path])
        assert len(versions) == 1

    def test_returns_dev_version_when_missing(self, tmp_path: Path) -> None:
        skill_dir = tmp_path / "no-version"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            "---\nname: no-version\ndescription: test\n---\n\nBody",
            encoding="utf-8",
        )
        versions = _collect_versions("no-version", [tmp_path])
        assert len(versions) == 1
        assert versions[0].version == Version("0.0.0-dev")

    def test_skips_missing_roots(self) -> None:
        versions = _collect_versions("test", [Path("/nonexistent/path")])
        assert versions == []


# ---------------------------------------------------------------------------
# DefaultSkillFinder
# ---------------------------------------------------------------------------


class TestDefaultSkillFinder:
    def test_find_exact(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "my-skill", "1.2.3")
        finder = DefaultSkillFinder([tmp_path])
        dep = finder.find("my-skill", "==1.2.3")
        assert dep is not None
        assert dep.name == "my-skill"
        assert dep.version == Version("1.2.3")

    def test_find_range(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "my-skill", "1.5.0")
        finder = DefaultSkillFinder([tmp_path])
        dep = finder.find("my-skill", ">=1.0.0,<2.0.0")
        assert dep is not None
        assert dep.version == Version("1.5.0")

    def test_not_found(self, tmp_path: Path) -> None:
        finder = DefaultSkillFinder([tmp_path])
        dep = finder.find("nonexistent", ">=1.0.0")
        assert dep is None

    def test_exclude(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "my-skill", "1.0.0")
        finder = DefaultSkillFinder([tmp_path])
        dep = finder.find("my-skill", ">=1.0.0", exclude={"my-skill"})
        assert dep is None

    def test_invalid_constraint(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "my-skill", "1.0.0")
        finder = DefaultSkillFinder([tmp_path])
        with pytest.raises(DependencyError, match="Invalid version constraint"):
            finder.find("my-skill", "not-a-constraint")


# ---------------------------------------------------------------------------
# resolve_dependencies
# ---------------------------------------------------------------------------


class TestResolveDependencies:
    def test_no_deps(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "standalone", "1.0.0")
        graph = resolve_dependencies(["standalone"], [tmp_path])
        assert isinstance(graph, DependencyGraph)
        assert "standalone" in graph.resolved
        assert graph.roots == frozenset({"standalone"})

    def test_simple_dep(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "base", "1.0.0")
        _make_skill_dir(tmp_path, "plugin", "2.0.0", deps={"base": ">=1.0.0"})
        graph = resolve_dependencies(["plugin"], [tmp_path])
        assert "plugin" in graph.resolved
        assert "base" in graph.resolved
        assert graph.resolved["base"].version == Version("1.0.0")

    def test_transitive_deps(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "core", "1.0.0")
        _make_skill_dir(tmp_path, "middle", "1.0.0", deps={"core": ">=1.0.0"})
        _make_skill_dir(tmp_path, "top", "1.0.0", deps={"middle": ">=1.0.0"})
        graph = resolve_dependencies(["top"], [tmp_path])
        assert "top" in graph.resolved
        assert "middle" in graph.resolved
        assert "core" in graph.resolved

    def test_circular_dependency(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "a", "1.0.0", deps={"b": ">=1.0.0"})
        _make_skill_dir(tmp_path, "b", "1.0.0", deps={"a": ">=1.0.0"})
        with pytest.raises(CircularDependencyError, match="Circular dependency"):
            resolve_dependencies(["a"], [tmp_path])

    def test_self_dependency(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "self-dep", "1.0.0", deps={"self-dep": ">=1.0.0"})
        with pytest.raises(CircularDependencyError, match="Circular dependency"):
            resolve_dependencies(["self-dep"], [tmp_path])

    def test_missing_dep(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "consumer", "1.0.0", deps={"missing": ">=1.0.0"})
        with pytest.raises(DependencyError, match="missing"):
            resolve_dependencies(["consumer"], [tmp_path])

    def test_version_conflict(self, tmp_path: Path) -> None:
        # Two skills depend on the same dep with incompatible constraints.
        # Use separate roots for different versions.
        root_v1 = tmp_path / "v1"
        root_v2 = tmp_path / "v2"
        root_v1.mkdir()
        root_v2.mkdir()
        _make_skill_dir(root_v1, "shared", "1.0.0")
        _make_skill_dir(root_v2, "shared", "2.0.0")
        _make_skill_dir(tmp_path, "a", "1.0.0", deps={"shared": ">=1.0.0,<2.0.0"})
        _make_skill_dir(tmp_path, "b", "1.0.0", deps={"shared": ">=2.0.0"})
        with pytest.raises(VersionConflictError, match="Version conflict"):
            resolve_dependencies(["a", "b"], [tmp_path, root_v1, root_v2])

    def test_tool_deps_satisfied(self, tmp_path: Path) -> None:
        _make_skill_dir_with_tools(tmp_path, "tool-user", "1.0.0", tools=["file_read", "web_search"])
        graph = resolve_dependencies(
            ["tool-user"],
            [tmp_path],
            tool_registry={"file_read", "web_search", "shell_exec"},
        )
        assert "tool-user" in graph.resolved

    def test_tool_deps_missing(self, tmp_path: Path) -> None:
        _make_skill_dir_with_tools(tmp_path, "tool-user", "1.0.0", tools=["nonexistent_tool"])
        with pytest.raises(DependencyError, match="missing tools"):
            resolve_dependencies(
                ["tool-user"],
                [tmp_path],
                tool_registry={"file_read"},
            )

    def test_multiple_entry_points(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "common", "1.0.0")
        _make_skill_dir(tmp_path, "alpha", "1.0.0", deps={"common": ">=1.0.0"})
        _make_skill_dir(tmp_path, "beta", "1.0.0", deps={"common": ">=1.0.0"})
        graph = resolve_dependencies(["alpha", "beta"], [tmp_path])
        assert "alpha" in graph.resolved
        assert "beta" in graph.resolved
        assert "common" in graph.resolved

    def test_adjacency(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "base", "1.0.0")
        _make_skill_dir(tmp_path, "derived", "1.0.0", deps={"base": ">=1.0.0"})
        graph = resolve_dependencies(["derived"], [tmp_path])
        assert "derived" in graph.adjacency
        assert "base" in graph.adjacency.get("derived", frozenset())


# ---------------------------------------------------------------------------
# find_best_match
# ---------------------------------------------------------------------------


class TestFindBestMatch:
    def test_found(self, tmp_path: Path) -> None:
        _make_skill_dir(tmp_path, "my-skill", "1.2.3")
        dep = find_best_match("my-skill", ">=1.0.0", [tmp_path])
        assert dep is not None
        assert dep.name == "my-skill"
        assert dep.version == Version("1.2.3")

    def test_not_found(self, tmp_path: Path) -> None:
        dep = find_best_match("nonexistent", ">=1.0.0", [tmp_path])
        assert dep is None


# ---------------------------------------------------------------------------
# verify_tool_dependencies
# ---------------------------------------------------------------------------


class TestVerifyToolDependencies:
    def test_all_satisfied(self) -> None:
        fm = {"openclaw": {"depends_on": {"tools": ["file_read", "web_search"]}}}
        missing = verify_tool_dependencies(fm, {"file_read", "web_search", "shell_exec"})
        assert missing == []

    def test_some_missing(self) -> None:
        fm = {"openclaw": {"depends_on": {"tools": ["file_read", "missing_tool"]}}}
        missing = verify_tool_dependencies(fm, {"file_read"})
        assert missing == ["missing_tool"]

    def test_no_tools(self) -> None:
        fm = {}
        missing = verify_tool_dependencies(fm, {"file_read"})
        assert missing == []


# ---------------------------------------------------------------------------
# Integration: end-to-end resolution
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_complex_dependency_tree(self, tmp_path: Path) -> None:
        """Build a realistic dependency tree and resolve it."""
        # Level 0: leaf dependencies
        _make_skill_dir(tmp_path, "utils", "1.0.0")
        _make_skill_dir(tmp_path, "data-lib", "2.0.0")

        # Level 1: depends on utils
        _make_skill_dir(tmp_path, "file-processor", "1.5.0", deps={"utils": ">=1.0.0"})
        _make_skill_dir(tmp_path, "web-scraper", "2.0.0", deps={"utils": ">=1.0.0", "data-lib": ">=2.0.0"})

        # Level 2: top-level skill
        _make_skill_dir(
            tmp_path,
            "my-agent-toolkit",
            "3.0.0",
            deps={"file-processor": ">=1.0.0", "web-scraper": ">=2.0.0"},
        )

        graph = resolve_dependencies(["my-agent-toolkit"], [tmp_path])
        assert len(graph.resolved) == 5
        assert graph.resolved["utils"].version == Version("1.0.0")
        assert graph.resolved["data-lib"].version == Version("2.0.0")
        assert graph.resolved["file-processor"].version == Version("1.5.0")
        assert graph.resolved["web-scraper"].version == Version("2.0.0")
        assert graph.resolved["my-agent-toolkit"].version == Version("3.0.0")

    def test_precedence_order(self, tmp_path: Path) -> None:
        """Higher-priority roots should win when same skill exists in multiple roots."""
        low_prio = tmp_path / "low"
        high_prio = tmp_path / "high"
        low_prio.mkdir()
        high_prio.mkdir()

        _make_skill_dir(low_prio, "shared", "1.0.0")
        _make_skill_dir(high_prio, "shared", "2.0.0")

        # When both roots are searched, the finder should find both versions
        # and return the highest satisfying one
        dep = find_best_match("shared", ">=1.0.0", [low_prio, high_prio])
        assert dep is not None
        assert dep.version == Version("2.0.0")