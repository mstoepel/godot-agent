"""Reader and writer for Godot's ``ConfigFile`` text format.

Covers ``project.godot``, ``*.import`` sidecars and ``export_presets.cfg``.
The format is INI-like but not INI: keys contain ``/``, comments start with
``;``, and a value may span several lines when it is an array or dictionary.
``configparser`` mishandles all three, so this module parses it directly.

Values are kept as raw source text. Call
:func:`godot_agent.gdformat.values.parse_value` when a value must be inspected
structurally -- keeping the text means an untouched file round-trips exactly.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from godot_agent.gdformat.values import GDParseError, parse_value

__all__ = ["GodotConfig", "parse_config", "read_config"]

#: Name of the implicit section holding keys that appear before any ``[header]``.
ROOT_SECTION = ""


def _closes_value(text: str) -> bool:
    """Return True when ``text`` has balanced brackets outside of strings.

    Used to decide whether a value continues onto the next line.
    """
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


@dataclass(slots=True)
class GodotConfig:
    """A parsed Godot config file.

    Attributes:
        sections: Section name to ``{key: raw value text}``, in file order.
            Keys written before any header live under :data:`ROOT_SECTION`.
    """

    sections: dict[str, dict[str, str]] = field(default_factory=dict)

    def get(self, section: str, key: str, default: str | None = None) -> str | None:
        """Return the raw text of one key, or ``default`` when absent."""
        return self.sections.get(section, {}).get(key, default)

    def get_value(self, section: str, key: str, default: object = None) -> object:
        """Return one key parsed into a Python value, or ``default``.

        A value that fails to parse is returned as its raw text rather than
        raising, so a single exotic entry cannot break project inspection.
        """
        raw = self.get(section, key)
        if raw is None:
            return default
        try:
            return parse_value(raw)
        except GDParseError:
            return raw

    def set(self, section: str, key: str, raw_value: str) -> None:
        """Set one key to a raw value text, creating the section if needed."""
        self.sections.setdefault(section, {})[key] = raw_value

    def items(self, section: str) -> Iterator[tuple[str, str]]:
        """Iterate ``(key, raw value)`` pairs of one section in file order."""
        yield from self.sections.get(section, {}).items()

    def dumps(self) -> str:
        """Serialize back to Godot's config text form."""
        out: list[str] = []
        for name, entries in self.sections.items():
            if name != ROOT_SECTION:
                if out:
                    out.append("")
                out.append(f"[{name}]")
                out.append("")
            for key, value in entries.items():
                out.append(f"{key}={value}")
        return "\n".join(out) + "\n"


def parse_config(text: str) -> GodotConfig:
    """Parse Godot config text.

    Unparseable lines are skipped rather than raising: these files are written
    by the engine, and a forward-compatible reader is worth more here than a
    strict one.
    """
    config = GodotConfig()
    section = ROOT_SECTION
    config.sections[section] = {}

    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        index += 1
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            config.sections.setdefault(section, {})
            continue
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        while not _closes_value(value) and index < len(lines):
            value = value + "\n" + lines[index]
            index += 1
        config.sections[section][key] = value

    if not config.sections[ROOT_SECTION]:
        del config.sections[ROOT_SECTION]
    return config


def read_config(path: Path | str) -> GodotConfig:
    """Read and parse a Godot config file from disk (UTF-8)."""
    return parse_config(Path(path).read_text(encoding="utf-8"))
