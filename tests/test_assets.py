"""Tests for asset generation, budgeting and import.

The placeholder provider makes the whole generate-import-reference path
testable with no API key and no network, which is exactly why it exists.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from godot_agent.assets import (
    BudgetExceededError,
    GeneratedAsset,
    ImageRequest,
    ModelRequest,
    SessionBudget,
    resolve_provider,
    write_asset,
)
from godot_agent.assets.base import NotConfiguredError
from godot_agent.assets.importer import PIXEL_ART_PARAMS, _patch_import_params
from godot_agent.assets.registry import available_providers, reset_budget
from godot_agent.config import Settings, reset_settings, set_settings
from godot_agent.gdformat.config import parse_config
from godot_agent.tools.assets import godot_generate_pixel_art, godot_list_asset_providers

FIXTURES = Path(__file__).parent / "fixtures" / "scenes"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """No ambient API keys, and a fresh budget per test."""
    for info in available_providers():
        if info.env_var:
            monkeypatch.delenv(info.env_var, raising=False)
    set_settings(Settings(godot_bin=None, project_path=None))
    reset_budget()
    yield
    reset_settings()
    reset_budget()


@pytest.fixture
def project(tmp_path: Path) -> str:
    root = tmp_path / "game"
    root.mkdir()
    shutil.copy(FIXTURES / "project.godot", root / "project.godot")
    return str(root)


# -- provider resolution --------------------------------------------------


def test_falls_back_to_placeholder_when_nothing_is_configured() -> None:
    """A project with no API keys must still be able to make art."""
    assert resolve_provider("image").info.name == "placeholder"
    assert resolve_provider("pixel").info.name == "placeholder"


def test_explicit_provider_wins() -> None:
    assert resolve_provider("pixel", "placeholder").info.name == "placeholder"


def test_unknown_provider_is_named() -> None:
    with pytest.raises(Exception, match="unknown asset provider"):
        resolve_provider("image", "nope")


def test_3d_without_a_key_explains_what_to_set() -> None:
    """There is no free fallback for 3D, so the error must be actionable."""
    with pytest.raises(Exception, match="MESHY_API_KEY"):
        resolve_provider("model3d")


def test_remote_provider_reports_its_missing_key() -> None:
    provider = resolve_provider("pixel", "retrodiffusion")
    with pytest.raises(NotConfiguredError) as caught:
        provider.generate(ImageRequest(prompt="x"))
    assert caught.value.env_var == "RETRODIFFUSION_API_KEY"


# -- placeholder generation -----------------------------------------------


def test_placeholder_renders_a_real_png_at_the_requested_size() -> None:
    from PIL import Image

    (asset,) = resolve_provider("image", "placeholder").generate(
        ImageRequest(prompt="a knight", width=48, height=24)
    )
    assert asset.suffix == ".png"
    assert asset.cost_usd == 0.0
    with Image.open(__import__("io").BytesIO(asset.data)) as image:
        assert image.size == (48, 24)


def test_placeholder_is_deterministic_per_prompt() -> None:
    """Stable colours per subject keep a placeholder-art game readable."""
    provider = resolve_provider("image", "placeholder")
    first = provider.generate(ImageRequest(prompt="a knight"))[0].data
    second = provider.generate(ImageRequest(prompt="a knight"))[0].data
    other = provider.generate(ImageRequest(prompt="a goblin"))[0].data
    assert first == second
    assert first != other


def test_placeholder_generates_a_batch() -> None:
    assets = resolve_provider("image", "placeholder").generate(
        ImageRequest(prompt="a coin", count=3)
    )
    assert len(assets) == 3
    assert len({asset.data for asset in assets}) == 3


def test_placeholder_refuses_3d() -> None:
    with pytest.raises(Exception, match="only generates images"):
        resolve_provider("image", "placeholder").generate(ModelRequest(prompt="a chair"))


# -- budget ---------------------------------------------------------------


def test_budget_allows_spend_under_the_limit() -> None:
    budget = SessionBudget(limit_usd=1.0)
    budget.check(0.5, "test")
    budget.record(0.5, "test")
    assert budget.remaining_usd == 0.5


def test_budget_refuses_spend_over_the_limit() -> None:
    budget = SessionBudget(limit_usd=1.0, spent_usd=0.9)
    with pytest.raises(BudgetExceededError, match=r"\$1.00 limit"):
        budget.check(0.5, "a big generation")


def test_budget_error_names_the_free_alternative() -> None:
    budget = SessionBudget(limit_usd=0.0)
    with pytest.raises(BudgetExceededError, match="placeholder"):
        budget.check(1.0, "x")


def test_budget_ignores_free_generation() -> None:
    """The placeholder provider must never be blocked by the budget."""
    budget = SessionBudget(limit_usd=0.0)
    budget.check(0.0, "placeholder")
    budget.record(0.0, "placeholder")
    assert budget.spent_usd == 0.0


# -- writing and import settings ------------------------------------------


def test_write_asset_lands_inside_the_project(project: str) -> None:
    asset = GeneratedAsset(data=b"\x89PNG", suffix=".png", provider="test")
    path = write_asset(Path(project), "res://assets/sprites/x.png", asset)
    assert path.read_bytes() == b"\x89PNG"
    assert path.relative_to(project).as_posix() == "assets/sprites/x.png"


def test_write_asset_refuses_to_escape_the_project(project: str) -> None:
    asset = GeneratedAsset(data=b"x", suffix=".png", provider="test")
    with pytest.raises(ValueError, match="outside the project root"):
        write_asset(Path(project), "res://../../escape.png", asset)


def test_patches_import_params_without_losing_engine_fields(tmp_path: Path) -> None:
    """The .import file carries cache paths we cannot reconstruct; keep them."""
    sidecar = tmp_path / "player.png.import"
    sidecar.write_text(
        "[remap]\n\n"
        'importer="texture"\n'
        'type="CompressedTexture2D"\n'
        'uid="uid://abc123"\n'
        'path="res://.godot/imported/player.png-9f8e.ctex"\n\n'
        "[params]\n\n"
        "compress/mode=2\n"
        "mipmaps/generate=true\n",
        encoding="utf-8",
    )

    assert _patch_import_params(sidecar, PIXEL_ART_PARAMS) is True

    config = parse_config(sidecar.read_text(encoding="utf-8"))
    assert config.get("params", "compress/mode") == "0"
    assert config.get("params", "mipmaps/generate") == "false"
    # The engine-generated remap section must survive untouched.
    assert config.get("remap", "uid") == '"uid://abc123"'
    assert config.get("remap", "path") == '"res://.godot/imported/player.png-9f8e.ctex"'


def test_patching_is_idempotent(tmp_path: Path) -> None:
    sidecar = tmp_path / "x.png.import"
    sidecar.write_text("[params]\n\ncompress/mode=0\n", encoding="utf-8")
    assert _patch_import_params(sidecar, {"compress/mode": "0"}) is False


def test_patching_a_missing_sidecar_is_a_no_op(tmp_path: Path) -> None:
    assert _patch_import_params(tmp_path / "nope.import", PIXEL_ART_PARAMS) is False


# -- tools ----------------------------------------------------------------


def test_list_providers_reports_configuration_and_budget() -> None:
    result = godot_list_asset_providers.invoke({})
    assert result["ok"]
    by_name = {entry["name"]: entry for entry in result["providers"]}
    assert by_name["placeholder"]["configured"] is True
    assert by_name["meshy"]["configured"] is False
    assert by_name["placeholder"]["kinds"] == ["image", "pixel"]
    assert by_name["meshy"]["env_var"] == "MESHY_API_KEY"
    assert result["budget_remaining_usd"] == result["budget_limit_usd"]


def test_generate_pixel_art_writes_into_the_project(project: str) -> None:
    """The full path: generate, write to res://, report the result.

    The import step needs a real Godot binary, so it is expected to fail here;
    what must work is that the file lands in the right place with the right
    content and the failure is reported rather than swallowed.
    """
    result = godot_generate_pixel_art.invoke(
        {
            "prompt": "a knight on a plain white background",
            "dest": "res://assets/sprites/knight.png",
            "width": 32,
            "height": 32,
            "provider": "placeholder",
            "project": project,
        }
    )
    written = Path(project) / "assets" / "sprites" / "knight.png"
    assert written.is_file()
    assert written.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert result["provider"] == "placeholder"
    assert result["cost_usd"] == 0.0


def test_generate_batch_numbers_the_files(project: str) -> None:
    godot_generate_pixel_art.invoke(
        {
            "prompt": "a coin on a plain white background",
            "dest": "res://assets/coin.png",
            "count": 3,
            "provider": "placeholder",
            "project": project,
        }
    )
    sprites = sorted(p.name for p in (Path(project) / "assets").glob("coin*.png"))
    assert sprites == ["coin_1.png", "coin_2.png", "coin_3.png"]


def test_generate_reports_a_missing_project(tmp_path: Path) -> None:
    result = godot_generate_pixel_art.invoke(
        {
            "prompt": "x",
            "dest": "res://x.png",
            "provider": "placeholder",
            "project": str(tmp_path),
        }
    )
    assert not result["ok"]
    assert "project.godot" in result["error"]
