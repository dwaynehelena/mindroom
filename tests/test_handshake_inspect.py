"""U4 handshake slice tests for C8 (operator inspect-openclaw)."""

# ruff: noqa: ANN001, D103

from __future__ import annotations

import subprocess
from pathlib import Path

from mindroom.handshake_inspect import (
    HANDSHAKE_INSPECT_SCHEMA,
    MESH_ENROLLMENT_ENV,
    ExtensionPointProbe,
    OpenClawInstallProbe,
    inspect_openclaw,
    probe_local_openclaw,
    unread_mesh_enrollment_switch_present,
)


def _openclaw_help() -> str:
    return (
        "Usage: openclaw [options] [command]\n"
        "\n"
        "Commands:\n"
        "  pairing     Secure DM pairing\n"
        "  nodes       Manage gateway-owned nodes\n"
        "  devices     Device pairing\n"
        "  gateway     Run, inspect, and query the WebSocket Gateway\n"
        "  help        Display help for command\n"
        "\n"
        "Docs: https://docs.openclaw.ai\n"
    )


def test_c8_schema_is_handshake_inspect_v1() -> None:
    result = inspect_openclaw(
        openclaw=OpenClawInstallProbe(None, None, "", ()),
        extension=ExtensionPointProbe(None),
        env={},
    )
    assert result.schema == HANDSHAKE_INSPECT_SCHEMA


def test_c8_surface_absent_when_openclaw_missing() -> None:
    result = inspect_openclaw(
        openclaw=OpenClawInstallProbe(executable=None, version=None, help_text="", commands=()),
        extension=ExtensionPointProbe(handshake=None),
        env={},
    )
    assert result.outcome == "surface_absent"
    assert result.implement_fr4_2 is False
    assert result.unread_switch_removed is True
    assert result.extension_point is None
    assert any("not found" in reason for reason in result.reasons)


def test_c8_pairing_nodes_gateway_connect_is_not_a_usable_surface() -> None:
    result = inspect_openclaw(
        openclaw=OpenClawInstallProbe(
            executable="/usr/bin/openclaw",
            version="OpenClaw 2026.7.1-2",
            help_text=_openclaw_help(),
            commands=("pairing", "nodes", "devices", "gateway", "help"),
        ),
        extension=ExtensionPointProbe(handshake=None),
        env={},
    )
    assert result.outcome == "surface_absent"
    assert result.implement_fr4_2 is False
    assert result.openclaw_install == "/usr/bin/openclaw"
    assert any("not a nullary mesh-enrollment handshake" in reason for reason in result.reasons)


def test_c8_existing_callable_without_openclaw_mesh_surface_does_not_bind() -> None:
    def leftover_handshake() -> None:
        return None

    result = inspect_openclaw(
        openclaw=OpenClawInstallProbe(
            executable="/usr/bin/openclaw",
            version="OpenClaw 2026.7.1-2",
            help_text=_openclaw_help(),
            commands=("pairing", "nodes", "gateway"),
        ),
        extension=ExtensionPointProbe(handshake=leftover_handshake, handshake_enabled=False),
        env={},
    )
    assert result.outcome == "surface_absent"
    assert result.implement_fr4_2 is False


def test_c8_surface_found_only_with_openclaw_mesh_marker_and_existing_callable() -> None:
    def handshake() -> None:
        return None

    result = inspect_openclaw(
        openclaw=OpenClawInstallProbe(
            executable="/usr/bin/openclaw",
            version="OpenClaw 2026.7.1-2",
            help_text="Commands:\n  mesh-enrollment  Bind worker identity\n",
            commands=("mesh-enrollment",),
        ),
        extension=ExtensionPointProbe(handshake=handshake, handshake_enabled=False),
        env={},
    )
    assert result.outcome == "surface_found"
    assert result.implement_fr4_2 is True
    assert result.extension_point == "mindroom.mesh.enrollment.MeshEnrollmentCoordinator.handshake"


def test_c8_env_switch_is_detected_when_present() -> None:
    assert unread_mesh_enrollment_switch_present(env={MESH_ENROLLMENT_ENV: "1"}) is True
    result = inspect_openclaw(
        openclaw=OpenClawInstallProbe(None, None, "", ()),
        extension=ExtensionPointProbe(None),
        env={MESH_ENROLLMENT_ENV: "1"},
    )
    assert result.unread_switch_removed is False
    assert result.implement_fr4_2 is False


def test_c8_fr45_unread_switch_is_removed_in_this_tree() -> None:
    assert unread_mesh_enrollment_switch_present(env={}) is False
    src = Path("src/mindroom")
    assert not (src / "config" / "mesh.py").exists()
    enrollment = src / "mesh" / "enrollment.py"
    assert not enrollment.exists()
    result = inspect_openclaw(env={})
    assert result.outcome == "surface_absent"
    assert result.implement_fr4_2 is False
    assert result.unread_switch_removed is True
    assert not any("switch is still present" in reason for reason in result.reasons)


def test_c8_probe_parses_openclaw_help_without_treating_pairing_as_mesh() -> None:
    def which(_name: str) -> str | None:
        return "/tmp/openclaw"

    def run(argv, **_kwargs):  # noqa: ANN003
        text = "OpenClaw 2026.7.1-2\n" if "--version" in argv else _openclaw_help()
        return subprocess.CompletedProcess(argv, 0, stdout=text, stderr="")

    probe = probe_local_openclaw(env={}, which=which, run=run)
    assert probe.executable == "/tmp/openclaw"
    assert probe.version == "OpenClaw 2026.7.1-2"
    assert "pairing" in probe.commands
    assert "nodes" in probe.commands
    assert "gateway" in probe.commands
    assert "mesh-enrollment" not in probe.commands