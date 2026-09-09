"""Parse and serialize Godot variant literals.

Godot's text formats store property values as GDScript-like literals::

    "a string"          &"a StringName"     ^"a/node/path"
    12   -1.5   1e-05   inf   -inf   nan    true   false   null
    [1, 2, 3]           {"key": "value"}
    Vector2(0, 1)       Color(1, 1, 1, 1)   PackedStringArray("a", "b")
    SubResource("Node_a1b2")                ExtResource("1_c3d4")

Constructor-style values keep their type name rather than being coerced into a
Python type, because the agent needs to write them back out unchanged. Callers
that only need to *read* a file should prefer the raw text kept by the parsers
in :mod:`godot_agent.tscn`; this module exists for the cases where a value has
to be inspected or edited structurally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "GDCall",
    "GDNodePath",
    "GDParseError",
    "GDStringName",
    "dumps",
    "parse_value",
]


class GDParseError(ValueError):
    """Raised when a value literal cannot be parsed."""


@dataclass(frozen=True, slots=True)
class GDStringName:
    """A Godot ``StringName`` literal, written ``&"name"``."""

    value: str


@dataclass(frozen=True, slots=True)
class GDNodePath:
    """A Godot ``NodePath`` literal, written ``^"a/b"``."""

    value: str


@dataclass(frozen=True, slots=True)
class GDCall:
    """A constructor-style value such as ``Vector2(0, 1)``.

    ``Object(...)`` and ``Dictionary(...)`` values mix positional and keyed
    arguments, so both are kept: ``args`` holds the positional part in order and
    ``kwargs`` the ``"key": value`` part.
    """

    name: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()


_WHITESPACE = " \t\r\n"
_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_REST = _IDENT_START | set("0123456789")
_NUMBER_START = set("0123456789+-.")


class _Parser:
    """Recursive-descent parser over a single value literal."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    # -- lexing helpers ---------------------------------------------------

    def skip_ws(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in _WHITESPACE:
            self.pos += 1

    def peek(self) -> str:
        return self.text[self.pos] if self.pos < len(self.text) else ""

    def expect(self, char: str) -> None:
        if self.peek() != char:
            raise GDParseError(
                f"expected {char!r} at offset {self.pos} in {self.text!r}, got {self.peek()!r}"
            )
        self.pos += 1

    # -- grammar ----------------------------------------------------------

    def parse(self) -> Any:
        self.skip_ws()
        value = self.parse_value()
        self.skip_ws()
        if self.pos != len(self.text):
            raise GDParseError(f"trailing text at offset {self.pos} in {self.text!r}")
        return value

    def parse_value(self) -> Any:
        self.skip_ws()
        char = self.peek()
        if char == "":
            raise GDParseError(f"unexpected end of input in {self.text!r}")
        if char == '"':
            return self.parse_string()
        if char == "&":
            self.pos += 1
            return GDStringName(self.parse_string())
        if char == "^":
            self.pos += 1
            return GDNodePath(self.parse_string())
        if char == "[":
            return self.parse_array()
        if char == "{":
            return self.parse_dict()
        if char in _IDENT_START:
            return self.parse_identifier_like()
        if char in _NUMBER_START:
            return self.parse_number()
        raise GDParseError(f"unexpected {char!r} at offset {self.pos} in {self.text!r}")

    def parse_string(self) -> str:
        self.expect('"')
        out: list[str] = []
        while True:
            if self.pos >= len(self.text):
                raise GDParseError(f"unterminated string in {self.text!r}")
            char = self.text[self.pos]
            if char == "\\":
                self.pos += 1
                if self.pos >= len(self.text):
                    raise GDParseError(f"unterminated escape in {self.text!r}")
                esc = self.text[self.pos]
                self.pos += 1
                if esc == "u":
                    hex_digits = self.text[self.pos : self.pos + 4]
                    if len(hex_digits) != 4:
                        raise GDParseError(f"truncated \\u escape in {self.text!r}")
                    self.pos += 4
                    out.append(chr(int(hex_digits, 16)))
                else:
                    out.append(_STRING_UNESCAPES.get(esc, esc))
                continue
            if char == '"':
                self.pos += 1
                return "".join(out)
            out.append(char)
            self.pos += 1

    def parse_array(self) -> list[Any]:
        self.expect("[")
        items: list[Any] = []
        self.skip_ws()
        if self.peek() == "]":
            self.pos += 1
            return items
        while True:
            items.append(self.parse_value())
            self.skip_ws()
            char = self.peek()
            if char == ",":
                self.pos += 1
                self.skip_ws()
                # Godot tolerates a trailing comma before the closing bracket.
                if self.peek() == "]":
                    self.pos += 1
                    return items
                continue
            if char == "]":
                self.pos += 1
                return items
            raise GDParseError(f"expected ',' or ']' at offset {self.pos} in {self.text!r}")

    def parse_dict(self) -> dict[Any, Any]:
        self.expect("{")
        out: dict[Any, Any] = {}
        self.skip_ws()
        if self.peek() == "}":
            self.pos += 1
            return out
        while True:
            key = self.parse_value()
            self.skip_ws()
            self.expect(":")
            value = self.parse_value()
            out[key] = value
            self.skip_ws()
            char = self.peek()
            if char == ",":
                self.pos += 1
                self.skip_ws()
                if self.peek() == "}":
                    self.pos += 1
                    return out
                continue
            if char == "}":
                self.pos += 1
                return out
            raise GDParseError(f"expected ',' or '}}' at offset {self.pos} in {self.text!r}")

    def parse_identifier_like(self) -> Any:
        start = self.pos
        while self.pos < len(self.text) and self.text[self.pos] in _IDENT_REST:
            self.pos += 1
        name = self.text[start : self.pos]
        self.skip_ws()
        if self.peek() != "(":
            literal = _BARE_LITERALS.get(name, _MISSING)
            if literal is not _MISSING:
                return literal
            # A bare identifier: an enum name or a type used as an argument,
            # e.g. the first argument of Object(InputEventKey, ...).
            return GDCall(name)
        return self.parse_call(name)

    def parse_call(self, name: str) -> GDCall:
        self.expect("(")
        args: list[Any] = []
        kwargs: list[tuple[str, Any]] = []
        self.skip_ws()
        if self.peek() == ")":
            self.pos += 1
            return GDCall(name)
        while True:
            self.skip_ws()
            entry = self.parse_value()
            self.skip_ws()
            if self.peek() == ":":
                self.pos += 1
                if not isinstance(entry, str):
                    raise GDParseError(f"non-string key in {name}(...) in {self.text!r}")
                kwargs.append((entry, self.parse_value()))
            else:
                args.append(entry)
            self.skip_ws()
            char = self.peek()
            if char == ",":
                self.pos += 1
                self.skip_ws()
                if self.peek() == ")":
                    self.pos += 1
                    break
                continue
            if char == ")":
                self.pos += 1
                break
            raise GDParseError(f"expected ',' or ')' at offset {self.pos} in {self.text!r}")
        return GDCall(name, tuple(args), tuple(kwargs))

    def parse_number(self) -> int | float:
        start = self.pos
        if self.peek() in "+-":
            self.pos += 1
        # inf/nan carry a sign, so they are only reachable from here.
        rest = self.text[self.pos :]
        for word, value in (("inf", math.inf), ("nan", math.nan)):
            if rest.startswith(word):
                self.pos += len(word)
                # Godot writes "inf_neg" in some builds; plain "-inf" is normal.
                if self.text[start] == "-":
                    return -value
                return value
        is_float = False
        while self.pos < len(self.text):
            char = self.text[self.pos]
            if char.isdigit():
                self.pos += 1
            elif char in ".eE":
                is_float = True
                self.pos += 1
            elif char in "+-" and self.text[self.pos - 1] in "eE":
                self.pos += 1
            else:
                break
        raw = self.text[start : self.pos]
        if raw in ("", "+", "-"):
            raise GDParseError(f"malformed number at offset {start} in {self.text!r}")
        return float(raw) if is_float else int(raw)


class _Missing:
    __slots__ = ()


_MISSING = _Missing()

_BARE_LITERALS: dict[str, Any] = {
    "true": True,
    "false": False,
    "null": None,
    "nan": math.nan,
    "inf": math.inf,
}


_STRING_ESCAPES = {
    "\\": "\\\\",
    '"': '\\"',
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
_STRING_UNESCAPES = {
    "n": "\n",
    "r": "\r",
    "t": "\t",
    "b": "\b",
    "f": "\f",
    "a": "\a",
    "v": "\v",
    "\\": "\\",
    '"': '"',
    "'": "'",
}


def parse_value(text: str) -> Any:
    """Parse a single Godot variant literal.

    Raises:
        GDParseError: if ``text`` is not a well-formed literal.
    """
    return _Parser(text).parse()


def _dump_string(value: str) -> str:
    out = [_STRING_ESCAPES.get(char, char) for char in value]
    return '"' + "".join(out) + '"'


def _dump_float(value: float) -> str:
    if math.isnan(value):
        return "nan"
    if math.isinf(value):
        return "inf" if value > 0 else "-inf"
    # Godot writes whole floats with a trailing ".0"; repr() already does.
    return repr(value)


def dumps(value: Any) -> str:
    """Serialize a value back to Godot's text form."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, GDStringName):
        return "&" + _dump_string(value.value)
    if isinstance(value, GDNodePath):
        return "^" + _dump_string(value.value)
    if isinstance(value, GDCall):
        if not value.args and not value.kwargs:
            return value.name
        parts = [dumps(arg) for arg in value.args]
        parts += [f"{_dump_string(key)}: {dumps(val)}" for key, val in value.kwargs]
        return f"{value.name}({', '.join(parts)})"
    if isinstance(value, str):
        return _dump_string(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return _dump_float(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(dumps(item) for item in value) + "]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        body = ", ".join(f"{dumps(key)}: {dumps(val)}" for key, val in value.items())
        return "{" + body + "}"
    raise TypeError(f"cannot serialize {type(value).__name__} as a Godot value")
