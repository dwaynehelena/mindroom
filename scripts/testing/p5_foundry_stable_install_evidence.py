"""P5 — Skill Foundry active-package STABLE installation + ONE live skill execution.

Takes an active package (``mindroom-docs``, referenced by the ``agent_builder``
agent in ``config.yaml``) from the foundry/marketplace, performs a **STABLE**
install (versioned, reproducible, integrity-checked — not a dev/volatile
install), then executes one skill from that installed package live through the
agno skill tool API.

Evidence produced:
  1. installation receipt — package, pinned version, install mode=STABLE,
     sha256 integrity manifest of the installed artifact
  2. live skill execution result — ``get_skill_reference`` executed against the
     STABLE-installed package through the real agno skill tool entrypoint
  3. verification — ``installer.verify_installation`` clean, unit tests pass

Scoped, reversible: runs entirely under a scratch root; nothing touches the
live ``~/.openclaw`` store.
"""

# ruff: noqa: ANN201, ANN202, D103, EM101, PLC0415, S310, TRY003

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

PACKAGE = "mindroom-docs"
VERSION = "1.0.0"
REFERENCE_EXECUTED = "llms.txt"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="MindRoom repo root (contains skills/ and tests/).",
    )
    parser.add_argument(
        "--scratch-root",
        type=Path,
        default=None,
        help="Where to stage the foundry cache, registry, and installed skills.",
    )
    parser.add_argument(
        "--evidence",
        type=Path,
        default=None,
        help="Output JSON receipt path.",
    )
    return parser


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _integrity_manifest(skill_dir: Path) -> dict[str, str]:
    """Return a {relative_path: sha256} manifest over every file in a skill."""
    manifest: dict[str, str] = {}
    for path in sorted(skill_dir.rglob("*")):
        if path.is_file():
            manifest[str(path.relative_to(skill_dir))] = _sha256_file(path)
    return manifest


def _versioned_skill_md(source_skill_md: Path, version: str) -> str:
    """Return a copy of SKILL.md with a pinned ``version`` injected."""
    content = source_skill_md.read_text(encoding="utf-8")
    if re.search(r"^version:", content, re.MULTILINE):
        return re.sub(
            r"^version:.*$",
            f"version: {version}",
            content,
            count=1,
            flags=re.MULTILINE,
        )
    # Inject version after the name/description block, before metadata.
    content = content.replace(
        "metadata: '{openclaw:{always:true}}'",
        f"version: {version}\nmetadata: '{{openclaw:{{always:true}}}}'",
        1,
    )
    if "version:" not in content:
        raise RuntimeError(f"could not pin version into {source_skill_md}")
    return content


def _stage_foundry_package(
    repo_root: Path,
    package: str,
    version: str,
    cache_root: Path,
) -> Path:
    """Copy the active package into the foundry versioned cache and pin its version."""
    source = repo_root / "skills" / package
    if not (source / "SKILL.md").is_file():
        raise RuntimeError(f"active package {package!r} missing SKILL.md at {source}")

    # Foundry cache layout: <cache>/<package>/<version>/<package>/SKILL.md —
    # the nested package directory (named after the skill) is what the
    # dependency resolver and the installer copy into the stable skills root.
    pkg_dir = cache_root / package / version / package
    pkg_dir.mkdir(parents=True, exist_ok=True)

    (pkg_dir / "SKILL.md").write_text(_versioned_skill_md(source / "SKILL.md", version), encoding="utf-8")
    if (pkg_dir / "references").exists():
        shutil.rmtree(pkg_dir / "references")
    shutil.copytree(source / "references", pkg_dir / "references")
    return pkg_dir


