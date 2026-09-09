"""Parser and writer for Godot's ``.tscn`` / ``.tres`` text format.

A scene file is a flat list of bracketed sections::

    [gd_scene load_steps=3 format=3 uid="uid://b1"]

    [ext_resource type="Script" path="res://player.gd" id="1_abc"]

    [sub_resource type="RectangleShape2D" id="Rect_x1"]
    size = Vector2(32, 32)

    [node name="Player" type="CharacterBody2D"]
    script = ExtResource("1_abc")

    [connection signal="timeout" from="Timer" to="." method="_on_timeout"]

Header attributes and property values are kept as raw source text. Nothing is
coerced on read, so a file the agent only inspects round-trips byte for byte,
and an edit rewrites exactly the one value it touched. That matters more than
convenience here: a scene file the engine cannot load takes the whole project
down, and silent reformatting makes review diffs unreadable.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from godot_agent.gdformat.values import GDParseError, dumps, parse_value

__all__ = [
    "Section",
    "TscnFile",
    "TscnParseError",
    "parse_tscn",
    "read_tscn",
]


class TscnParseError(ValueError):
    """Raised when a scene or resource file is not well formed."""


#: Section kinds the engine writes one-per-line without blank separators.
_COMPACT_KINDS = frozenset({"ext_resource", "connection", "editable"})


def _split_outside_strings(text: str) -> Iterator[str]:
    """Yield whitespace-separated chunks of ``text``, ignoring spaces in strings."""
    chunk: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            chunk.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            chunk.append(char)
        elif char.isspace():
            if chunk:
                yield "".join(chunk)
                chunk = []
        else:
            chunk.append(char)
    if chunk:
        yield "".join(chunk)


def _balanced(text: str) -> bool:
    """Return True when brackets and quotes in ``text`` are balanced."""
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth -= 1
    return depth <= 0 and not in_string


@dataclass(slots=True, eq=False)
class Section:
    """One bracketed section and the properties that follow it.

    Compared by identity, not by value: sections are mutable and a scene may
    legitimately hold two identical-looking ones, so callers that collect
    sections in a set mean "these particular objects".

    Attributes:
        kind: The first word of the header, e.g. ``node`` or ``ext_resource``.
        attributes: Header attributes as raw text, in order.
        properties: ``key = value`` lines as raw text, in order.
    """

    kind: str
    attributes: dict[str, str] = field(default_factory=dict)
    properties: dict[str, str] = field(default_factory=dict)

    # -- header attributes ------------------------------------------------

    def attr(self, name: str, default: object = None) -> object:
        """Return one header attribute parsed into a Python value."""
        raw = self.attributes.get(name)
        if raw is None:
            return default
        try:
            return parse_value(raw)
        except GDParseError:
            return raw

    def attr_str(self, name: str, default: str | None = None) -> str | None:
        """Return one header attribute as a string, unquoting when needed."""
        value = self.attr(name)
        if value is None:
            return default
        return value if isinstance(value, str) else str(value)

    def set_attr(self, name: str, value: object) -> None:
        """Set one header attribute from a Python value, serializing it."""
        self.attributes[name] = dumps(value)

    # -- properties -------------------------------------------------------

    def get(self, key: str, default: object = None) -> object:
        """Return one property parsed into a Python value."""
        raw = self.properties.get(key)
        if raw is None:
            return default
        try:
            return parse_value(raw)
        except GDParseError:
            return raw

    def set(self, key: str, value: object) -> None:
        """Set one property from a Python value, serializing it."""
        self.properties[key] = dumps(value)

    def set_raw(self, key: str, raw_value: str) -> None:
        """Set one property from already-serialized Godot text."""
        self.properties[key] = raw_value

    # -- serialization ----------------------------------------------------

    def header_text(self) -> str:
        parts = [self.kind]
        parts += [f"{key}={value}" for key, value in self.attributes.items()]
        return "[" + " ".join(parts) + "]"

    def dumps(self) -> str:
        lines = [self.header_text()]
        lines += [f"{key} = {value}" for key, value in self.properties.items()]
        return "\n".join(lines)


@dataclass(slots=True)
class TscnFile:
    """A parsed ``.tscn`` or ``.tres`` file."""

    sections: list[Section] = field(default_factory=list)

    @property
    def descriptor(self) -> Section | None:
        """The leading ``gd_scene`` / ``gd_resource`` section, if present."""
        if self.sections and self.sections[0].kind in ("gd_scene", "gd_resource"):
            return self.sections[0]
        return None

    def of_kind(self, kind: str) -> list[Section]:
        """Return every section of one kind, in file order."""
        return [section for section in self.sections if section.kind == kind]

    @property
    def nodes(self) -> list[Section]:
        """Every ``[node ...]`` section, in file order (parents precede children)."""
        return self.of_kind("node")

    @property
    def ext_resources(self) -> list[Section]:
        return self.of_kind("ext_resource")

    @property
    def sub_resources(self) -> list[Section]:
        return self.of_kind("sub_resource")

    @property
    def connections(self) -> list[Section]:
        return self.of_kind("connection")

    def dumps(self) -> str:
        """Serialize back to scene text.

        Reproduces the engine's own blank-line policy: single-line sections of
        the same kind (the ``ext_resource`` block, the trailing ``connection``
        block) stay packed together, and everything else is separated by a
        blank line. Matching it exactly is what lets an untouched file
        round-trip byte for byte.
        """
        out: list[str] = []
        previous: Section | None = None
        for section in self.sections:
            if previous is not None:
                packed = (
                    section.kind == previous.kind
                    and section.kind in _COMPACT_KINDS
                    and not previous.properties
                    and not section.properties
                )
                out.append("\n" if packed else "\n\n")
            out.append(section.dumps())
            previous = section
        return "".join(out) + "\n"

    def write(self, path: Path | str) -> None:
        """Write to disk with Unix line endings, as Godot does."""
        Path(path).write_text(self.dumps(), encoding="utf-8", newline="\n")


def _parse_header(line: str) -> Section:
    inner = line.strip()[1:-1].strip()
    if not inner:
        raise TscnParseError(f"empty section header: {line!r}")
    chunks = list(_split_outside_strings(inner))
    kind = chunks[0]
    if "=" in kind:
        raise TscnParseError(f"section header missing a kind: {line!r}")
    section = Section(kind=kind)
    for chunk in chunks[1:]:
        key, sep, value = chunk.partition("=")
        if not sep or not key or not value:
            raise TscnParseError(f"malformed header attribute {chunk!r} in {line!r}")
        section.attributes[key] = value
    return section


def parse_tscn(text: str) -> TscnFile:
    """Parse ``.tscn`` / ``.tres`` text.

    Raises:
        TscnParseError: if a header is malformed or a property appears before
            any section header.
    """
    scene = TscnFile()
    current: Section | None = None

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("["):
            # A header wraps when an attribute holds a long array.
            while not _balanced(stripped) and index < len(lines):
                stripped = stripped + " " + lines[index].strip()
                index += 1
            if not stripped.endswith("]"):
                raise TscnParseError(f"unterminated section header: {line!r}")
            current = _parse_header(stripped)
            scene.sections.append(current)
            continue
        if current is None:
            raise TscnParseError(f"property outside of any section: {line!r}")
        key, sep, value = line.partition("=")
        if not sep:
            raise TscnParseError(f"malformed property line: {line!r}")
        key = key.strip()
        value = value.strip()
        while not _balanced(value) and index < len(lines):
            value = value + "\n" + lines[index]
            index += 1
        current.properties[key] = value

    return scene


def read_tscn(path: Path | str) -> TscnFile:
    """Read and parse a scene or resource file from disk (UTF-8)."""
    return parse_tscn(Path(path).read_text(encoding="utf-8"))
