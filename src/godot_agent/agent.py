"""Build the Godot agent.

This is the programmatic surface: a ``create_deep_agent`` graph with the Godot
tools, middleware and subagents already wired. The dcode plugin and the MCP
server expose the same tools; this is the path used for headless runs, CI and
evals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from deepagents.middleware.subagents import SubAgent
from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from godot_agent.middleware import GodotProjectMiddleware, GodotValidationMiddleware
from godot_agent.prompts import MAIN_PROMPT, SUBAGENT_PROMPTS
from godot_agent.tools import all_tools, tools_for

__all__ = ["SUBAGENT_DESCRIPTIONS", "build_subagents", "create_godot_agent"]

#: Model used when the caller does not pick one.
DEFAULT_MODEL = "claude-sonnet-5"

#: Which tool groups each subagent gets. A subagent handed every tool spends
#: its context deciding what to ignore, so each takes only its own slice.
_SUBAGENT_TOOLS: dict[str, tuple[str, ...]] = {
    "gdscript-engineer": ("project", "run"),
    "scene-builder": ("project", "scene", "run"),
    "qa-tester": ("project", "run", "test", "playtest"),
    "art-director": ("project", "assets"),
}

SUBAGENT_DESCRIPTIONS: dict[str, str] = {
    "gdscript-engineer": (
        "Implements a focused piece of gameplay logic in GDScript and verifies it "
        "compiles. Use for non-trivial scripts; does not edit scenes."
    ),
    "scene-builder": (
        "Builds and edits .tscn scene files: node trees, properties, attached "
        "scripts and signal wiring. Use when a scene needs more than a node or two."
    ),
    "qa-tester": (
        "Writes and runs gdUnit4 tests and playtests the game headlessly. Use to "
        "verify a feature works and to leave regression tests behind."
    ),
    "art-director": (
        "Generates and integrates 2D art, keeping it consistent with the project's "
        "style guide. Use for sprites, tilesets and UI art."
    ),
}


def build_subagents(
    extra_tools: dict[str, list[BaseTool]] | None = None,
    model: str | BaseChatModel | None = None,
) -> list[SubAgent]:
    """Build the subagent definitions.

    Args:
        extra_tools: Additional tools per subagent name, merged with its group
            slice. The asset pipeline uses this to give ``art-director`` the
            generation tools without every other agent seeing them.
        model: Model override applied to every subagent.
    """
    extra_tools = extra_tools or {}
    subagents: list[SubAgent] = []
    for name, groups in _SUBAGENT_TOOLS.items():
        tools = [*tools_for(*groups), *extra_tools.get(name, [])]
        definition: SubAgent = {
            "name": name,
            "description": SUBAGENT_DESCRIPTIONS[name],
            "system_prompt": SUBAGENT_PROMPTS[name],
            "tools": tools,
        }
        if model is not None:
            definition["model"] = model
        subagents.append(definition)
    return subagents


def create_godot_agent(
    model: str | BaseChatModel | None = None,
    *,
    project_path: Path | str | None = None,
    tools: list[BaseTool] | None = None,
    middleware: list[AgentMiddleware] | None = None,
    subagents: list[SubAgent] | None = None,
    system_prompt: str | None = None,
    subagent_model: str | BaseChatModel | None = None,
    validate_writes: bool = True,
    **kwargs: Any,
) -> Any:
    """Create the Godot game-development agent.

    Args:
        model: Chat model or model id. Defaults to :data:`DEFAULT_MODEL`.
        project_path: The Godot project to work in. Defaults to discovery from
            the working directory.
        tools: Replaces the Godot tool set entirely. Usually you want to leave
            this alone and pass extras through ``kwargs`` instead.
        middleware: Extra middleware, appended after the Godot middleware.
        subagents: Replaces the default subagents.
        system_prompt: Replaces the main prompt.
        subagent_model: A cheaper model for subagents, if you want one.
        validate_writes: Re-check scripts and scenes as they are written.
        **kwargs: Passed through to ``create_deep_agent`` (``backend``,
            ``checkpointer``, ``skills``, ``memory``, ``interrupt_on``, ...).

    Returns:
        A compiled LangGraph agent.
    """
    godot_middleware: list[AgentMiddleware] = [GodotProjectMiddleware(project_path)]
    if validate_writes:
        godot_middleware.append(GodotValidationMiddleware(project_path))
    godot_middleware.extend(middleware or [])

    return create_deep_agent(
        model=model or DEFAULT_MODEL,
        tools=tools if tools is not None else all_tools(),
        system_prompt=system_prompt or MAIN_PROMPT,
        middleware=godot_middleware,
        subagents=subagents if subagents is not None else build_subagents(model=subagent_model),
        **kwargs,
    )
