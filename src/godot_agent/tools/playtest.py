"""Tools for driving a running game through the bridge addon.

Compiling a project proves almost nothing about a game. These tools let the
agent actually play it: press a button, step frames, read the score, capture
the screen. That is the difference between "the code runs" and "the feature
works".

All of them need the ``godot_agent_bridge`` addon installed and the game
launched with ``GODOT_AGENT_PROBE=1``; `godot_install_bridge` sets the first
part up.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Annotated, Any

from langchain_core.tools import tool

from godot_agent.bridge import BridgeClient, BridgeError
from godot_agent.tools._common import ProjectArg, failure, resolve_project, tool_errors

__all__ = [
    "godot_install_bridge",
    "godot_playtest_input",
    "godot_playtest_screenshot",
    "godot_playtest_state",
    "godot_playtest_status",
]

#: The addon source shipped with this package.
_ADDON_SOURCE = Path(__file__).resolve().parent.parent.parent.parent / "godot-addon"

ActionArg = Annotated[
    str,
    "An input action name from the project's input map. Unknown names are "
    "rejected, because Godot silently ignores them at runtime.",
]


def _runtime(project: str | None) -> BridgeClient:
    return BridgeClient.runtime(resolve_project(project))


@tool
@tool_errors
def godot_install_bridge(project: ProjectArg = None) -> dict[str, Any]:
    """Install the agent bridge addon into the project.

    Copies `addons/godot_agent_bridge/` in and tells you the two remaining
    steps, which have to happen in the editor. Needed before any playtest tool
    will work.
    """
    root = resolve_project(project)
    source = _ADDON_SOURCE / "addons" / "godot_agent_bridge"
    if not source.is_dir():
        return failure(f"the bridge addon source is missing from this install ({source})")

    destination = root / "addons" / "godot_agent_bridge"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)

    gitignore = root / ".gitignore"
    marker = ".godot-agent/"
    if gitignore.is_file():
        if marker not in gitignore.read_text(encoding="utf-8"):
            with gitignore.open("a", encoding="utf-8") as handle:
                handle.write(f"\n# godot-agent bridge token\n{marker}\n")
    else:
        gitignore.write_text(f"# godot-agent bridge token\n{marker}\n", encoding="utf-8")

    return {
        "ok": True,
        "installed": "res://addons/godot_agent_bridge",
        "next_steps": [
            "Enable 'Godot Agent Bridge' in Project Settings > Plugins.",
            "Add res://addons/godot_agent_bridge/agent_probe.gd as an autoload "
            "named AgentProbe, so the runtime probe runs inside the game.",
            "Launch the game with GODOT_AGENT_PROBE=1 to enable the probe.",
        ],
        "note": (
            "The bridge binds to 127.0.0.1 only and requires a token written to "
            ".godot-agent/bridge_token, which has been added to .gitignore."
        ),
    }


@tool
@tool_errors
def godot_playtest_status(project: ProjectArg = None) -> dict[str, Any]:
    """Check whether a running game is reachable for playtesting.

    Run this first. It reports whether the probe is up and, importantly,
    whether the game is running headless -- Godot does not deliver input events
    in headless mode, so input-driven tests are meaningless there.
    """
    root = resolve_project(project)
    runtime = BridgeClient.runtime(root)
    editor = BridgeClient.editor(root)

    try:
        info = runtime.ping()
    except BridgeError as error:
        return {
            "ok": False,
            "runtime_available": False,
            "editor_available": editor.is_available(),
            "error": str(error),
        }

    return {
        "ok": True,
        "runtime_available": True,
        "editor_available": editor.is_available(),
        "frame": info.get("frame"),
        "scene": info.get("scene"),
        "headless": info.get("headless"),
        **({"warning": info["warning"]} if "warning" in info else {}),
    }


@tool
@tool_errors
def godot_playtest_input(
    action: ActionArg,
    project: ProjectArg = None,
    mode: Annotated[
        str,
        "'tap' presses and releases over a few frames, 'press' holds, "
        "'release' lets go. Use 'tap' for one-shot actions like jump.",
    ] = "tap",
    frames: Annotated[int, "How many frames to hold for, when mode is 'tap'."] = 2,
) -> dict[str, Any]:
    """Send an input action to the running game.

    A press and release within the same frame is often missed entirely by
    `is_action_just_pressed`, so 'tap' holds for a couple of frames. This does
    nothing useful if the game is running headless -- check
    `godot_playtest_status` first.
    """
    if mode not in ("tap", "press", "release"):
        return failure(f"mode must be tap, press or release, not {mode!r}")
    return {"ok": True, **_runtime(project).call(mode, action=action, frames=frames)}


@tool
@tool_errors
def godot_playtest_state(
    project: ProjectArg = None,
    node: Annotated[
        str | None,
        "Node path to read one property from, e.g. 'HUD/ScoreLabel'. "
        "Omit to dump the scene tree instead.",
    ] = None,
    prop: Annotated[str | None, "Property name to read from `node`."] = None,
    depth: Annotated[int, "How deep to walk the tree when dumping."] = 3,
) -> dict[str, Any]:
    """Read live state out of the running game.

    With `node` and `prop`, reads one property -- the score, the player's
    health, whether it is game over. That is how a playtest asserts on
    behaviour; inferring it from a screenshot is guesswork.

    Without them, dumps the running scene tree.
    """
    client = _runtime(project)
    if node and prop:
        return {"ok": True, **client.call("get_property", node=node, property=prop)}
    if node or prop:
        return failure("pass both node and prop to read a property, or neither to dump the tree")
    return {"ok": True, **client.call("state", depth=depth)}


@tool
@tool_errors
def godot_playtest_screenshot(
    dest: Annotated[str, "Where to save the PNG, as a res:// or user:// path."] = (
        "user://agent_screenshot.png"
    ),
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Capture what the running game is currently showing.

    Reports whether the frame came back blank, which almost always means the
    scene never rendered -- a blank screenshot that reads as success is worse
    than an error.
    """
    result = _runtime(project).call("screenshot", path=dest)
    payload = {"ok": True, **result}
    if result.get("blank"):
        payload["warning"] = (
            "The captured frame is a single flat colour, which usually means the "
            "scene did not render. Check that the game is running with a display "
            "and that the camera is looking at something."
        )
    return payload
