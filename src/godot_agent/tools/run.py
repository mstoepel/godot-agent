"""Tools for running a project and validating GDScript.

``godot_check_script`` is the fast feedback loop: it parses a script without
running the game, so a syntax or type error is caught in about a second instead
of surfacing as a cascade of scene-load failures later.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool

from godot_agent.engine.cli import DEFAULT_QUIT_AFTER, run_godot, run_project
from godot_agent.tools._common import (
    ProjectArg,
    failure,
    res_to_path,
    resolve_project,
    tool_errors,
)

__all__ = ["godot_check_script", "godot_run", "godot_run_script"]


@tool
@tool_errors
def godot_run(
    scene: Annotated[
        str | None,
        "res:// scene to run. Omit to run the project's main scene.",
    ] = None,
    project: ProjectArg = None,
    frames: Annotated[
        int,
        "Frame budget before Godot quits. About 60 frames per second.",
    ] = DEFAULT_QUIT_AFTER,
    headless: Annotated[
        bool,
        "Run without a window. Set false only when something must render to a screen.",
    ] = True,
    timeout: Annotated[float | None, "Seconds before the process is killed."] = None,
) -> dict[str, Any]:
    """Run the game for a bounded number of frames and report what went wrong.

    This is the check that a change actually works: it loads every scene and
    script on the startup path and surfaces runtime errors with their
    `res://file:line`. Always bounded, so it cannot hang the session.
    """
    result = run_project(
        project,
        scene=scene,
        quit_after=frames,
        headless=headless,
        timeout=timeout,
    )
    payload = result.to_model()
    payload["scene"] = scene or "(main scene)"
    payload["frames"] = frames
    return payload


@tool
@tool_errors
def godot_check_script(
    script: Annotated[str, "res:// path of the .gd file to check."],
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Parse-check one GDScript file without running the game.

    The fastest way to validate a script you just wrote. Catches syntax errors,
    unknown identifiers and type errors in about a second. Run it after every
    non-trivial script edit, before running the game.
    """
    root = resolve_project(project)
    path = res_to_path(root, script)
    if not path.is_file():
        return failure(f"{script} does not exist (looked in {path})")

    result = run_godot(
        ["--headless", "--check-only", "--script", script],
        project=root,
    )
    payload = result.to_model()
    payload["script"] = script
    if payload["ok"]:
        payload["summary"] = f"{script} parses cleanly."
    return payload


@tool
@tool_errors
def godot_run_script(
    script: Annotated[
        str,
        "res:// path of a GDScript extending SceneTree or MainLoop.",
    ],
    project: ProjectArg = None,
    args: Annotated[list[str] | None, "Arguments passed through to the script."] = None,
    timeout: Annotated[float | None, "Seconds before the process is killed."] = None,
) -> dict[str, Any]:
    """Run a GDScript tool script inside the project and return its output.

    Use this when something needs real engine APIs that text-file editing cannot
    reach: inspecting a resource, instantiating a scene to check it loads, or
    querying import settings. The script must extend `SceneTree` or `MainLoop`
    and quit when finished.
    """
    root = resolve_project(project)
    path = res_to_path(root, script)
    if not path.is_file():
        return failure(f"{script} does not exist (looked in {path})")

    command = ["--headless", "--script", script]
    if args:
        command += ["--", *args]

    result = run_godot(command, project=root, timeout=timeout)
    payload = result.to_model(tail_lines=120)
    payload["script"] = script
    payload["stdout"] = result.stdout
    return payload
