"""dcode Python extension: register the Godot tools and middleware in-process.

This is the optional fast path. It requires ``DEEPAGENTS_CODE_EXPERIMENTAL=1``
and is not the primary surface -- the MCP server in ``.mcp.json`` provides the
same tools without an experimental flag, and the hooks in ``hooks/hooks.json``
provide the same validation. Everything here is additive: with the extension
disabled the plugin still works, just out of process.

Registering both here and over MCP would double every tool, so the MCP server
is expected to be the one in use unless a user deliberately turns this on.
"""

from __future__ import annotations

import os

from deepagents_code.extensions import ExtensionAPI

# `godot_agent` must be importable by the dcode process. Installing this
# package with `uv tool install godot-agent` or `pip install -e .` handles it.
from godot_agent.middleware import GodotProjectMiddleware, GodotValidationMiddleware
from godot_agent.tools import all_tools

#: Set to "1" to skip tool registration when the MCP server already provides
#: them, keeping only the middleware.
_MIDDLEWARE_ONLY = "GODOT_AGENT_EXTENSION_MIDDLEWARE_ONLY"


async def extension(d: ExtensionAPI) -> None:
    """Register Godot tools and middleware with the dcode agent server.

    Args:
        d: The dcode extension API.
    """
    if os.environ.get(_MIDDLEWARE_ONLY) != "1":
        for item in all_tools():
            d.register_tool(item)

    # Instances rather than classes: both take constructor arguments, and the
    # extension API only builds classes that have a zero-argument constructor.
    d.register_middleware(GodotProjectMiddleware(d.cwd))
    d.register_middleware(GodotValidationMiddleware(d.cwd))
