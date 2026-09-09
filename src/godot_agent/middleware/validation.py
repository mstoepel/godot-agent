"""Validate Godot files the moment the agent writes them.

Without this, a malformed scene or a script with a syntax error is discovered
much later -- usually as a scene-load cascade whose first error points at a
file the agent never touched. Checking at the write closes that gap: the error
arrives attached to the edit that caused it, while the reasoning is still in
context.

The check itself lives in :mod:`godot_agent.validate`, because the dcode
``PostToolUse`` hook needs the identical answer from a separate process.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware
from langchain.tools.tool_node import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from godot_agent.validate import validate_file

__all__ = ["GodotValidationMiddleware"]

#: Tool names that write files, across dcode and the deep-agents filesystem.
_WRITE_TOOLS = frozenset({"write_file", "edit_file", "Write", "Edit", "str_replace"})

#: Argument names those tools use for the target path.
_PATH_ARGS = ("file_path", "path", "filename")


class GodotValidationMiddleware(AgentMiddleware):
    """Re-check ``.gd`` / ``.tscn`` / ``.tres`` files right after they are written."""

    name = "GodotValidationMiddleware"

    def __init__(
        self,
        project_path: Path | str | None = None,
        *,
        check_scripts: bool = True,
    ) -> None:
        super().__init__()
        self._project_path = Path(project_path) if project_path else None
        self._check_scripts = check_scripts

    # -- middleware hooks -------------------------------------------------

    def _target(self, request: ToolCallRequest) -> Path | None:
        """The file a write-shaped tool call just touched."""
        call = request.tool_call or {}
        if call.get("name") not in _WRITE_TOOLS:
            return None
        args = call.get("args") or {}
        for key in _PATH_ARGS:
            value = args.get(key)
            if isinstance(value, str) and value:
                return Path(value)
        return None

    def _check(self, request: ToolCallRequest) -> str | None:
        target = self._target(request)
        if target is None:
            return None
        return validate_file(
            target,
            project_path=self._project_path,
            check_scripts=self._check_scripts,
        )

    def _append(
        self,
        response: ToolMessage | Command,
        problem: str,
    ) -> ToolMessage | Command:
        """Attach the problem to the tool result the model will read."""
        if not isinstance(response, ToolMessage):
            return response
        return ToolMessage(
            content=f"{response.content}\n\n[godot-agent validation] {problem}",
            tool_call_id=response.tool_call_id,
            name=response.name,
            status=response.status,
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        response = handler(request)
        problem = self._check(request)
        return self._append(response, problem) if problem else response

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        response = await handler(request)
        problem = self._check(request)
        return self._append(response, problem) if problem else response
