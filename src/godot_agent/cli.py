"""The ``godot-agent`` command line.

Four jobs: check the toolchain (``doctor``), run the agent headlessly for CI
and evals (``run``), install the dcode surfaces (``install-dcode``), and serve
the lifecycle hooks dcode invokes (``hook``).
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from godot_agent import __version__

app = typer.Typer(
    name="godot-agent",
    help="A Godot 4 game-development agent, usable from dcode or headlessly.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

#: Where dcode keeps its configuration and agent profiles.
DEEPAGENTS_HOME = Path.home() / ".deepagents"

#: The agent profile name this package installs.
PROFILE_NAME = "godot"

_PACKAGE_ROOT = Path(__file__).resolve().parent.parent.parent


def _resource(*parts: str) -> Path:
    """Resolve a path inside the repository checkout."""
    return _PACKAGE_ROOT.joinpath(*parts)


@app.command()
def version() -> None:
    """Print the installed version."""
    console.print(f"godot-agent {__version__}")


@app.command()
def doctor(
    project: Annotated[
        str | None, typer.Option(help="Godot project directory to inspect.")
    ] = None,
) -> None:
    """Check that Godot, the project and the tool surfaces are usable."""
    from godot_agent.config import Settings, set_settings
    from godot_agent.tools.project import godot_doctor
    from godot_agent.tools.registry import tool_names

    if project:
        set_settings(Settings(project_path=Path(project)))

    report = godot_doctor.invoke({})
    healthy = bool(report.get("ok"))

    # Plain ASCII: this runs in cp1252 consoles on Windows, where a check mark
    # raises UnicodeEncodeError rather than printing.
    console.print("[green]OK[/green]" if healthy else "[red]PROBLEM[/red]")
    for key, value in report.items():
        if key == "ok":
            continue
        console.print(f"  {key}: {value}")
    console.print(f"  tools: {len(tool_names())} registered")

    if not healthy:
        raise typer.Exit(1)


@app.command(name="hook")
def hook_command(
    name: Annotated[str, typer.Argument(help="Hook name: session-start, guard-write, validate.")],
) -> None:
    """Handle one dcode lifecycle hook. Reads its event as JSON on stdin."""
    from godot_agent.hooks import run_hook

    raise typer.Exit(run_hook(name))


@app.command()
def mcp(
    project: Annotated[
        str | None, typer.Option(help="Godot project directory to serve.")
    ] = None,
) -> None:
    """Serve the Godot tools over MCP on stdio."""
    from godot_agent_mcp.server import main as mcp_main

    raise typer.Exit(mcp_main(["--project", project] if project else []))


@app.command(name="install-dcode")
def install_dcode(
    set_default: Annotated[
        bool,
        typer.Option(help="Point dcode's [startup] agent at the installed profile."),
    ] = False,
    force: Annotated[bool, typer.Option(help="Overwrite an existing profile.")] = False,
) -> None:
    """Install the Godot agent profile into ``~/.deepagents/godot/``.

    Installs the profile (AGENTS.md plus subagents and skills) that dcode loads
    when it starts. The plugin half -- MCP tools, hooks and skills -- is
    installed separately with dcode's own plugin commands; this prints those.
    """
    source = _resource("dcode-profile")
    if not source.is_dir():
        console.print(f"[red]Profile source not found at {source}[/red]")
        raise typer.Exit(1)

    target = DEEPAGENTS_HOME / PROFILE_NAME
    if target.exists() and not force:
        console.print(
            f"[yellow]{target} already exists. Re-run with --force to replace it.[/yellow]"
        )
        raise typer.Exit(1)

    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)

    skills_source = _resource("plugin", "skills")
    if skills_source.is_dir():
        shutil.copytree(skills_source, target / "skills", dirs_exist_ok=True)

    console.print(f"[green]Installed profile to {target}[/green]")

    if set_default:
        _set_startup_agent()

    console.print("\nInstall the plugin (tools, hooks, skills) with:")
    console.print(f"  dcode plugin marketplace add {_PACKAGE_ROOT}")
    console.print("  dcode plugin install godot-agent@godot-agent")
    if not set_default:
        console.print(f"\nThen select the profile with: [startup] agent = \"{PROFILE_NAME}\"")
        console.print(f"in {DEEPAGENTS_HOME / 'config.toml'}, or re-run with --set-default.")


def _set_startup_agent() -> None:
    """Point dcode's ``[startup] agent`` at our profile, preserving other keys."""
    config_path = DEEPAGENTS_HOME / "config.toml"
    existing = config_path.read_text(encoding="utf-8") if config_path.is_file() else ""

    if f'agent = "{PROFILE_NAME}"' in existing:
        console.print(f"[dim]{config_path} already selects the {PROFILE_NAME} agent.[/dim]")
        return
    if "[startup]" in existing:
        console.print(
            f"[yellow]{config_path} already has a [startup] section; "
            f'set agent = "{PROFILE_NAME}" there by hand so nothing else is lost.[/yellow]'
        )
        return

    config_path.parent.mkdir(parents=True, exist_ok=True)
    separator = "\n\n" if existing.strip() else ""
    config_path.write_text(
        f'{existing.rstrip()}{separator}[startup]\nagent = "{PROFILE_NAME}"\n',
        encoding="utf-8",
    )
    console.print(f"[green]Set [startup] agent = \"{PROFILE_NAME}\" in {config_path}[/green]")


@app.command(name="sync-profile")
def sync_profile() -> None:
    """Regenerate dcode-profile/ from the prompts in godot_agent.prompts.

    The profile is checked in so dcode can install it without Python, but the
    prompts are the source of truth. Run this after editing them.
    """
    from godot_agent.profile import write_profile

    written = write_profile(_resource("dcode-profile"))
    for path in written:
        console.print(f"  {path.relative_to(_PACKAGE_ROOT)}")
    console.print(f"[green]Wrote {len(written)} profile files[/green]")


@app.command()
def run(
    task: Annotated[str, typer.Argument(help="What the agent should build.")],
    project: Annotated[
        str | None, typer.Option(help="Godot project directory to work in.")
    ] = None,
    model: Annotated[str | None, typer.Option(help="Model id, e.g. claude-sonnet-5.")] = None,
    max_steps: Annotated[int, typer.Option(help="Cap on agent steps.")] = 200,
    output: Annotated[
        str | None, typer.Option(help="Write the final transcript to this JSON file.")
    ] = None,
) -> None:
    """Run the agent headlessly on one task. Intended for CI and evals."""
    from godot_agent.agent import create_godot_agent

    agent = create_godot_agent(model=model, project_path=project)
    console.print(f"[bold]Task:[/bold] {task}")

    result = agent.invoke(
        {"messages": [{"role": "user", "content": task}]},
        config={"recursion_limit": max_steps},
    )

    messages = result.get("messages", [])
    final = messages[-1].content if messages else ""
    console.print(final if isinstance(final, str) else json.dumps(final, indent=2))

    if output:
        Path(output).write_text(
            json.dumps(
                {"task": task, "messages": [m.model_dump() for m in messages]},
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        console.print(f"[dim]Transcript written to {output}[/dim]")


def main() -> None:
    """Console-script entry point."""
    try:
        app()
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        sys.exit(130)


if __name__ == "__main__":  # pragma: no cover
    main()
