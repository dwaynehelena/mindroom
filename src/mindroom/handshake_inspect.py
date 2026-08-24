"""C8 operator ``inspect-openclaw`` (schema ``mindroom.handshake-inspect/1``).

U4 inspects the local OpenClaw install, not only the MindRoom extension point.
A usable surface is an existing nullary ``Callable[[], None]`` that can be bound
into ``MeshEnrollmentCoordinator.handshake`` without new HTTP, a new credential,
or general-purpose mesh routing.

OpenClaw DM pairing, device/node pairing, and the Gateway WebSocket ``connect``
handshake are not that surface. When inspection finds no usable surface, FR4.2
is not implemented and the unread ``MINDROOM_MESH_ENROLLMENT`` switch stays
removed (FR4.5).
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

HandshakeInspectOutcome = Literal["surface_found", "surface_absent"]
HANDSHAKE_INSPECT_SCHEMA = "mindroom.handshake-inspect/1"
MESH_ENROLLMENT_ENV = "MINDROOM_MESH_ENROLLMENT"
_MESH_HANDSHAKE_MARKERS = (
    "mesh-enrollment",
    "mesh_enrollment",
    "mindroom.mesh-enrollment",
    "mindroom.handshake",
)
_NOT_MESH_HANDSHAKE_COMMANDS = frozenset(
    {
        "pairing",
        "nodes",
        "devices",
        "gateway",
        "node",
        "connect",
        "doctor",
        "help",
    }
)


@dataclass(frozen=True, slots=True)
class OpenClawInstallProbe:
    """Facts from inspecting a local OpenClaw install (FR4.1)."""

    executable: str | None
    version: str | None
    help_text: str
    commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExtensionPointProbe:
    """Facts from the MindRoom handshake extension point."""

    handshake: Callable[[], None] | None
    handshake_enabled: bool = False
    enrollment_flag_reader: str | None = None


@dataclass(frozen=True, slots=True)
class HandshakeInspectResult:
    """C8 inspect outcome. No HTTP payload; in-process only."""

    outcome: HandshakeInspectOutcome
    implement_fr4_2: bool
    openclaw_install: str | None
    extension_point: str | None
    reasons: tuple[str, ...]
    unread_switch_removed: bool
    schema: str = HANDSHAKE_INSPECT_SCHEMA


def probe_local_openclaw(
    *,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> OpenClawInstallProbe:
    """Inspect the locally installed OpenClaw CLI, not the MindRoom adapter."""
    executable = which("openclaw")
    if executable is None:
        return OpenClawInstallProbe(executable=None, version=None, help_text="", commands=())
    help_text = _run_openclaw((executable, "--help"), env=env, run=run)
    version_text = _run_openclaw((executable, "--version"), env=env, run=run)
    version = next((line.strip() for line in version_text.splitlines() if line.strip()), None)
    return OpenClawInstallProbe(
        executable=executable,
        version=version,
        help_text=help_text,
        commands=_commands_from_help(help_text),
    )


def probe_mindroom_extension_point() -> ExtensionPointProbe:
    """Inspect the MindRoom handshake slot without constructing MeshGateway."""
    try:
        enrollment = importlib.import_module("mindroom.mesh.enrollment")
    except ImportError:
        return ExtensionPointProbe(handshake=None)
    reader = getattr(enrollment, "enrollment_flag_enabled", None)
    enrollment_flag_reader = (
        "mindroom.mesh.enrollment.enrollment_flag_enabled" if callable(reader) else None
    )
    coordinator = getattr(enrollment, "MeshEnrollmentCoordinator", None)
    handshake = getattr(coordinator, "handshake", None) if coordinator is not None else None
    if not callable(handshake):
        handshake = None
    handshake_enabled = bool(getattr(coordinator, "handshake_enabled", False)) if coordinator is not None else False
    return ExtensionPointProbe(
        handshake=handshake,
        handshake_enabled=handshake_enabled,
        enrollment_flag_reader=enrollment_flag_reader,
    )


def unread_mesh_enrollment_switch_present(*, env: Mapping[str, str] | None = None) -> bool:
    """True when the unread ``MINDROOM_MESH_ENROLLMENT`` switch still exists in source.

    Pycache-only leftovers from a prior checkout do not count as a live switch.
    """
    source = os.environ if env is None else env
    if source.get(MESH_ENROLLMENT_ENV):
        return True
    package_root = Path(__file__).resolve().parent
    return (package_root / "config" / "mesh.py").is_file() or (
        package_root / "mesh" / "enrollment.py"
    ).is_file()


def inspect_openclaw(
    *,
    openclaw: OpenClawInstallProbe | None = None,
    extension: ExtensionPointProbe | None = None,
    env: Mapping[str, str] | None = None,
) -> HandshakeInspectResult:
    """Run operator ``inspect-openclaw`` and return the C8 closed alternative."""
    install = openclaw if openclaw is not None else probe_local_openclaw(env=env)
    point = extension if extension is not None else probe_mindroom_extension_point()
    switch_present = unread_mesh_enrollment_switch_present(env=env)
    reasons: list[str] = []
    usable_openclaw = _openclaw_exposes_nullary_mesh_handshake(install, reasons)
    existing_callable = callable(point.handshake)
    if not existing_callable:
        reasons.append("MindRoom handshake extension point has no existing Callable[[], None]")
    if switch_present:
        reasons.append("unread MINDROOM_MESH_ENROLLMENT switch is still present")
    if usable_openclaw and existing_callable:
        return HandshakeInspectResult(
            outcome="surface_found",
            implement_fr4_2=True,
            openclaw_install=install.executable,
            extension_point="mindroom.mesh.enrollment.MeshEnrollmentCoordinator.handshake",
            reasons=tuple(reasons) or ("existing nullary handshake callable is bindable",),
            unread_switch_removed=not switch_present,
        )
    reasons.append("FR4.2 not implemented; FR4.5 keeps the unread mesh enrollment switch removed")
    return HandshakeInspectResult(
        outcome="surface_absent",
        implement_fr4_2=False,
        openclaw_install=install.executable,
        extension_point=None,
        reasons=tuple(reasons),
        unread_switch_removed=not switch_present,
    )


def inspect_openclaw_for_this_install() -> HandshakeInspectResult:
    """Production inspect of this process's OpenClaw install and extension point."""
    return inspect_openclaw()


