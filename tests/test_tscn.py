"""Tests for the scene/resource parser and the node-tree view.

The fixtures under ``tests/fixtures/scenes`` are unmodified files from the
official ``godotengine/godot-demo-projects`` repository, so round-trip
assertions are made against text the engine itself wrote.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from godot_agent.gdformat.values import GDCall, GDStringName
from godot_agent.tscn import Scene, TscnParseError, parse_tscn, read_tscn

FIXTURES = Path(__file__).parent / "fixtures" / "scenes"
SCENE_FILES = sorted(FIXTURES.glob("*.tscn"))


def test_fixtures_are_present() -> None:
    """Guard against a silently empty fixture directory making the suite vacuous."""
    assert len(SCENE_FILES) >= 4


@pytest.mark.parametrize("path", SCENE_FILES, ids=lambda p: p.name)
def test_round_trips_byte_for_byte(path: Path) -> None:
    """An untouched engine-written scene must reproduce exactly."""
    original = path.read_text(encoding="utf-8")
    assert parse_tscn(original).dumps() == original


def test_parses_sections_of_each_kind() -> None:
    scene = read_tscn(FIXTURES / "player.tscn")
    assert scene.descriptor is not None
    assert scene.descriptor.kind == "gd_scene"
    assert scene.descriptor.attr_str("uid") == "uid://bwhlkliwp13p4"
    assert len(scene.ext_resources) == 5
    assert len(scene.sub_resources) == 7
    assert len(scene.nodes) == 4
    assert len(scene.connections) == 1


def test_parses_header_attributes_of_mixed_types() -> None:
    """Godot 4.7 writes ``unique_id`` as a bare int next to quoted strings."""
    scene = read_tscn(FIXTURES / "player.tscn")
    root = scene.nodes[0]
    assert root.attr_str("name") == "Player"
    assert root.attr_str("type") == "Area2D"
    assert root.attr("unique_id") == 2141725708


def test_parses_multiline_property_value() -> None:
    """``SpriteFrames.animations`` spans 20+ lines; it must survive as one value."""
    scene = read_tscn(FIXTURES / "player.tscn")
    sprite_frames = scene.sub_resources[0]
    raw = sprite_frames.properties["animations"]
    assert "\n" in raw
    animations = sprite_frames.get("animations")
    assert isinstance(animations, list)
    assert animations[0]["name"] == GDStringName("right")
    assert animations[0]["speed"] == 5.0


def test_parses_stringname_and_subresource_references() -> None:
    scene = read_tscn(FIXTURES / "player.tscn")
    sprite = scene.nodes[1]
    assert sprite.get("animation") == GDStringName("right")
    assert sprite.get("sprite_frames") == GDCall("SubResource", ("1",))


def test_connection_attributes() -> None:
    scene = read_tscn(FIXTURES / "player.tscn")
    connection = scene.connections[0]
    assert connection.attr_str("signal") == "body_entered"
    assert connection.attr_str("method") == "_on_body_entered"


@pytest.mark.parametrize(
    "text",
    [
        "size = Vector2(1, 1)",  # property before any section
        "[node name=]",  # malformed attribute value
        "[unterminated",
    ],
)
def test_rejects_malformed_scenes(text: str) -> None:
    with pytest.raises(TscnParseError):
        parse_tscn(text)


# -- node tree ------------------------------------------------------------


def test_resolves_node_paths() -> None:
    scene = Scene.read(FIXTURES / "main.tscn")
    paths = [node.path for node in scene.nodes]
    assert paths[0] == "."
    assert all(path != "" for path in paths)
    root = scene.root
    assert root is not None and root.is_root


def test_find_and_children() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    assert scene.find(".") is not None
    sprite = scene.find("AnimatedSprite2D")
    assert sprite is not None
    assert sprite.type == "AnimatedSprite2D"
    assert scene.find("NoSuchNode") is None
    assert {child.name for child in scene.children_of(".")} == {
        "AnimatedSprite2D",
        "CollisionShape2D",
        "Trail",
    }


def test_describe_renders_a_tree(tmp_path: Path) -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    described = scene.describe()
    assert "Player [Area2D]" in described
    assert "  AnimatedSprite2D [AnimatedSprite2D]" in described
    assert "signal body_entered" in described


def test_add_node_writes_a_loadable_section(tmp_path: Path) -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    scene.add_node("Hitbox", "CollisionShape2D", parent=".", properties={"disabled": True})
    added = scene.find("Hitbox")
    assert added is not None
    assert added.type == "CollisionShape2D"
    assert added.section.properties["disabled"] == "true"

    # The result must survive a re-parse, which is the cheapest proxy we have
    # for "the engine can still load this".
    out = tmp_path / "player.tscn"
    scene.write(out)
    assert Scene.read(out).find("Hitbox") is not None


def test_add_node_places_children_after_their_parent() -> None:
    """Godot requires a parent to appear earlier in the file than its children."""
    scene = Scene.read(FIXTURES / "player.tscn")
    scene.add_node("Muzzle", "Marker2D", parent="AnimatedSprite2D")
    order = [node.path for node in scene.nodes]
    assert order.index("AnimatedSprite2D") < order.index("AnimatedSprite2D/Muzzle")


def test_add_node_rejects_duplicate_sibling_names() -> None:
    """Godot silently renames duplicates, which would break NodePaths."""
    scene = Scene.read(FIXTURES / "player.tscn")
    with pytest.raises(ValueError, match="already exists"):
        scene.add_node("CollisionShape2D", "Node2D", parent=".")


def test_add_node_rejects_missing_parent() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    with pytest.raises(ValueError, match="does not exist"):
        scene.add_node("Orphan", "Node2D", parent="Nowhere")


def test_remove_node_takes_descendants_with_it() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    scene.add_node("Muzzle", "Marker2D", parent="AnimatedSprite2D")
    scene.remove_node("AnimatedSprite2D")
    assert scene.find("AnimatedSprite2D") is None
    assert scene.find("AnimatedSprite2D/Muzzle") is None


def test_remove_node_refuses_the_root() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    with pytest.raises(ValueError, match="root"):
        scene.remove_node(".")


def test_set_property() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    scene.set_property("CollisionShape2D", "disabled", True)
    node = scene.find("CollisionShape2D")
    assert node is not None
    assert node.section.properties["disabled"] == "true"


def test_add_ext_resource_is_idempotent() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    before = len(scene.file.ext_resources)
    first = scene.add_ext_resource("Texture2D", "res://art/new.png")
    second = scene.add_ext_resource("Texture2D", "res://art/new.png")
    assert first == second
    assert len(scene.file.ext_resources) == before + 1


def test_add_ext_resource_reuses_existing_path() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    existing = scene.add_ext_resource("Script", "res://player.gd")
    assert existing == "1"


def test_add_connection_is_idempotent() -> None:
    scene = Scene.read(FIXTURES / "player.tscn")
    before = len(scene.file.connections)
    scene.add_connection("body_entered", ".", ".", "_on_body_entered")
    assert len(scene.file.connections) == before
    scene.add_connection("area_entered", ".", ".", "_on_area_entered")
    assert len(scene.file.connections) == before + 1


def test_edit_only_changes_the_touched_value() -> None:
    """A one-property edit must not reformat the rest of the file."""
    path = FIXTURES / "player.tscn"
    original = path.read_text(encoding="utf-8").splitlines()
    scene = Scene.read(path)
    scene.set_property(".", "z_index", 12)
    changed = scene.file.dumps().splitlines()
    differing = [
        (before, after)
        for before, after in zip(original, changed, strict=True)
        if before != after
    ]
    assert differing == [("z_index = 10", "z_index = 12")]


def test_format_member_does_not_double_the_root_dot() -> None:
    """The root's path is literally "."; naive joining reads as a broken path."""
    from godot_agent.tscn.scene import format_member

    assert format_member(".", "_on_body_entered") == "<root>._on_body_entered"
    assert format_member("Hitbox", "body_entered") == "Hitbox.body_entered"
    assert format_member(None, "x") == "<root>.x"
