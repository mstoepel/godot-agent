"""Locate the Godot executable and read a project's configuration.

The agent needs a reliable answer to two questions before it can do anything
useful: *which Godot am I driving* and *what is in this project*. Both are
cached per path, because they are asked on nearly every tool call and neither
changes within a session unless the file does.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from godot_agent.config import get_settings
from godot_agent.gdformat.config import GodotConfig, read_config
from godot_agent.gdformat.values import GDCall

__all__ = [
    "GodotNotFoundError",
    "GodotVersion",
    "ProjectInfo",
    "ProjectNotFoundError",
    "find_godot",
    "find_project_root",
    "godot_version",
    "read_project_info",
]

#: Executable names Godot ships under, newest naming first.
_BINARY_NAMES = ("godot", "godot4", "Godot", "Godot_v4")

#: Directories Godot is commonly installed into, by platform.
_SEARCH_DIRS: dict[str, tuple[str, ...]] = {
    "win32": (
        r"%LOCALAPPDATA%\Programs\Godot",
        r"%PROGRAMFILES%\Godot",
        r"%PROGRAMFILES(X86)%\Steam\steamapps\common\Godot Engine",
        r"%USERPROFILE%\scoop\apps\godot\current",
        r"%USERPROFILE%\AppData\Local\Microsoft\WinGet\Links",
    ),
    "darwin": (
        "/Applications/Godot.app/Contents/MacOS",
        "~/Applications/Godot.app/Contents/MacOS",
        "/opt/homebrew/bin",
    ),
    "linux": (
        "/usr/bin",
        "/usr/local/bin",
        "~/.local/bin",
        "/var/lib/flatpak/exports/bin",
    ),
}

_VERSION_RE = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?\.(?P<status>\w+)")


class GodotNotFoundError(RuntimeError):
    """Raised when no Godot executable could be located."""


class ProjectNotFoundError(RuntimeError):
    """Raised when a directory is not inside a Godot project."""


@dataclass(frozen=True, slots=True)
class GodotVersion:
    """A parsed ``godot --version`` string."""

    raw: str
    major: int
    minor: int
    patch: int
    status: str

    def __str__(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}.{self.status}"

    @property
    def supports_gdunit4(self) -> bool:
        """gdUnit4 supports Godot 4.5 and newer."""
        return (self.major, self.minor) >= (4, 5)


def _candidate_paths() -> list[Path]:
    """Every plausible Godot path for this platform, in priority order."""
    candidates: list[Path] = []

    for name in _BINARY_NAMES:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    platform = "win32" if sys.platform.startswith("win") else sys.platform
    for raw_dir in _SEARCH_DIRS.get(platform, ()):
        directory = Path(os.path.expandvars(raw_dir)).expanduser()
        if "%" in str(directory) or not directory.is_dir():
            continue
        for entry in sorted(directory.iterdir(), reverse=True):
            if not entry.is_file():
                continue
            stem = entry.stem.lower()
            if stem.startswith(("godot", "godot_v")) and "console" not in stem:
                candidates.append(entry)

    return candidates


@lru_cache(maxsize=8)
def _probe_version(binary: str) -> GodotVersion | None:
    """Run ``--version`` on a candidate, returning None if it is not Godot."""
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (result.stdout or result.stderr).strip().splitlines()
    if not raw:
        return None
    match = _VERSION_RE.match(raw[-1].strip())
    if not match:
        return None
    return GodotVersion(
        raw=raw[-1].strip(),
        major=int(match["major"]),
        minor=int(match["minor"]),
        patch=int(match["patch"] or 0),
        status=match["status"],
    )


def find_godot(explicit: Path | str | None = None) -> Path:
    """Return the Godot executable to drive.

    Resolution order: the ``explicit`` argument, then ``GODOT_BIN``, then
    ``PATH``, then the platform's usual install directories. Every candidate is
    verified by actually running ``--version``, because a stale ``PATH`` entry
    or a same-named unrelated binary is a confusing way to fail later.

    Raises:
        GodotNotFoundError: if nothing usable was found.
    """
    explicit = explicit or get_settings().godot_bin
    if explicit:
        path = Path(explicit).expanduser()
        if _probe_version(str(path)) is None:
            raise GodotNotFoundError(
                f"{path} is not a working Godot executable "
                "(set GODOT_BIN to the right path, or unset it to search)"
            )
        return path

    for candidate in _candidate_paths():
        if _probe_version(str(candidate)) is not None:
            return candidate

    raise GodotNotFoundError(
        "No Godot executable found. Install Godot 4.5+ and put it on PATH, "
        "or set GODOT_BIN to its full path. Downloads: https://godotengine.org/download"
    )


def godot_version(binary: Path | str | None = None) -> GodotVersion:
    """Return the version of ``binary``, or of the resolved default Godot."""
    path = Path(binary) if binary else find_godot()
    version = _probe_version(str(path))
    if version is None:
        raise GodotNotFoundError(f"{path} did not report a Godot version")
    return version


def find_project_root(start: Path | str | None = None) -> Path:
    """Walk up from ``start`` to the directory holding ``project.godot``.

    Raises:
        ProjectNotFoundError: if no project is found at or above ``start``.
    """
    settings = get_settings()
    origin = Path(start or settings.project_path or Path.cwd()).expanduser().resolve()
    if origin.is_file():
        origin = origin.parent
    for directory in (origin, *origin.parents):
        if (directory / "project.godot").is_file():
            return directory
    raise ProjectNotFoundError(
        f"No project.godot found at or above {origin}. "
        "Pass an explicit project path or set GODOT_PROJECT."
    )


@dataclass(slots=True)
class ProjectInfo:
    """A summary of a Godot project, cheap enough to inject into a prompt."""

    root: Path
    name: str
    main_scene: str | None
    renderer: str | None
    features: list[str] = field(default_factory=list)
    autoloads: dict[str, str] = field(default_factory=dict)
    input_actions: list[str] = field(default_factory=list)
    addons: list[str] = field(default_factory=list)
    config: GodotConfig | None = None

    def summary(self) -> str:
        """Render as a compact fact sheet for the model."""
        lines = [
            f"Project: {self.name}",
            f"Root: {self.root}",
            f"Main scene: {self.main_scene or '(none set)'}",
            f"Renderer: {self.renderer or '(default)'}",
        ]
        if self.features:
            lines.append(f"Features: {', '.join(self.features)}")
        if self.autoloads:
            entries = ", ".join(f"{key}={value}" for key, value in self.autoloads.items())
            lines.append(f"Autoloads: {entries}")
        if self.input_actions:
            lines.append(f"Input actions: {', '.join(sorted(self.input_actions))}")
        lines.append(f"Addons: {', '.join(self.addons) if self.addons else '(none)'}")
        return "\n".join(lines)


def _as_text(value: object) -> str | None:
    """Coerce a parsed config value to display text."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, GDCall):
        return value.name
    return str(value)


def read_project_info(project_root: Path | str | None = None) -> ProjectInfo:
    """Read and summarize ``project.godot``.

    Never raises on a malformed key: the summary is advisory context, and a
    project with one exotic setting should still be workable.
    """
    root = find_project_root(project_root)
    config = read_config(root / "project.godot")

    features = config.get_value("application", "config/features", [])
    if not isinstance(features, list):
        features = []

    autoloads = {
        key: _as_text(config.get_value("autoload", key)) or ""
        for key, _ in config.items("autoload")
    }

    input_actions = [key for key, _ in config.items("input")]

    addons_dir = root / "addons"
    addons = (
        sorted(entry.name for entry in addons_dir.iterdir() if entry.is_dir())
        if addons_dir.is_dir()
        else []
    )

    return ProjectInfo(
        root=root,
        name=_as_text(config.get_value("application", "config/name")) or root.name,
        main_scene=_as_text(config.get_value("application", "run/main_scene")),
        renderer=_as_text(config.get_value("rendering", "renderer/rendering_method")),
        features=[str(feature) for feature in features],
        autoloads=autoloads,
        input_actions=input_actions,
        addons=addons,
        config=config,
    )
