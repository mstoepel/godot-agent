"""Tools for generating art and getting it into the project.

Every generated asset goes through the importer -- nothing is written into
``res://`` raw. That is the difference between an asset the engine can load and
one that breaks every scene referencing it.

The tool descriptions carry the prompting rules that decide output quality
(state a contrasting background, never write "pixel art" into a pixel-art
prompt, generate at true resolution). Those belong here rather than in a skill
because they change how the *arguments* are filled in, and the model reads this
at the moment it fills them.
"""

from __future__ import annotations

from typing import Annotated, Any

from langchain_core.tools import tool

from godot_agent.assets.base import AssetProviderError, ImageRequest, ModelRequest
from godot_agent.assets.importer import import_asset
from godot_agent.assets.registry import (
    available_providers,
    get_budget,
    resolve_provider,
)
from godot_agent.tools._common import ProjectArg, failure, resolve_project, tool_errors

__all__ = [
    "godot_generate_image",
    "godot_generate_model",
    "godot_generate_pixel_art",
    "godot_list_asset_providers",
]

DestArg = Annotated[
    str,
    "Destination res:// path, e.g. res://assets/sprites/player.png. "
    "Directories are created as needed.",
]


def _generate_and_import(
    kind: str,
    request: ImageRequest | ModelRequest,
    dest: str,
    project: str | None,
    provider_name: str | None,
    pixel_art: bool,
) -> dict[str, Any]:
    """Shared path: price, check the budget, generate, import, report."""
    root = resolve_project(project)
    provider = resolve_provider(kind, provider_name)
    budget = get_budget()

    description = f"{provider.info.name} {kind} generation"
    try:
        estimate = provider.estimate_cost(request)
    except AssetProviderError:
        # A provider without a dry run should not block the request; the real
        # charge is still recorded below.
        estimate = 0.0
    budget.check(estimate, description)

    assets = provider.generate(request)
    if not assets:
        return failure("the provider returned no assets")

    results = []
    for index, asset in enumerate(assets):
        target = dest if len(assets) == 1 else _numbered(dest, index)
        outcome = import_asset(root, target, asset, pixel_art=pixel_art)
        budget.record(asset.cost_usd, description)
        results.append(
            {
                "res_path": outcome.res_path,
                "imported": outcome.imported,
                "errors": outcome.errors,
            }
        )

    spent = sum(asset.cost_usd for asset in assets)
    failed = [entry for entry in results if not entry["imported"]]
    return {
        "ok": not failed,
        "provider": provider.info.name,
        "assets": results,
        "cost_usd": round(spent, 4),
        "session_spend_usd": round(budget.spent_usd, 4),
        "budget_remaining_usd": round(budget.remaining_usd, 4),
        **(
            {"error": "Some assets failed to import; see per-asset errors."}
            if failed
            else {}
        ),
    }


def _numbered(dest: str, index: int) -> str:
    """Turn ``res://a/b.png`` into ``res://a/b_1.png`` for a batch."""
    head, _, tail = dest.rpartition(".")
    return f"{head}_{index + 1}.{tail}" if head else f"{dest}_{index + 1}"


@tool
@tool_errors
def godot_list_asset_providers() -> dict[str, Any]:
    """List the asset providers and which are usable right now.

    Check this before generating if you are unsure what is available. The
    'placeholder' provider always works, costs nothing and renders locally --
    use it to build and play a game before real art exists.
    """
    budget = get_budget()
    return {
        "ok": True,
        "providers": [
            {
                "name": info.name,
                "kinds": list(info.kinds),
                "configured": info.configured,
                "env_var": info.env_var,
                "description": info.description,
            }
            for info in available_providers()
        ],
        "budget_limit_usd": budget.limit_usd,
        "budget_remaining_usd": round(budget.remaining_usd, 4),
    }


@tool
@tool_errors
def godot_generate_pixel_art(
    prompt: Annotated[
        str,
        "Describe the SUBJECT only -- never write 'pixel art' or name a style; "
        "the style argument handles that. State a background colour that "
        "contrasts the subject, e.g. 'a knight with a red plume on a plain "
        "white background'. Leaving the background unstated produces muddy "
        "grey that ruins background removal.",
    ],
    dest: DestArg,
    width: Annotated[int, "True pixel width. Sprites 16-48, icons 32-64, tiles 16-32."] = 32,
    height: Annotated[int, "True pixel height."] = 32,
    count: Annotated[int, "How many variations to generate."] = 1,
    transparent: Annotated[bool, "Cut the background out. Use for any standalone asset."] = True,
    style: Annotated[str | None, "Provider style id. Omit for the default."] = None,
    seed: Annotated[int | None, "Seed for a reproducible result."] = None,
    provider: Annotated[
        str | None, "Force a provider, e.g. 'placeholder' for free local art."
    ] = None,
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Generate grid-aligned pixel art and import it into the project.

    Generate at the game's TRUE pixel size. Generating large and scaling down
    destroys the grid alignment that makes pixel art read as pixel art. Sets
    nearest-neighbour filtering and disables mipmaps on import so the result
    stays crisp.
    """
    return _generate_and_import(
        "pixel",
        ImageRequest(
            prompt=prompt,
            width=width,
            height=height,
            count=count,
            style=style,
            transparent=transparent,
            seed=seed,
        ),
        dest,
        project,
        provider,
        pixel_art=True,
    )


@tool
@tool_errors
def godot_generate_image(
    prompt: Annotated[
        str,
        "What to draw. State the background explicitly. Include the art "
        "direction from assets/STYLE.md so separate assets look like one game.",
    ],
    dest: DestArg,
    width: Annotated[int, "Target width in pixels."] = 512,
    height: Annotated[int, "Target height in pixels."] = 512,
    count: Annotated[int, "How many variations to generate."] = 1,
    transparent: Annotated[bool, "Request a transparent background."] = False,
    provider: Annotated[str | None, "Force a provider."] = None,
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Generate a general 2D image (concept art, UI, textures) and import it.

    For sprites and tiles in a pixel-art game use `godot_generate_pixel_art`
    instead -- this produces pixel-*styled* output rather than true grid-aligned
    pixels.
    """
    return _generate_and_import(
        "image",
        ImageRequest(
            prompt=prompt,
            width=width,
            height=height,
            count=count,
            transparent=transparent,
        ),
        dest,
        project,
        provider,
        pixel_art=False,
    )


@tool
@tool_errors
def godot_generate_model(
    prompt: Annotated[str, "Describe the object. Max ~800 characters."],
    dest: Annotated[str, "Destination res:// path ending in .glb"],
    textured: Annotated[
        bool,
        "Generate PBR textures. Roughly doubles both cost and time; turn it off "
        "for blockout geometry.",
    ] = True,
    target_polycount: Annotated[int, "Target triangle count."] = 30000,
    provider: Annotated[str | None, "Force a provider."] = None,
    project: ProjectArg = None,
) -> dict[str, Any]:
    """Generate a 3D model as GLB and import it into the project.

    Slow and paid -- a textured model takes minutes and costs real money, so
    check `godot_list_asset_providers` for the remaining budget first. Godot
    imports GLB as a scene; you still need to add collision shapes yourself.
    """
    return _generate_and_import(
        "model3d",
        ModelRequest(
            prompt=prompt,
            textured=textured,
            target_polycount=target_polycount,
        ),
        dest,
        project,
        provider,
        pixel_art=False,
    )
