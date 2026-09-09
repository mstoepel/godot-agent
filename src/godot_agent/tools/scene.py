"""Tools for reading and editing ``.tscn`` scene files.

These edit the text format directly rather than driving the editor, so they
work headlessly and produce reviewable diffs. Every write goes through a
re-parse before it lands: a scene the engine cannot load is far more expensive
to recover from than a rejected tool call.

Property values are passed as Godot literals in a string (``Vector2(32, 32)``,
``&"idle"``, ``true``). That keeps the model writing what it would write in the
file itself, and lets us reject a malformed value before it reaches disk.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool

from godot_agent.gdformat.values import GDParseError, parse_value
from godot_agent.tools._common import (
    ProjectArg,
    ResPathArg,
    failure,
    res_to_path,
    resolve_project,
    tool_errors,
)
from godot_agent.tscn.parser import Section, TscnFile, parse_tscn
from godot_agent.tscn.scene import ROOT_PATH, Scene, format_member

__all__ = [
    "godot_add_node",
    "godot_attach_script",
    "godot_connect_signal",
    "godot_create_scene",
    "godot_read_scene",
    "godot_remove_node",
    "godot_set_node_property",
]

NodePathArg = Annotated[
    str,
    'Node path relative to the scene root: "." for the root, "Sprite2D" for a '
    'child, "Body/Sprite2D" for a grandchild.',
]

PropertiesArg = Annotated[
    dict[str, str] | None,
    'Property name to Godot literal, e.g. {"position": "Vector2(0, 8)"}.',
]


def _load(project: str | None, scene: str) -> tuple[Scene, Any]:
    """Load a scene by ``res://`` path, returning it with its filesystem path."""
    root = resolve_project(project)
    path = res_to_path(root, scene)
    if not path.is_file():
        raise FileNotFoundError(f"{scene} does not exist (looked in {path})")
    return Scene.read(path), path


def _apply_properties(scene: Scene, node_path: str, properties: dict[str, str]) -> list[str]:
    """Validate and set raw literal properties, returning the keys set."""
    node = scene.find(node_path)
    if node is None:
        raise ValueError(f"node {node_path!r} does not exist in this scene")
    applied: list[str] = []
    for key, raw in properties.items():
        try:
            parse_value(raw)
        except GDParseError as error:
            raise ValueError(
                f"property {key!r} value {raw!r} is not a valid Godot literal: {error}"
            ) from error
        node.section.set_raw(key, raw)
        applied.append(key)
    return applied


def _save(scene: Scene, path: Any) -> None:
    """Write a scene, re-parsing first so a broken file never reaches disk."""
    text = scene.file.dumps()
    parse_tscn(text)  # raises TscnParseError if we produced something unloadable
    path.write_text(text, encoding="utf-8", newline="\n")


@tool
@tool_errors
def godot_read_scene(scene: ResPathArg, project: ProjectArg = None) -> dict[str, Any]:
    """Read a scene's node tree, external resources and signal connections.

    Do this before editing a scene. The node paths it returns are the exact
    strings the other scene tools and `get_node()` expect.
    """
    loaded, _ = _load(project, scene)
    return {
        "ok": True,
        "scene": scene,
        "tree": loaded.describe(),
        "node_paths": [node.path for node in loaded.nodes],
        "ext_resources": [
            {
                "id": section.attr_str("id"),
                "type": section.attr_str("type"),
                "path": section.attr_str("path"),
            }
            for section in loaded.file.ext_resources
        ],
        "sub_resource_count": len(loaded.file.sub_resources),
    }


