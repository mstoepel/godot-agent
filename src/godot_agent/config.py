"""Process-wide settings for the Godot agent.

Everything is resolved from the environment with sane defaults so the library
works with zero configuration when a Godot binary is on ``PATH``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

#: Environment variable holding an explicit path to the Godot executable.
GODOT_BIN_ENV = "GODOT_BIN"

#: Environment variable holding the target Godot project directory.
GODOT_PROJECT_ENV = "GODOT_PROJECT"

#: Default ceiling on paid asset-generation spend for a single session, in USD.
DEFAULT_ASSET_BUDGET_USD = 5.0

#: Default wall-clock ceiling for any Godot subprocess, in seconds.
DEFAULT_TIMEOUT_S = 300.0

#: Loopback port the editor half of the bridge addon listens on.
DEFAULT_BRIDGE_PORT = 45719


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else None


@dataclass(slots=True)
class AssetProviders:
    """Which backend serves each asset-generation tool.

    Provider names are resolved lazily by :mod:`godot_agent.tools.assets`; an
    unset key simply means that tool reports itself unavailable rather than
    failing at import time.
    """

    image: str = field(
        default_factory=lambda: os.environ.get("GODOT_AGENT_IMAGE_PROVIDER", "openai")
    )
    pixel: str = field(
        default_factory=lambda: os.environ.get("GODOT_AGENT_PIXEL_PROVIDER", "retrodiffusion")
    )
    model3d: str = field(
        default_factory=lambda: os.environ.get("GODOT_AGENT_3D_PROVIDER", "meshy")
    )


@dataclass(slots=True)
class Settings:
    """Resolved configuration for one agent session."""

    godot_bin: Path | None = field(default_factory=lambda: _env_path(GODOT_BIN_ENV))
    project_path: Path | None = field(default_factory=lambda: _env_path(GODOT_PROJECT_ENV))
    timeout_s: float = field(
        default_factory=lambda: _env_float("GODOT_AGENT_TIMEOUT", DEFAULT_TIMEOUT_S)
    )
    asset_budget_usd: float = field(
        default_factory=lambda: _env_float("GODOT_AGENT_ASSET_BUDGET", DEFAULT_ASSET_BUDGET_USD)
    )
    bridge_port: int = field(
        default_factory=lambda: _env_int("GODOT_AGENT_BRIDGE_PORT", DEFAULT_BRIDGE_PORT)
    )
    providers: AssetProviders = field(default_factory=AssetProviders)

    def with_project(self, project_path: Path | str | None) -> Settings:
        """Return a copy pinned to ``project_path``."""
        if project_path is None:
            return self
        return Settings(
            godot_bin=self.godot_bin,
            project_path=Path(project_path).expanduser().resolve(),
            timeout_s=self.timeout_s,
            asset_budget_usd=self.asset_budget_usd,
            bridge_port=self.bridge_port,
            providers=self.providers,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the process-wide settings, building them on first use."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(settings: Settings) -> None:
    """Replace the process-wide settings. Intended for tests and CLI startup."""
    global _settings
    _settings = settings


def reset_settings() -> None:
    """Drop the cached settings so the next read re-reads the environment."""
    global _settings
    _settings = None