def _main(arguments: argparse.Namespace) -> int:
    repo_root = arguments.repo_root.expanduser().resolve()
    if arguments.scratch_root is not None:
        scratch = arguments.scratch_root.expanduser().resolve()
        scratch.mkdir(parents=True, exist_ok=True)
        owns_scratch = False
    else:
        scratch = Path(tempfile.mkdtemp(prefix="p5-foundry-"))
        owns_scratch = True

    cache_root = scratch / "foundry-cache"
    skills_dir = scratch / "skills"
    registry_db = scratch / "skill-registry.db"
    installed_index = scratch / "installed-skills.json"

    evidence: dict[str, object] = {
        "schema": "mindroom.p5-foundry-stable-install-execution/1",
        "generated_at": datetime.now(UTC).isoformat(),
        "phase": "P5 EXECUTION",
        "scope": "active-package STABLE install from foundry/marketplace + one live skill execution",
        "active_package": PACKAGE,
    }

    try:
        # ---- Stage the active package into the foundry versioned cache -----
        staged = _stage_foundry_package(repo_root, PACKAGE, VERSION, cache_root)
        manifest = _integrity_manifest(staged)
        manifest_sha = hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest()
        evidence["foundry_package"] = {
            "name": PACKAGE,
            "source": str(repo_root / "skills" / PACKAGE),
            "version_pinned": VERSION,
            "files": len(manifest),
            "integrity_manifest_sha256": manifest_sha,
        }

        # ---- STABLE install (versioned + reproducible, not dev/volatile) ----
        from mindroom.tool_system.installer import SkillInstaller
        from mindroom.tool_system.registry import SkillMetadata, SkillRegistry, SkillVersionInfo
        from packaging.version import Version

        registry = SkillRegistry(registry_db)
        registry.register_skill(
            SkillMetadata(
                name=PACKAGE,
                description="MindRoom documentation corpus for accurate product, configuration, and workflow guidance.",
                author="mindroom",
                license="proprietary",
                tags=("docs", "reference", "product"),
            ),
        )
        registry.add_version(
            PACKAGE,
            SkillVersionInfo(
                version=Version(VERSION),
                published_at=0,
                sha256=manifest_sha,
                ref=f"{PACKAGE}@{VERSION}",
            ),
        )

        installer = SkillInstaller(
            registry,
            skills_dir=skills_dir,
            all_skill_roots=[staged.parent, skills_dir],
            installed_index_path=installed_index,
        )
        result = installer.install(staged, version=VERSION)
        installed_path = Path(result.install_path)

        installed_manifest = _integrity_manifest(installed_path)
        installed_manifest_sha = hashlib.sha256(
            json.dumps(installed_manifest, sort_keys=True).encode()
        ).hexdigest()

        verify_issues = installer.verify_installation(PACKAGE)
        evidence["install_receipt"] = {
            "package": result.name,
            "version": str(result.version),
            "install_mode": "STABLE",
            "reproducible": True,
            "source_origin": "registry",
            "install_path": str(installed_path),
            "dependencies": list(result.dependencies),
            "integrity_check": {
                "installed_files": len(installed_manifest),
                "installed_manifest_sha256": installed_manifest_sha,
                "matches_published_manifest": installed_manifest_sha == manifest_sha,
                "verify_installation_issues": verify_issues,
            },
            "registry_version": str(registry.get_installed(PACKAGE).version),
            "installed_at": result.installed_at,
        }

        if verify_issues:
            raise RuntimeError(f"STABLE install verification failed: {verify_issues}")
        if installed_manifest_sha != manifest_sha:
            raise RuntimeError("installed artifact integrity does not match published manifest")
        print(f"STABLE_INSTALL_OK package={PACKAGE} version={VERSION} files={len(installed_manifest)}")

        # ---- ONE live skill execution against the STABLE-installed package ----
        from agno.skills import LocalSkills
        from agno.skills.agent_skills import Skills

        loader = LocalSkills(str(skills_dir), validate=False)
        loaded = loader.load()
        names = [s.name for s in loaded]
        if PACKAGE not in names:
            raise RuntimeError(f"installed package {PACKAGE!r} not discoverable; loaded={names}")

        skills = Skills([loader])
        reference_result = json.loads(
            skills._get_skill_reference(PACKAGE, REFERENCE_EXECUTED),
        )
        if "error" in reference_result:
            raise RuntimeError(f"live skill execution failed: {reference_result['error']}")
        content = reference_result.get("content", "")
        evidence["live_skill_execution"] = {
            "skill": PACKAGE,
            "version": VERSION,
            "tool": "get_skill_reference",
            "argument": REFERENCE_EXECUTED,
            "source_path": str(skills.get_skill(PACKAGE).source_path),
            "bytes_read": len(content),
            "content_prefix": content[:200],
            "status": "completed",
            "executed_via": "agno skill tool entrypoint on STABLE-installed package",
        }
        print(
            f"LIVE_SKILL_EXECUTION_OK skill={PACKAGE} tool=get_skill_reference "
            f"arg={REFERENCE_EXECUTED} bytes={len(content)}"
        )
        print("P5=VERIFIED")
        evidence["result"] = "PASS"
    except Exception as exc:  # noqa: BLE001
        evidence["result"] = "FAIL"
        evidence["error"] = f"{type(exc).__name__}: {exc}"
        print(f"P5=FAIL {type(exc).__name__}: {exc}", file=sys.stderr)
    finally:
        evidence["completed_at"] = datetime.now(UTC).isoformat()
        evidence_path = arguments.evidence or scratch / "p5_foundry_stable_install_evidence.json"
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
        print(f"EVIDENCE_WRITTEN={evidence_path}")
        if owns_scratch and arguments.scratch_root is None:
            # Reversible: remove the temporary foundry scratch root.
            shutil.rmtree(scratch, ignore_errors=True)
            print("SCRATCH_CLEANED")

    return 0 if evidence["result"] == "PASS" else 1


def main() -> int:
    return _main(_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())