"""The single tool registry.

Every surface reads from here: the MCP stdio server, the dcode Python
extension, and the in-process agent factory. Defining a tool once and exposing
it three ways is the whole reason this module exists -- three parallel
definitions would drift within a week.

Tools are grouped so a subagent can take only the slice it needs. A subagent
handed every tool spends its context deciding which to ignore.
"""

from __future__ import annotations

from langchain_core.tools import BaseTool

from godot_agent.tools.assets import (
    godot_generate_image,
    godot_generate_model,
    godot_generate_pixel_art,
    godot_list_asset_providers,
)
from godot_agent.tools.playtest import (
    godot_install_bridge,
    godot_playtest_input,
    godot_playtest_screenshot,
    godot_playtest_state,
    godot_playtest_status,
)
from godot_agent.tools.project import (
    godot_doctor,
    godot_import,
    godot_project_info,
    godot_scaffold_project,
)
from godot_agent.tools.run import godot_check_script, godot_run, godot_run_script
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

__all__ = ["TOOL_GROUPS", "all_tools", "tool_names", "tools_for"]

#: Tools grouped by the job they serve. Keys are referenced by subagent
#: definitions, so renaming one is a breaking change for those.
TOOL_GROUPS: dict[str, list[BaseTool]] = {
    "project": [
        godot_doctor,
        godot_project_info,
        godot_import,
        godot_scaffold_project,
    ],
    "scene": [
        godot_read_scene,
        godot_create_scene,
        godot_add_node,
        godot_set_node_property,
        godot_remove_node,
        godot_attach_script,
        godot_connect_signal,
    ],
    "run": [
        godot_run,
        godot_check_script,
        godot_run_script,
    ],
    "test": [
        godot_run_tests,
        godot_scaffold_test,
    ],
    "playtest": [
        godot_install_bridge,
        godot_playtest_status,
        godot_playtest_input,
        godot_playtest_state,
        godot_playtest_screenshot,
    ],
    "assets": [
        godot_list_asset_providers,
        godot_generate_pixel_art,
        godot_generate_image,
        godot_generate_model,
    ],
}


def all_tools() -> list[BaseTool]:
    """Every registered tool, deduplicated, in group order."""
    seen: dict[str, BaseTool] = {}
    for group in TOOL_GROUPS.values():
        for item in group:
            seen.setdefault(item.name, item)
    return list(seen.values())


def tools_for(*groups: str) -> list[BaseTool]:
    """Return the tools in the named groups.

    Raises:
        KeyError: on an unknown group name, so a typo in a subagent definition
            fails loudly at startup rather than silently handing it no tools.
    """
    out: dict[str, BaseTool] = {}
    for name in groups:
        for item in TOOL_GROUPS[name]:
            out.setdefault(item.name, item)
    return list(out.values())


def tool_names() -> list[str]:
    """The name of every registered tool."""
    return [item.name for item in all_tools()]
