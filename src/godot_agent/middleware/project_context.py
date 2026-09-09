"""Inject the project's facts into the system prompt.

Without this the model guesses: it invents input action names that silently do
nothing, targets the wrong renderer, or re-derives the scene layout with a
handful of tool calls at the start of every turn. One cached fact sheet is far
cheaper than either failure mode.

The sheet is rebuilt only when ``project.godot`` changes on disk, so the cost
is one stat call per model invocation rather than a full project read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import SystemMessage

from godot_agent.engine.discovery import (
    GodotNotFoundError,
    ProjectNotFoundError,
    godot_version,
    read_project_info,
)

__all__ = ["GodotProjectMiddleware"]

#: How many entries of the project tree to list before truncating.
_MAX_TREE_ENTRIES = 60

#: Directories that are build output or engine cache, never worth listing.
_SKIP_DIRS = {".godot", ".git", "reports", "export", "__pycache__", ".import"}


class GodotProjectMiddleware(AgentMiddleware):
    """Prepend a cached Godot project fact sheet to every model request."""

    name = "GodotProjectMiddleware"

    def __init__(self, project_path: Path | str | None = None) -> None:
        super().__init__()
        self._project_path = Path(project_path) if project_path else None
        self._cached: str | None = None
        self._cache_key: tuple[str, float] | None = None

    # -- fact sheet -------------------------------------------------------

    def _tree(self, root: Path) -> list[str]:
        """A shallow listing of the project's own files, engine cache excluded."""
        entries: list[str] = []
        for path in sorted(root.rglob("*")):
            if len(entries) >= _MAX_TREE_ENTRIES:
                entries.append("... (truncated; use the filesystem tools to see more)")
                break
            relative = path.relative_to(root)
            if any(part in _SKIP_DIRS for part in relative.parts):
                continue
            if path.is_dir():
                continue
            if path.suffix in (".import", ".uid"):
                continue
            entries.append(relative.as_posix())
        return entries

    def _build(self) -> str:
        try:
            info = read_project_info(self._project_path)
        except ProjectNotFoundError as error:
            return (
                "## Godot project\n\n"
                f"No Godot project found: {error}\n"
                "Use `godot_scaffold_project` to create one before doing anything else."
            )

        try:
            version = f"Godot {godot_version()}"
        except GodotNotFoundError as error:
            version = f"Godot executable not found -- {error}"

        lines = [
            "## Godot project",
            "",
            f"Engine: {version}",
            info.summary(),
            "",
            "Files:",
            *(f"  {entry}" for entry in self._tree(info.root)),
        ]
        return "\n".join(lines)

    def _fact_sheet(self) -> str:
        """The cached sheet, rebuilt when ``project.godot`` changes."""
        try:
            info_root = read_project_info(self._project_path).root
            marker = info_root / "project.godot"
            key = (str(marker), marker.stat().st_mtime)
        except (ProjectNotFoundError, OSError):
            key = ("<none>", 0.0)

        if self._cached is None or key != self._cache_key:
            self._cached = self._build()
            self._cache_key = key
        return self._cached

    # -- middleware hooks -------------------------------------------------

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._augment(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._augment(request))

    def _augment(self, request: ModelRequest) -> ModelRequest:
        """Append the fact sheet to the request's system prompt.

        ``ModelRequest`` reads back as ``system_prompt`` but is overridden with
        a ``SystemMessage`` under ``system_message``, so the round trip goes
        through both names.
        """
        sheet = self._fact_sheet()
        existing = request.system_prompt or ""
        if sheet in existing:
            return request
        combined = f"{existing}\n\n{sheet}".strip()
        return request.override(system_message=SystemMessage(content=combined))
