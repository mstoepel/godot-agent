"""Drive the Godot executable and return structured results.

Every entry point here is bounded and non-interactive. An agent that can start
an unbounded Godot process will eventually hang a session, so runs take a frame
budget or a timeout, and the editor is never launched in a mode that waits for
a human.

Results are deliberately not raw text: :class:`GodotResult.to_model` returns a
compact dict with parsed diagnostics and a truncated tail, because handing a
model 4,000 lines of engine chatter buries the one line that matters.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from godot_agent.config import get_settings
from godot_agent.engine.discovery import find_godot, find_project_root
from godot_agent.engine.errors import Diagnostic, parse_output, summarize

__all__ = [
    "GodotResult",
    "export_project",
    "import_project",
    "run_godot",
    "run_project",
    "run_script",
]

#: How many trailing output lines to keep when reporting to the model.
DEFAULT_TAIL_LINES = 40

#: Frame budget applied to a play session when the caller does not set one.
DEFAULT_QUIT_AFTER = 600


@dataclass(slots=True)
class GodotResult:
    """The outcome of one Godot invocation."""

    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    diagnostics: list[Diagnostic] = field(default_factory=list)

    @property
    def errors(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.severity == "warning"]

    @property
    def ok(self) -> bool:
        """True when the process succeeded and reported no errors.

        The exit code alone is not enough: Godot exits 0 after printing script
        errors in plenty of situations, so a green exit code with errors on
        stderr must not read as success.
        """
        return self.exit_code == 0 and not self.timed_out and not self.errors

    def tail(self, lines: int = DEFAULT_TAIL_LINES) -> str:
        """The last ``lines`` lines of combined output."""
        combined = (self.stdout + "\n" + self.stderr).strip().splitlines()
        return "\n".join(combined[-lines:])

    def to_model(self, tail_lines: int = DEFAULT_TAIL_LINES) -> dict[str, Any]:
        """Render for a tool response: verdict, diagnostics, bounded tail."""
        return {
            "ok": self.ok,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 2),
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "diagnostics": summarize(self.diagnostics),
            "output_tail": self.tail(tail_lines),
        }


def _as_text(stream: bytes | str | None) -> str:
    """Normalize a captured stream, which is bytes on a timeout."""
    if stream is None:
        return ""
    return stream.decode(errors="replace") if isinstance(stream, bytes) else stream


def run_godot(
    args: list[str],
    *,
    project: Path | str | None = None,
    timeout: float | None = None,
    binary: Path | str | None = None,
    cwd: Path | str | None = None,
) -> GodotResult:
    """Run Godot with ``args`` and parse its output.

    Args:
        args: Arguments after the executable. ``--path`` is added automatically
            when ``project`` resolves.
        project: Project directory; defaults to the configured project.
        timeout: Seconds before the process is killed. Defaults to the
            configured timeout.
        binary: Godot executable; defaults to the discovered one.
        cwd: Working directory for the subprocess.

    Raises:
        GodotNotFoundError: if no Godot executable is available.
    """
    settings = get_settings()
    executable = Path(binary) if binary else find_godot()
    command = [str(executable)]

    if project is not None or settings.project_path is not None:
        try:
            root = find_project_root(project)
            command += ["--path", str(root)]
        except Exception:
            # A command such as --version needs no project; let it run.
            pass

    command += args
    limit = timeout if timeout is not None else settings.timeout_s

    started = time.monotonic()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=limit,
            check=False,
            cwd=str(cwd) if cwd else None,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as expired:
        timed_out = True
        stdout = _as_text(expired.stdout)
        stderr = _as_text(expired.stderr)
        exit_code = -1

    duration = time.monotonic() - started
    return GodotResult(
        command=command,
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        duration_s=duration,
        timed_out=timed_out,
        diagnostics=parse_output(stdout + "\n" + stderr),
    )


def import_project(
    project: Path | str | None = None,
    *,
    timeout: float | None = None,
) -> GodotResult:
    """Import or reimport every asset, without opening the editor.

    Must run after any new file lands in the project: until an asset is
    imported it has no ``.import`` sidecar and no UID, and every scene that
    references it fails to load.
    """
    return run_godot(["--headless", "--import"], project=project, timeout=timeout)


def run_project(
    project: Path | str | None = None,
    *,
    scene: str | None = None,
    quit_after: int | None = DEFAULT_QUIT_AFTER,
    headless: bool = True,
    write_movie: Path | str | None = None,
    extra_args: list[str] | None = None,
    timeout: float | None = None,
) -> GodotResult:
    """Play the project (or one scene) for a bounded number of frames.

    Args:
        scene: A ``res://`` scene path; defaults to the project's main scene.
        quit_after: Frame budget. ``None`` removes it, which is only safe when
            the scene quits on its own.
        headless: Run without a window. Turn this off only when something must
            actually be rendered to the screen.
        write_movie: Record to this path; forces deterministic fixed-step
            timing, which is what makes a playtest reproducible.
    """
    args: list[str] = []
    if headless:
        args.append("--headless")
    if quit_after is not None:
        args += ["--quit-after", str(quit_after)]
    if write_movie is not None:
        args += ["--write-movie", str(write_movie)]
    if scene:
        args.append(scene)
    args += extra_args or []
    return run_godot(args, project=project, timeout=timeout)


def run_script(
    script: str,
    project: Path | str | None = None,
    *,
    script_args: list[str] | None = None,
    timeout: float | None = None,
) -> GodotResult:
    """Run a GDScript file inside the project and quit.

    ``script`` is a ``res://`` path to a script extending ``SceneTree`` or
    ``MainLoop``. This is how tooling that needs engine APIs -- resource
    inspection, scene instantiation, import settings -- runs without an editor.
    """
    args = ["--headless", "--script", script]
    if script_args:
        args += ["--", *script_args]
    return run_godot(args, project=project, timeout=timeout)


def export_project(
    preset: str,
    output: Path | str,
    project: Path | str | None = None,
    *,
    debug: bool = False,
    pack_only: bool = False,
    timeout: float | None = None,
) -> GodotResult:
    """Export the project using a preset from ``export_presets.cfg``.

    Requires matching export templates to be installed; the error Godot emits
    when they are missing is passed through as a diagnostic.
    """
    if pack_only:  # noqa: SIM108 - a nested ternary over three modes reads worse
        flag = "--export-pack"
    else:
        flag = "--export-debug" if debug else "--export-release"

    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    return run_godot(
        ["--headless", flag, preset, str(destination)],
        project=project,
        timeout=timeout,
    )