def _openclaw_exposes_nullary_mesh_handshake(install: OpenClawInstallProbe, reasons: list[str]) -> bool:
    if install.executable is None:
        reasons.append("local OpenClaw executable was not found on PATH")
        return False
    haystack = "\n".join((install.help_text, " ".join(install.commands))).lower()
    if any(marker in haystack for marker in _MESH_HANDSHAKE_MARKERS):
        return True
    advertised = tuple(command for command in install.commands if command not in _NOT_MESH_HANDSHAKE_COMMANDS)
    extra = f" (extra commands: {', '.join(advertised)})" if advertised else ""
    reasons.append(
        "local OpenClaw exposes pairing/nodes/gateway-connect, not a nullary mesh-enrollment handshake"
        + extra
    )
    return False


def _commands_from_help(help_text: str) -> tuple[str, ...]:
    commands: list[str] = []
    in_commands = False
    for raw in help_text.splitlines():
        line = raw.strip()
        if line.lower().startswith("commands:"):
            in_commands = True
            continue
        if not in_commands:
            continue
        if not line:
            if commands:
                break
            continue
        name = line.split()[0]
        ident = name.replace("-", "_")
        if ident.isidentifier():
            commands.append(name)
    return tuple(commands)


def _run_openclaw(
    argv: Sequence[str],
    *,
    env: Mapping[str, str] | None,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> str:
    try:
        completed = run(
            list(argv),
            check=False,
            capture_output=True,
            text=True,
            env=None if env is None else dict(env),
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (completed.stdout or "") + (completed.stderr or "")