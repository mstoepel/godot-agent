"""Tests for the tool layer.

Tools are invoked the way the agent invokes them -- through ``.invoke({...})``
-- so the pydantic argument schemas are exercised too, not just the Python
functions behind them.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from godot_agent.config import Settings, reset_settings, set_settings
from godot_agent.tools import all_tools, tool_names, tools_for
from godot_agent.tools.project import godot_project_info, godot_scaffold_project
from godot_agent.tools.scene import (
    godot_add_node,
    godot_attach_script,
    godot_connect_signal,
    godot_create_scene,
    godot_read_scene,
    godot_remove_node,
    godot_set_node_property,
)
from godot_agent.tools.testing import godot_run_tests, godot_scaffold_test
from godot_agent.tscn import Scene

FIXTURES = Path(__file__).parent / "fixtures" / "scenes"


@pytest.fixture(autouse=True)
def _isolated_settings():
    set_settings(Settings(godot_bin=None, project_path=None))
    yield
    reset_settings()


@pytest.fixture
def project(tmp_path: Path) -> str:
    """A real project on disk; returns its path as the tools take it."""
    root = tmp_path / "game"
    root.mkdir()
    for name in ("project.godot", "player.tscn", "main.tscn"):
        shutil.copy(FIXTURES / name, root / name)
    (root / "scripts").mkdir()
    (root / "scripts" / "player.gd").write_text(
        "extends Area2D\n\nfunc _on_body_entered(_body: Node) -> void:\n\tpass\n",
        encoding="utf-8",
    )
    return str(root)


# -- registry -------------------------------------------------------------


def test_registry_exposes_uniquely_named_tools() -> None:
    names = tool_names()
    assert len(names) == len(set(names))
    assert all(name.startswith("godot_") for name in names)


def test_every_tool_documents_itself() -> None:
    """Descriptions are prompt real estate; an undocumented tool is unusable."""
    for item in all_tools():
        assert item.description and len(item.description) > 40, item.name


def test_tool_groups_are_selectable() -> None:
    assert {item.name for item in tools_for("scene")} == {
        "godot_read_scene",
        "godot_create_scene",
        "godot_add_node",
        "godot_set_node_property",
        "godot_remove_node",
        "godot_attach_script",
        "godot_connect_signal",
    }


def test_unknown_group_fails_loudly() -> None:
    with pytest.raises(KeyError):
        tools_for("no_such_group")


# -- project tools --------------------------------------------------------


def test_project_info_reports_facts(project: str) -> None:
    result = godot_project_info.invoke({"project": project})
    assert result["ok"]
    assert result["name"] == "Dodge the Creeps"
    assert "move_right" in result["input_actions"]


def test_project_info_explains_a_missing_project(tmp_path: Path) -> None:
    result = godot_project_info.invoke({"project": str(tmp_path)})
    assert not result["ok"]
    assert "project.godot" in result["error"]


def test_scaffold_creates_a_runnable_project(tmp_path: Path) -> None:
    target = tmp_path / "new-game"
    result = godot_scaffold_project.invoke({"path": str(target), "name": "Test Game"})
    assert result["ok"]
    assert (target / "project.godot").is_file()
    assert (target / "main.tscn").is_file()

    info = godot_project_info.invoke({"project": str(target)})
    assert info["ok"]
    assert info["name"] == "Test Game"
    # Pixel-art defaults: nearest filtering and the compatibility renderer.
    assert info["renderer"] == "gl_compatibility"


def test_scaffold_refuses_to_clobber(project: str) -> None:
    result = godot_scaffold_project.invoke({"path": project})
    assert not result["ok"]
    assert "already contains" in result["error"]


# -- scene tools ----------------------------------------------------------


def test_read_scene_returns_paths_the_other_tools_accept(project: str) -> None:
    result = godot_read_scene.invoke({"scene": "res://player.tscn", "project": project})
    assert result["ok"]
    assert "." in result["node_paths"]
    assert "AnimatedSprite2D" in result["node_paths"]
    assert len(result["ext_resources"]) == 5


def test_read_missing_scene_is_reported(project: str) -> None:
    result = godot_read_scene.invoke({"scene": "res://nope.tscn", "project": project})
    assert not result["ok"]
    assert "does not exist" in result["error"]


def test_create_add_and_read_round_trip(project: str) -> None:
    created = godot_create_scene.invoke(
        {
            "scene": "res://scenes/enemy.tscn",
            "root_name": "Enemy",
            "root_type": "CharacterBody2D",
            "project": project,
        }
    )
    assert created["ok"]

    added = godot_add_node.invoke(
        {
            "scene": "res://scenes/enemy.tscn",
            "name": "Sprite2D",
            "node_type": "Sprite2D",
            "properties": {"position": "Vector2(0, -8)"},
            "project": project,
        }
    )
    assert added["ok"]
    assert added["node_path"] == "Sprite2D"

    read = godot_read_scene.invoke({"scene": "res://scenes/enemy.tscn", "project": project})
    assert read["node_paths"] == [".", "Sprite2D"]


def test_create_scene_refuses_to_clobber(project: str) -> None:
    result = godot_create_scene.invoke(
        {"scene": "res://player.tscn", "root_name": "X", "project": project}
    )
    assert not result["ok"]
    assert "already exists" in result["error"]


def test_set_property_rejects_a_malformed_literal(project: str) -> None:
    """A bad value must be refused, not written into a file the engine loads."""
    before = (Path(project) / "player.tscn").read_text(encoding="utf-8")
    result = godot_set_node_property.invoke(
        {
            "scene": "res://player.tscn",
            "node": ".",
            "properties": {"z_index": "Vector2(((("},
            "project": project,
        }
    )
    assert not result["ok"]
    assert "not a valid Godot literal" in result["error"]
    assert (Path(project) / "player.tscn").read_text(encoding="utf-8") == before


def test_set_property_writes_a_valid_literal(project: str) -> None:
    result = godot_set_node_property.invoke(
        {
            "scene": "res://player.tscn",
            "node": ".",
            "properties": {"z_index": "42"},
            "project": project,
        }
    )
    assert result["ok"]
    scene = Scene.read(Path(project) / "player.tscn")
    assert scene.find(".").section.get("z_index") == 42


def test_add_node_rejects_a_duplicate_name(project: str) -> None:
    result = godot_add_node.invoke(
        {
            "scene": "res://player.tscn",
            "name": "CollisionShape2D",
            "node_type": "Node2D",
            "project": project,
        }
    )
    assert not result["ok"]
    assert "already exists" in result["error"]


def test_remove_node(project: str) -> None:
    result = godot_remove_node.invoke(
        {"scene": "res://player.tscn", "node": "Trail", "project": project}
    )
    assert result["ok"]
    assert Scene.read(Path(project) / "player.tscn").find("Trail") is None


def test_attach_script_registers_the_resource(project: str) -> None:
    godot_create_scene.invoke(
        {"scene": "res://scenes/thing.tscn", "root_name": "Thing", "project": project}
    )
    result = godot_attach_script.invoke(
        {
            "scene": "res://scenes/thing.tscn",
            "node": ".",
            "script": "res://scripts/player.gd",
            "project": project,
        }
    )
    assert result["ok"]
    scene = Scene.read(Path(project) / "scenes" / "thing.tscn")
    assert len(scene.file.ext_resources) == 1
    assert scene.find(".").section.properties["script"].startswith("ExtResource(")


def test_attach_missing_script_is_reported(project: str) -> None:
    result = godot_attach_script.invoke(
        {
            "scene": "res://player.tscn",
            "node": ".",
            "script": "res://scripts/ghost.gd",
            "project": project,
        }
    )
    assert not result["ok"]
    assert "does not exist" in result["error"]


def test_connect_signal(project: str) -> None:
    result = godot_connect_signal.invoke(
        {
            "scene": "res://player.tscn",
            "signal": "area_entered",
            "from_node": ".",
            "to_node": ".",
            "method": "_on_area_entered",
            "project": project,
        }
    )
    assert result["ok"]
    scene = Scene.read(Path(project) / "player.tscn")
    assert len(scene.file.connections) == 2


def test_connect_signal_validates_nodes(project: str) -> None:
    result = godot_connect_signal.invoke(
        {
            "scene": "res://player.tscn",
            "signal": "timeout",
            "from_node": "Ghost",
            "to_node": ".",
            "method": "_on_timeout",
            "project": project,
        }
    )
    assert not result["ok"]


def test_scene_edits_stay_outside_the_project_boundary(project: str) -> None:
    """A res:// path must never resolve outside the project root."""
    result = godot_read_scene.invoke(
        {"scene": "res://../../escape.tscn", "project": project}
    )
    assert not result["ok"]
    assert "outside the project root" in result["error"]


# -- test tools -----------------------------------------------------------


def test_run_tests_explains_a_missing_addon(project: str) -> None:
    """The common first failure should name the fix, not just the symptom."""
    result = godot_run_tests.invoke({"project": project})
    assert not result["ok"]
    assert result["missing_addon"] == "gdUnit4"
    assert "addons/gdUnit4" in result["error"]


def test_scaffold_test_creates_a_suite(project: str) -> None:
    result = godot_scaffold_test.invoke(
        {"subject": "res://scripts/player.gd", "project": project}
    )
    assert result["ok"]
    suite = Path(project) / "tests" / "player_test.gd"
    assert suite.is_file()
    assert "extends GdUnitTestSuite" in suite.read_text(encoding="utf-8")


def test_scaffold_test_does_not_overwrite(project: str) -> None:
    args = {"subject": "res://scripts/player.gd", "project": project}
    assert godot_scaffold_test.invoke(args)["ok"]
    assert not godot_scaffold_test.invoke(args)["ok"]
