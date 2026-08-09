"""Skill Foundry configuration models.

The ``skill_foundry`` section governs the active-package installation,
versioning, and live execution of registry-installed skills on top of the
existing skill-discovery flow.  All settings are additive — omitting the
section leaves the default skill roots and worker mounts unchanged.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SkillRegistryConfig(BaseModel):
    """Remote registry source used to discover published skills."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(
        default="https://skills.openclaw.ai",
        description="Base URL of the registry index (index.json is appended).",
    )
    git: str | None = Field(
        default=None,
        description="Optional git repository URL for a git-backed registry.",
    )
    branch: str = Field(default="main", description="Git branch to track for git-backed registries.")

    @field_validator("url")
    @classmethod
    def _validate_url(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped.startswith(("http://", "https://")):
            msg = "skill_foundry.registry.url must be an http(s) URL"
            raise ValueError(msg)
        return stripped.rstrip("/")


class SkillFoundryConfig(BaseModel):
    """Configuration for active-package skill installation and execution."""

    model_config = ConfigDict(extra="forbid")

    registry: SkillRegistryConfig = Field(
        default_factory=SkillRegistryConfig,
        description="Remote registry source for discovering skills.",
    )
    auto_update: bool = Field(
        default=True,
        description="Whether to refresh the registry index and installed skills on startup.",
    )
    update_interval_minutes: int = Field(
        default=60,
        ge=1,
        description="How often to poll for registry updates when auto-update is enabled.",
    )
    cache_root: Path | None = Field(
        default=None,
        description="Directory where registry-installed skills are cached. "
        "Defaults to ~/.mindroom/skills-cache. Becomes the lowest-priority skill root.",
    )
    enabled: bool = Field(
        default=True,
        description="Master switch for the Skill Foundry integration layer.",
    )

    @field_validator("cache_root")
    @classmethod
    def _validate_cache_root(cls, value: Path | None) -> Path | None:
        if value is None:
            return None
        resolved = value.expanduser()
        if not resolved.is_absolute() or resolved.is_symlink():
            msg = "skill_foundry.cache_root must be an absolute, non-symlink directory path"
            raise ValueError(msg)
        return resolved