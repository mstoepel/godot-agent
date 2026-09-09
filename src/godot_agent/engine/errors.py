"""Turn Godot's console output into structured diagnostics.

Godot reports problems in a handful of shapes, and a raw dump of them is
expensive for a model to read and easy to misread. Three forms matter:

Engine errors, with the location on a continuation line::

    ERROR: Condition "!is_inside_tree()" is true.
       at: get_global_transform (scene/2d/node_2d.cpp:196)

Script runtime errors, whose continuation line points into the project::

    SCRIPT ERROR: Invalid access to property or key 'x' on a base object of type 'Nil'.
       at: _process (res://player.gd:25)

Parse errors emitted while loading a script, which carry their own location::

    res://player.gd:12 - Parse Error: Identifier "foo" not declared in the current scope.

Anything unrecognized is preserved verbatim rather than dropped, so a message
shape we have not seen still reaches the caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

__all__ = ["Diagnostic", "Severity", "parse_output", "summarize"]

Severity = Literal["error", "warning"]

#: ``ERROR:``, ``SCRIPT ERROR:``, ``USER WARNING:`` and friends.
_HEADER_RE = re.compile(
    r"^\s*(?:(?P<origin>USER|SCRIPT)\s+)?(?P<severity>ERROR|WARNING)\s*:\s*(?P<message>.*)$"
)

#: The ``at: function (file:line)`` continuation line.
_AT_RE = re.compile(r"^\s*at:\s*(?P<function>.*?)\s*\((?P<file>[^()]*?):(?P<line>\d+)\)\s*$")

#: A parse error that carries its own ``res://file.gd:12 - Message`` location.
_INLINE_RE = re.compile(
    r"^\s*(?P<file>res://[^\s:]+):(?P<line>\d+)\s*[-:]\s*(?P<message>.+?)\s*$"
)

#: Lines that are noise in every run and never worth showing the model.
_NOISE_PREFIXES = (
    "Godot Engine v",
    "OpenGL API ",
    "Vulkan API ",
    "TextServer: ",
    "Using ",
)


@dataclass(slots=True)
class Diagnostic:
    """One problem reported by the engine."""

    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    function: str | None = None
    #: Extra lines the engine attached to this diagnostic, in order.
    details: list[str] = field(default_factory=list)

    @property
    def is_project_local(self) -> bool:
        """True when the location points into the project rather than engine C++.

        Engine-internal locations are usually a symptom, not the cause; the
        agent should look at project-local diagnostics first.
        """
        return bool(self.file and self.file.startswith("res://"))

    def format(self) -> str:
        """Render as a single ``file:line: severity: message`` line."""
        where = ""
        if self.file:
            where = f"{self.file}:{self.line}: " if self.line else f"{self.file}: "
        suffix = f" (in {self.function})" if self.function else ""
        return f"{where}{self.severity}: {self.message}{suffix}"


def _is_noise(line: str) -> bool:
    return any(line.startswith(prefix) for prefix in _NOISE_PREFIXES)


def parse_output(text: str) -> list[Diagnostic]:
    """Extract diagnostics from Godot's combined stdout and stderr."""
    diagnostics: list[Diagnostic] = []
    current: Diagnostic | None = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line.strip() or _is_noise(line.strip()):
            continue

        at_match = _AT_RE.match(line)
        if at_match and current is not None and current.file is None:
            current.function = at_match["function"] or None
            current.file = at_match["file"]
            current.line = int(at_match["line"])
            continue

        header = _HEADER_RE.match(line)
        if header:
            current = Diagnostic(
                severity="error" if header["severity"] == "ERROR" else "warning",
                message=header["message"].strip(),
            )
            diagnostics.append(current)
            continue

        inline = _INLINE_RE.match(line)
        if inline:
            message = inline["message"].strip()
            current = Diagnostic(
                severity="warning" if message.lower().startswith("warning") else "error",
                message=message,
                file=inline["file"],
                line=int(inline["line"]),
            )
            diagnostics.append(current)
            continue

        # An indented line right after a diagnostic is extra context for it.
        if current is not None and raw_line.startswith((" ", "\t")):
            current.details.append(line.strip())

    return diagnostics


def summarize(diagnostics: list[Diagnostic], limit: int = 20) -> str:
    """Render diagnostics for the model, project-local ones first.

    Ordering by relevance rather than by time matters: a single project-local
    parse error typically triggers a cascade of engine-internal errors, and the
    first line the model reads should be the one it can act on.
    """
    if not diagnostics:
        return "No errors or warnings."

    def sort_key(item: tuple[int, Diagnostic]) -> tuple[int, int, int]:
        index, diagnostic = item
        return (
            0 if diagnostic.severity == "error" else 1,
            0 if diagnostic.is_project_local else 1,
            index,
        )

    ordered = [d for _, d in sorted(enumerate(diagnostics), key=sort_key)]
    lines = [diagnostic.format() for diagnostic in ordered[:limit]]
    if len(ordered) > limit:
        lines.append(f"... and {len(ordered) - limit} more")
    return "\n".join(lines)