@tool
@tool_errors
def godot_create_scene(
    scene: ResPathArg,
    root_name: Annotated[str, "Name of the scene's root node."],
    root_type: Annotated[str, "Godot class for the root, e.g. Node2D, CharacterBody2D."] = "Node2D",
    project: ProjectArg = None,
    overwrite: Annotated[bool, "Replace the file if it already exists."] = False,
) -> dict[str, Any]:
    """Create a new scene file containing a single root node.

    Add children with `godot_add_node` afterwards. Refuses to clobber an
    existing scene unless `overwrite` is set.
    """
    root = resolve_project(project)
    path = res_to_path(root, scene)
    if path.exists() and not overwrite:
        return failure(f"{scene} already exists; pass overwrite=true to replace it.")

    file = TscnFile()
    descriptor = Section(kind="gd_scene")
    descriptor.set_attr("format", 3)
    file.sections.append(descriptor)

    node = Section(kind="node")
    node.set_attr("name", root_name)
    node.set_attr("type", root_type)
    file.sections.append(node)

    path.parent.mkdir(parents=True, exist_ok=True)
    _save(Scene(file, path), path)
    return {"ok": True, "scene": scene, "root": root_name, "root_type": root_type}


@tool
@tool_errors
def godot_add_node(
    scene: ResPathArg,
    name: Annotated[str, "Node name. Must be unique among its siblings."],
    node_type: Annotated[str, "Godot class, e.g. Sprite2D, CollisionShape2D, Timer."],
    parent: NodePathArg = ROOT_PATH,
    properties: PropertiesArg = None,
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Add a node to a scene, under `parent`.

    Rejects a name already used by a sibling: Godot would silently rename the
    duplicate, breaking every node path written against it.
    """
    loaded, path = _load(project, scene)
    node = loaded.add_node(name, node_type, parent=parent)
    if properties:
        _apply_properties(loaded, node.path, properties)
    _save(loaded, path)
    return {"ok": True, "scene": scene, "node_path": node.path, "tree": loaded.describe()}


@tool
@tool_errors
def godot_set_node_property(
    scene: ResPathArg,
    node: NodePathArg,
    properties: Annotated[
        dict[str, str],
        'Property name to Godot literal, e.g. {"position": "Vector2(0, 8)"}.',
    ],
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Set one or more properties on a node in a scene.

    Values are Godot literals written exactly as they would appear in the file.
    A malformed value is rejected rather than written.
    """
    loaded, path = _load(project, scene)
    applied = _apply_properties(loaded, node, properties)
    _save(loaded, path)
    return {"ok": True, "scene": scene, "node": node, "set": applied}


@tool
@tool_errors
def godot_remove_node(
    scene: ResPathArg,
    node: NodePathArg,
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Remove a node and all of its descendants from a scene."""
    loaded, path = _load(project, scene)
    loaded.remove_node(node)
    _save(loaded, path)
    return {"ok": True, "scene": scene, "removed": node, "tree": loaded.describe()}


@tool
@tool_errors
def godot_attach_script(
    scene: ResPathArg,
    node: NodePathArg,
    script: Annotated[str, "res:// path of the .gd script to attach."],
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Attach a GDScript file to a node in a scene.

    Registers the script as an external resource and sets the node's `script`
    property. The script file must already exist.
    """
    root = resolve_project(project)
    script_path = res_to_path(root, script)
    if not script_path.is_file():
        return failure(f"script {script} does not exist (looked in {script_path})")

    loaded, path = _load(project, scene)
    if loaded.find(node) is None:
        return failure(f"node {node!r} does not exist in {scene}")

    resource_id = loaded.add_ext_resource("Script", script)
    loaded.set_property(node, "script", parse_value(f'ExtResource("{resource_id}")'))
    _save(loaded, path)
    return {"ok": True, "scene": scene, "node": node, "script": script}


@tool
@tool_errors
def godot_connect_signal(
    scene: ResPathArg,
    signal: Annotated[str, "Signal name, e.g. body_entered, timeout, pressed."],
    from_node: NodePathArg,
    to_node: NodePathArg,
    method: Annotated[str, "Handler method name on the receiving node's script."],
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Connect a signal between two nodes in a scene.

    The handler method must exist on the receiving node's script, or the
    connection errors at load time. Repeating an existing connection is a no-op.
    """
    loaded, path = _load(project, scene)
    for node_path in (from_node, to_node):
        if loaded.find(node_path) is None:
            return failure(f"node {node_path!r} does not exist in {scene}")
    loaded.add_connection(signal, from_node, to_node, method)
    _save(loaded, path)
    return {
        "ok": True,
        "scene": scene,
        "connection": (
            f"{format_member(from_node, signal)} -> {format_member(to_node, method)}"
        ),
    }
