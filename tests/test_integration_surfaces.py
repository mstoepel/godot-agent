"""Tests for the three surfaces the tools are exposed through.

The point of these is drift: the MCP server, the dcode plugin and the library
agent all read from one registry and one set of prompts, and these assert that
nothing has quietly forked.
"""

from __future__ import annotations

import asyncio
import io
import json
import shutil
import sys
from pathlib import Path

import pytest

from godot_agent.agent import SUBAGENT_DESCRIPTIONS, build_subagents, create_godot_agent
from godot_agent.config import Settings, reset_settings, set_settings
from godot_agent.hooks import run_hook
from godot_agent.profile import profile_files
from godot_agent.prompts import SUBAGENT_PROMPTS
from godot_agent.tools import tool_names
from godot_agent_mcp.server import build_server

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "scenes"


@pytest.fixture(autouse=True)
def _isolated_settings():
    set_settings(Settings(godot_bin=None, project_path=None))
    yield
    reset_settings()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    root = tmp_path / "game"
    root.mkdir()
    for name in ("project.godot", "player.tscn"):
        shutil.copy(FIXTURES / name, root / name)
    return root


# -- MCP surface ----------------------------------------------------------


def test_mcp_exposes_every_registered_tool() -> None:
    tools = asyncio.run(build_server().list_tools())
    assert {tool.name for tool in tools} == set(tool_names())


def test_mcp_schemas_keep_argument_descriptions() -> None:
    """Argument docs are the only place a caller learns that parent="." is the root."""
    tools = asyncio.run(build_server().list_tools())
    add_node = next(tool for tool in tools if tool.name == "godot_add_node")
    properties = add_node.input_schema["properties"]
    assert all("description" in value for value in properties.values())
    assert "root" in properties["parent"]["description"]


def test_mcp_tool_call_returns_structured_content() -> None:
    result = asyncio.run(build_server().call_tool("godot_doctor", {}))
    assert result.structured_content is not None
    assert "ok" in result.structured_content


# -- agent wiring ---------------------------------------------------------


def test_agent_graph_builds() -> None:
    agent = create_godot_agent(model="claude-sonnet-5")
    assert "model" in agent.get_graph().nodes


def test_subagents_get_only_their_own_tools() -> None:
    by_name = {sub["name"]: sub for sub in build_subagents()}
    art_tools = {tool.name for tool in by_name["art-director"]["tools"]}
    scene_tools = {tool.name for tool in by_name["scene-builder"]["tools"]}
    assert "godot_add_node" in scene_tools
    assert "godot_add_node" not in art_tools


def test_every_subagent_has_a_prompt_and_description() -> None:
    for sub in build_subagents():
        assert sub["system_prompt"].strip()
        assert sub["description"].strip()
        assert sub["tools"]


# -- dcode profile --------------------------------------------------------


def test_checked_in_profile_matches_the_prompts() -> None:
    """The profile is generated; a stale copy would make dcode and the library diverge."""
    root = REPO_ROOT / "dcode-profile"
    for relative, expected in profile_files().items():
        path = root / relative
        assert path.is_file(), f"{relative} is missing; run `godot-agent sync-profile`"
        actual = path.read_text(encoding="utf-8")
        assert actual == expected, f"{relative} is stale; run `godot-agent sync-profile`"


def test_profile_subagents_cover_the_agent_subagents() -> None:
    generated = set(profile_files())
    for name in SUBAGENT_PROMPTS:
        assert f"agents/{name}/AGENTS.md" in generated
        assert name in SUBAGENT_DESCRIPTIONS


def test_plugin_manifest_points_at_files_that_exist() -> None:
    manifest = json.loads(
        (REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8")
    )
    plugin_root = REPO_ROOT / "plugin"
    for key in ("skills", "mcpServers", "hooks"):
        target = plugin_root / manifest[key].removeprefix("./")
        assert target.exists(), f"{key} -> {manifest[key]} does not exist"

    extension = manifest["extensions"]["com.langchain.deepagents.code"]["pythonExtensions"]
    assert (plugin_root / extension.removeprefix("./")).is_file()


def test_every_skill_has_usable_front_matter() -> None:
    """dcode reads name and description to decide when to surface a skill."""
    skills = sorted((REPO_ROOT / "plugin" / "skills").glob("*/SKILL.md"))
    assert skills, "no skills found"
    for skill in skills:
        text = skill.read_text(encoding="utf-8")
        assert text.startswith("---\n"), skill.name
        front = text.split("---")[1]
        assert f"name: {skill.parent.name}" in front, skill.name
        assert "description:" in front, skill.name


def test_hooks_reference_real_hook_handlers() -> None:
    hooks = json.loads(
        (REPO_ROOT / "plugin" / "hooks" / "hooks.json").read_text(encoding="utf-8")
    )["hooks"]
    invoked = {
        handler["command"].split()[-1]
        for groups in hooks.values()
        for group in groups
        for handler in group["hooks"]
    }
    assert invoked <= {"session-start", "guard-write", "validate"}


# -- hook behaviour -------------------------------------------------------


def _run_hook(name: str, event: dict, monkeypatch) -> tuple[int, str]:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    code = run_hook(name)
    return code, out.getvalue()


def test_guard_write_blocks_the_import_cache(monkeypatch) -> None:
    code, out = _run_hook(
        "guard-write",
        {"tool_input": {"file_path": "game/.godot/imported/x.res"}},
        monkeypatch,
    )
    assert code == 0
    payload = json.loads(out)["hookSpecificOutput"]
    assert payload["permissionDecision"] == "deny"
    assert "godot_import" in payload["permissionDecisionReason"]


def test_guard_write_blocks_import_sidecars(monkeypatch) -> None:
    code, out = _run_hook(
        "guard-write", {"tool_input": {"file_path": "art/player.png.import"}}, monkeypatch
    )
    assert json.loads(out)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert code == 0


def test_guard_write_allows_ordinary_files(monkeypatch) -> None:
    code, out = _run_hook(
        "guard-write", {"tool_input": {"file_path": "scripts/player.gd"}}, monkeypatch
    )
    assert code == 0
    assert out == ""


def test_validate_blocks_a_broken_scene(monkeypatch, tmp_path: Path) -> None:
    broken = tmp_path / "broken.tscn"
    broken.write_text("size = Vector2(1, 1)\n", encoding="utf-8")
    code, _ = _run_hook("validate", {"tool_input": {"file_path": str(broken)}}, monkeypatch)
    assert code == 2


def test_validate_passes_a_good_scene(monkeypatch, project: Path) -> None:
    code, _ = _run_hook(
        "validate", {"tool_input": {"file_path": str(project / "player.tscn")}}, monkeypatch
    )
    assert code == 0


def test_hooks_never_raise_on_junk_input(monkeypatch) -> None:
    """A crashing hook that denies every call is worse than a missed check."""
    for name in ("session-start", "guard-write", "validate"):
        monkeypatch.setattr(sys, "stdin", io.StringIO("not json at all"))
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        assert run_hook(name) == 0


def test_unknown_hook_is_ignored(monkeypatch) -> None:
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    assert run_hook("no-such-hook") == 0
