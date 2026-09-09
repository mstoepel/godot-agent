"""Shared plumbing for the tool layer.

Every tool returns a dict rather than raising. A raised exception reaches the
model as an opaque traceback; a dict with ``ok: False`` and a sentence saying
what to do next is something it can act on. :func:`tool_errors` enforces that
uniformly so individual tools stay readable.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from godot_agent.engine.discovery import (
    GodotNotFoundError,
    ProjectNotFoundError,
    find_project_root,
)
from godot_agent.tscn.parser import TscnParseError

__all__ = ["ProjectArg", "ResPathArg", "failure", "resolve_project", "tool_errors"]

ProjectArg = Annotated[
    str | None,
    "Path to the Godot project directory. Omit to use the project in the working directory.",
]

ResPathArg = Annotated[str, "A res:// path inside the project, e.g. res://scenes/player.tscn"]

#: Exceptions a tool can hit that the model can actually do something about.
_EXPECTED = (
    GodotNotFoundError,
    ProjectNotFoundError,
    TscnParseError,
    FileNotFoundError,
    FileExistsError,
    ValueError,
    OSError,
)


def failure(message: str, **extra: Any) -> dict[str, Any]:
    """Build the standard failure payload."""
    return {"ok": False, "error": message, **extra}


def tool_errors[F: Callable[..., dict[str, Any]]](func: F) -> F:
    """Convert expected exceptions into ``{"ok": False, "error": ...}``.

    Unexpected exceptions are left to propagate: a bug in this package should
    surface as a real traceback rather than be laundered into advice for the
    model to work around.
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return func(*args, **kwargs)
        except _EXPECTED as error:
            return failure(f"{type(error).__name__}: {error}")

    return wrapper  # type: ignore[return-value]


def resolve_project(project: str | None) -> Path:
    """Resolve the project root, raising :class:`ProjectNotFoundError` if absent."""
    return find_project_root(project)


def res_to_path(root: Path, res_path: str) -> Path:
    """Map a ``res://`` path to a filesystem path inside ``root``.

    Raises:
        ValueError: if the path escapes the project, which is the one way a
            file tool can quietly write outside the workspace.
    """
    relative = res_path.removeprefix("res://").lstrip("/\\")
    resolved = (root / relative).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"{res_path!r} resolves outside the project root {root}")
    return resolved


def path_to_res(root: Path, path: Path) -> str:
    """Map a filesystem path back to its ``res://`` form."""
    relative = path.resolve().relative_to(root.resolve())
    return "res://" + relative.as_posix()
