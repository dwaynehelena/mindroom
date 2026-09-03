"""Skill Foundry CLI commands: search, install, uninstall, update, list, info, verify.

These commands delegate all mutations to the ``SkillInstaller`` and
``SkillRegistry`` modules so the marketplace UI and the CLI share one code
path for active-package installation.
"""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import TYPE_CHECKING

import typer

from .config import console

if TYPE_CHECKING:
    from collections.abc import Sequence


skill_app = typer.Typer(help="Manage Skill Foundry skills (install, update, search, verify).")

_OPENCLAW_HOME = Path.home() / ".openclaw"
_SKILLS_DIR = _OPENCLAW_HOME / "skills"
_REGISTRY_DB = _OPENCLAW_HOME / "skill-registry.db"
_INSTALLED_INDEX = _OPENCLAW_HOME / "installed-skills.json"


def _build_installer() -> tuple[object, object]:
    """Build a ``SkillRegistry`` and ``SkillInstaller`` bound to the default paths."""
    from mindroom.tool_system.installer import SkillInstaller
    from mindroom.tool_system.registry import SkillRegistry

    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(_REGISTRY_DB)
    installer = SkillInstaller(
        registry,
        skills_dir=_SKILLS_DIR,
        all_skill_roots=[_SKILLS_DIR],
        installed_index_path=_INSTALLED_INDEX,
    )
    return registry, installer


@skill_app.command("list")
def skill_list() -> None:
    """List installed skills and their versions."""
    from mindroom.tool_system.registry import SkillRegistry

    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(_REGISTRY_DB)
    installed = registry.list_installed()
    if not installed:
        console.print("No skills installed.")
        return
    for skill in installed:
        console.print(f"{skill.name}  v{skill.version}  ({skill.source})")


@skill_app.command("info")
def skill_info(
    name: str = typer.Argument(..., help="Skill name."),
) -> None:
    """Show details for one installed skill."""
    registry, _installer = _build_installer()
    installed = registry.get_installed(name)
    if installed is None:
        console.print(f"[red]Skill {name!r} is not installed.[/red]")
        raise typer.Exit(1)
    meta = registry.get_skill(name)
    console.print(f"[bold]{name}[/bold]")
    if meta is not None:
        console.print(f"  Description: {meta.description}")
        console.print(f"  Author:      {meta.author or '-'}")
        console.print(f"  License:     {meta.license or '-'}")
        console.print(f"  Tags:        {', '.join(meta.tags) or '-'}")
    console.print(f"  Version:     {installed.version}")
    console.print(f"  Path:        {installed.install_path}")
    console.print(f"  Installed:   {installed.installed_at:.0f}")


@skill_app.command("install")
def skill_install(
    spec: str = typer.Argument(
        ...,
        help="Skill to install as NAME or NAME@VERSION (VERSION may be a semver range).",
    ),
    force: bool = typer.Option(False, "--force", help="Re-install even if the same version is present."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Validate and resolve without copying files."),
) -> None:
    """Install a skill from the local registry or a source directory."""
    from mindroom.tool_system.installer import InstallError, ValidationError

    registry, installer = _build_installer()

    try:
        if "/" in spec or (Path(spec).exists() and (Path(spec) / "SKILL.md").exists()):
            result = installer.install(Path(spec), force=force, dry_run=dry_run)
        else:
            name, _, version = spec.partition("@")
            result = installer.install_from_registry(
                name.strip(),
                version=version.strip() or None,
                force=force,
                dry_run=dry_run,
            )
    except (InstallError, ValidationError) as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from None

    verb = "Would install" if dry_run else "Installed"
    console.print(f"[green]{verb}[/green] {result.name} v{result.version}")
    if result.dependencies:
        console.print(f"  Dependencies: {', '.join(result.dependencies)}")


@skill_app.command("uninstall")
def skill_uninstall(
    name: str = typer.Argument(..., help="Skill name to uninstall."),
) -> None:
    """Uninstall a skill, removing its files and registry entry."""
    _registry, installer = _build_installer()
    if not installer.uninstall(name):
        console.print(f"[yellow]Skill {name!r} was not installed.[/yellow]")
        raise typer.Exit(1)
    console.print(f"[green]Uninstalled[/green] {name}")


@skill_app.command("update")
def skill_update(
    name: str | None = typer.Argument(None, help="Skill to update. Omit to update all."),
) -> None:
    """Update one skill (or all installed skills) to the latest available version."""
    from mindroom.tool_system.installer import InstallError

    _registry, installer = _build_installer()

    if name is not None:
        try:
            result = installer.update(name)
        except InstallError as exc:
            console.print(f"[red]{exc}[/red]")
            raise typer.Exit(1) from None
        if result is None:
            console.print(f"{name} is already up to date.")
        else:
            console.print(f"[green]Updated[/green] {result.name} → v{result.version}")
        return

    for installed in _registry.list_installed():
        try:
            result = installer.update(installed.name)
        except InstallError as exc:
            console.print(f"[red]Failed to update {installed.name}:[/red] {exc}")
            continue
        if result is not None:
            console.print(f"[green]Updated[/green] {result.name} → v{result.version}")
        else:
            console.print(f"{installed.name} is already up to date.")


@skill_app.command("search")
def skill_search(
    query: str = typer.Argument("", help="Free-text search (matches name, description, author, tags)."),
    tags: str | None = typer.Option(None, "--tag", help="Comma-separated tags to filter by."),
) -> None:
    """Search the registry index for available skills."""
    from mindroom.tool_system.index import InstalledSkillsIndex, RegistryIndexFetcher, SkillIndex
    from mindroom.tool_system.registry import SkillRegistry

    _SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    registry = SkillRegistry(_REGISTRY_DB)
    index = SkillIndex(
        registry,
        index_fetcher=RegistryIndexFetcher(),
        installed_index=InstalledSkillsIndex(_INSTALLED_INDEX),
    )
    tag_list = [tag.strip() for tag in tags.split(",") if tag.strip()] if tags else None
    results = index.search(query, tags=tag_list)
    if not results:
        console.print("No skills found.")
        return
    for result in results:
        status = f"installed {result.installed_version}" if result.installed_version else "available"
        console.print(f"{result.name}  v{result.latest_version}  [{status}]  {result.description}")


@skill_app.command("verify")
def skill_verify(
    name: str | None = typer.Argument(None, help="Skill to verify. Omit to verify all installed."),
) -> None:
    """Check that installed skill files and declared dependencies are consistent."""
    registry, installer = _build_installer()

    targets = [name] if name is not None else [s.name for s in registry.list_installed()]
    if not targets:
        console.print("No installed skills to verify.")
        return

    all_ok = True
    for target in targets:
        issues = installer.verify_installation(target)
        if issues:
            all_ok = False
            console.print(f"[red]{target}:[/red]")
            for issue in issues:
                console.print(f"  - {issue}")
        else:
            console.print(f"[green]OK[/green] {target}")
    if not all_ok:
        raise typer.Exit(1)