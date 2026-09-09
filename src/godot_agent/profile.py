"""Generate the dcode agent profile from the shared prompts.

The profile under ``dcode-profile/`` is checked in so dcode can install it
without running Python, but it is *generated* from :mod:`godot_agent.prompts`.
Keeping one source means the dcode session and the library agent cannot end up
following different instructions -- a drift that would be invisible until the
two behaved differently on the same task.

``tests/test_profile.py`` fails if the checked-in files fall out of sync.
"""

from __future__ import annotations

from pathlib import Path

from godot_agent.agent import SUBAGENT_DESCRIPTIONS
from godot_agent.prompts import MAIN_PROMPT, SUBAGENT_PROMPTS

__all__ = ["profile_files", "write_profile"]


def _front_matter(name: str) -> str:
    description = " ".join(SUBAGENT_DESCRIPTIONS[name].split())
    return f"---\nname: {name}\ndescription: {description}\n---\n\n"


def profile_files() -> dict[str, str]:
    """The profile as ``relative path -> content``.

    Paths are POSIX-style and relative to the profile root.
    """
    files = {"AGENTS.md": f"# Godot game developer\n\n{MAIN_PROMPT}"}
    for name, prompt in SUBAGENT_PROMPTS.items():
        files[f"agents/{name}/AGENTS.md"] = f"{_front_matter(name)}{prompt}"
    return files


def write_profile(root: Path | str) -> list[Path]:
    """Write the profile under ``root``, returning the paths written."""
    base = Path(root)
    written: list[Path] = []
    for relative, content in profile_files().items():
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="\n")
        written.append(target)
    return written
