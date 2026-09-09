"""Validate Godot files that were just written.

Shared by two callers that reach the agent through different paths: the
in-process :mod:`godot_agent.middleware.validation` middleware, and the dcode
``PostToolUse`` hook in :mod:`godot_agent.hooks`. Both need the same answer, so
the check lives here rather than in either of them.

The check is deliberately cheap and degrades rather than failing: scenes are
re-parsed in process, and a script is only compiled when a Godot binary is
actually available.
"""

from __future__ import annotations

from pathlib import Path

from godot_agent.engine.cli import run_godot
from godot_agent.engine.discovery import (
    GodotNotFoundError,
    ProjectNotFoundError,
    find_godot,
    find_project_root,
)
from godot_agent.engine.errors import summarize
from godot_agent.tscn.parser import TscnParseError, parse_tscn

__all__ = ["SCENE_SUFFIXES", "validate_file"]

#: File types this module knows how to check.
SCENE_SUFFIXES = frozenset({".tscn", ".tres"})

#: Seconds to allow for a single script parse check.
_SCRIPT_CHECK_TIMEOUT = 60.0


def _validate_scene(path: Path) -> str | None:
    try:
        parse_tscn(path.read_text(encoding="utf-8"))
    except (TscnParseError, OSError, UnicodeDecodeError) as error:
        return (
            f"{path.name} is not a loadable Godot scene file: {error}\n"
            "Godot will refuse to open it. Fix the file before continuing."
        )
    return None


def _validate_script(path: Path, project_path: Path | str | None) -> str | None:
    try:
        root = find_project_root(project_path or path)
        find_godot()
    except (ProjectNotFoundError, GodotNotFoundError):
        # No engine or no project: nothing to compile against. Scene checks
        # still work, so this degrades rather than erroring.
        return None

    try:
        res_path = "res://" + path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None  # written outside the project; not ours to check

    result = run_godot(
        ["--headless", "--check-only", "--script", res_path],
        project=root,
        timeout=_SCRIPT_CHECK_TIMEOUT,
    )
    if result.ok:
        return None
    return f"{res_path} does not compile:\n{summarize(result.diagnostics, limit=8)}"


def validate_file(
    path: Path,
    *,
    project_path: Path | str | None = None,
    check_scripts: bool = True,
) -> str | None:
    """Check one written file.

    Args:
        path: The file that was just written.
        project_path: Project root, if already known.
        check_scripts: Run the ``--check-only`` pass on ``.gd`` files. Turning
            this off keeps validation to in-process scene parsing.

    Returns:
        A description of the problem, or ``None`` when the file is fine or is
        not a file type this module checks.
    """
    if not path.is_file():
        return None
    if path.suffix in SCENE_SUFFIXES:
        return _validate_scene(path)
    if path.suffix == ".gd" and check_scripts:
        return _validate_script(path, project_path)
    return None
