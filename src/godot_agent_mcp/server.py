"""An MCP stdio server exposing the Godot tool registry.

This is the tool surface that works in ``dcode`` today, and in any other MCP
client, without depending on dcode's experimental in-process extension API.
It re-exports :mod:`godot_agent.tools` rather than redefining anything, so the
three surfaces cannot drift apart.

Run it with ``godot-agent-mcp``, or wire it into a client's config::

    {"mcpServers": {"godot": {"command": "godot-agent-mcp"}}}
"""

from __future__ import annotations

import argparse
import os
from typing import Any

from langchain_core.tools import BaseTool
from mcp.server.mcpserver import MCPServer

from godot_agent import __version__
from godot_agent.config import Settings, set_settings
from godot_agent.tools import all_tools

__all__ = ["build_server", "main"]

_INSTRUCTIONS = """\
Tools for developing 2D games in Godot 4.

Orient first with `godot_doctor` and `godot_project_info`. Edit scenes with the
`godot_*` scene tools rather than writing .tscn text by hand -- hand-written
scene files are frequently unloadable. Run `godot_import` after adding any
asset, and verify changes with `godot_check_script` and `godot_run`.
"""


def build_server(project_path: str | None = None) -> MCPServer:
    """Build the MCP server with every registered Godot tool."""
    if project_path:
        set_settings(Settings(project_path=project_path))  # type: ignore[arg-type]

    server = MCPServer(
        name="godot-agent",
        title="Godot Agent",
        version=__version__,
        instructions=_INSTRUCTIONS,
    )

    for item in all_tools():
        function = getattr(item, "func", None) or getattr(item, "coroutine", None)
        if function is None:  # pragma: no cover - every registered tool wraps one
            raise TypeError(f"tool {item.name} has no underlying callable to expose")
        server.add_tool(function, name=item.name, description=item.description)
        _copy_argument_descriptions(server, item)

    return server


def _copy_argument_descriptions(server: MCPServer, tool: BaseTool) -> None:
    """Carry per-argument descriptions from the LangChain schema to the MCP one.

    Both derive their schema from the same function, but only LangChain reads
    the ``Annotated[str, "..."]`` metadata we document arguments with. Without
    this the MCP client sees bare types, and an argument like ``parent`` -- where
    the caller has to know that ``"."`` means the scene root -- becomes guesswork.
    """
    source = tool.args_schema
    if source is None:
        return
    described = {
        key: value["description"]
        for key, value in source.model_json_schema().get("properties", {}).items()
        if isinstance(value, dict) and "description" in value
    }
    if not described:
        return

    registered = server._tool_manager.get_tool(tool.name)
    if registered is None:
        return
    for key, description in described.items():
        target = registered.parameters.get("properties", {}).get(key)
        if isinstance(target, dict):
            target.setdefault("description", description)


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``godot-agent-mcp`` console script."""
    parser = argparse.ArgumentParser(
        prog="godot-agent-mcp",
        description="MCP stdio server exposing Godot game-development tools.",
    )
    parser.add_argument(
        "--project",
        default=os.environ.get("GODOT_PROJECT"),
        help="Godot project directory. Defaults to $GODOT_PROJECT, then discovery.",
    )
    args = parser.parse_args(argv)

    build_server(args.project).run(transport="stdio")
    return 0


def _entrypoint() -> Any:  # pragma: no cover - console-script shim
    raise SystemExit(main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
