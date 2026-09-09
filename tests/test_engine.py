"""Tests for output parsing, project discovery and project inspection."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from godot_agent.config import Settings, reset_settings, set_settings
from godot_agent.engine.discovery import (
    ProjectNotFoundError,
    find_project_root,
    read_project_info,
)
from godot_agent.engine.errors import parse_output, summarize

FIXTURES = Path(__file__).parent / "fixtures" / "scenes"


@pytest.fixture(autouse=True)
def _isolated_settings():
    """Keep ambient GODOT_* environment out of the unit tests."""
    set_settings(Settings(godot_bin=None, project_path=None))
    yield
    reset_settings()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """A real Godot project laid out on disk, from the downloaded fixtures."""
    root = tmp_path / "game"
    (root / "addons" / "gdUnit4").mkdir(parents=True)
    for name in ("project.godot", "player.tscn", "main.tscn"):
        shutil.copy(FIXTURES / name, root / name)
    return root


# -- output parsing -------------------------------------------------------


def test_parses_engine_error_with_location() -> None:
    output = (
        'ERROR: Condition "!is_inside_tree()" is true.\n'
        "   at: get_global_transform (scene/2d/node_2d.cpp:196)\n"
    )
    (diagnostic,) = parse_output(output)
    assert diagnostic.severity == "error"
    assert diagnostic.message == 'Condition "!is_inside_tree()" is true.'
    assert diagnostic.file == "scene/2d/node_2d.cpp"
    assert diagnostic.line == 196
    assert diagnostic.function == "get_global_transform"
    assert not diagnostic.is_project_local


def test_parses_script_error_pointing_into_the_project() -> None:
    output = (
        "SCRIPT ERROR: Invalid access to property or key 'x' on a base object of type 'Nil'.\n"
        "   at: _process (res://player.gd:25)\n"
    )
    (diagnostic,) = parse_output(output)
    assert diagnostic.severity == "error"
    assert diagnostic.file == "res://player.gd"
    assert diagnostic.line == 25
    assert diagnostic.is_project_local


def test_parses_inline_parse_error() -> None:
    output = (
        "res://player.gd:12 - Parse Error: "
        'Identifier "foo" not declared in the current scope.\n'
    )
    (diagnostic,) = parse_output(output)
    assert diagnostic.file == "res://player.gd"
    assert diagnostic.line == 12
    assert "Identifier" in diagnostic.message
    assert diagnostic.is_project_local


def test_parses_warnings() -> None:
    (diagnostic,) = parse_output("WARNING: The signal is not connected.\n")
    assert diagnostic.severity == "warning"


def test_drops_banner_noise() -> None:
    output = (
        "Godot Engine v4.7.2.stable.official - https://godotengine.org\n"
        "Vulkan API 1.3.280 - Forward+ - Using Vulkan Device #0\n"
        "ERROR: real problem\n"
    )
    diagnostics = parse_output(output)
    assert len(diagnostics) == 1
    assert diagnostics[0].message == "real problem"


def test_keeps_unrecognized_continuation_lines_as_details() -> None:
    output = "ERROR: Failed to load resource.\n    Extra context line.\n"
    (diagnostic,) = parse_output(output)
    assert diagnostic.details == ["Extra context line."]


def test_summarize_puts_actionable_errors_first() -> None:
    """A project-local error must outrank engine-internal noise it caused."""
    output = (
        "WARNING: a warning\n"
        'ERROR: Condition "!p_scene" is true.\n'
        "   at: instantiate (scene/resources/packed_scene.cpp:1)\n"
        "SCRIPT ERROR: Parse Error: something the agent can fix\n"
        "   at: _ready (res://player.gd:3)\n"
    )
    lines = summarize(parse_output(output)).splitlines()
    assert lines[0].startswith("res://player.gd:3:")
    assert lines[-1].endswith("a warning")


def test_summarize_reports_a_clean_run() -> None:
    assert summarize([]) == "No errors or warnings."


def test_summarize_truncates() -> None:
    output = "\n".join(f"ERROR: problem {index}" for index in range(30))
    assert "... and 10 more" in summarize(parse_output(output), limit=20)


# -- project discovery ----------------------------------------------------


def test_finds_project_root_from_a_nested_path(project: Path) -> None:
    nested = project / "scripts" / "enemies"
    nested.mkdir(parents=True)
    assert find_project_root(nested) == project


def test_finds_project_root_from_a_file(project: Path) -> None:
    assert find_project_root(project / "player.tscn") == project


def test_reports_a_missing_project(tmp_path: Path) -> None:
    with pytest.raises(ProjectNotFoundError, match=r"No project\.godot"):
        find_project_root(tmp_path)


# -- project inspection ---------------------------------------------------


def test_reads_project_facts(project: Path) -> None:
    info = read_project_info(project)
    assert info.root == project
    assert info.name == "Dodge the Creeps"
    assert info.main_scene == "res://main.tscn"
    assert "gdUnit4" in info.addons


def test_reads_input_actions(project: Path) -> None:
    """The input map drives every control decision the agent makes."""
    info = read_project_info(project)
    assert "move_right" in info.input_actions
    assert "move_left" in info.input_actions


def test_summary_is_renderable(project: Path) -> None:
    summary = read_project_info(project).summary()
    assert "Project: Dodge the Creeps" in summary
    assert "Main scene: res://main.tscn" in summary
    assert "gdUnit4" in summary


def test_survives_an_unparseable_setting(project: Path) -> None:
    """One exotic value must not make the whole project unreadable."""
    config = (project / "project.godot").read_text(encoding="utf-8")
    (project / "project.godot").write_text(
        config + "\n[weird]\n\nbroken=@@@not a value@@@\n", encoding="utf-8"
    )
    info = read_project_info(project)
    assert info.name == "Dodge the Creeps"
