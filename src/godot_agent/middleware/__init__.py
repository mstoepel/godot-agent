"""Middleware that adapts a generic coding agent into a Godot one."""

from godot_agent.middleware.project_context import GodotProjectMiddleware
from godot_agent.middleware.validation import GodotValidationMiddleware

__all__ = ["GodotProjectMiddleware", "GodotValidationMiddleware"]
