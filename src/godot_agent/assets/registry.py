"""Provider selection and the session spend guard.

Selection is by *kind* (``image``, ``pixel``, ``model3d``), not by name: the
agent asks for pixel art, and configuration decides whether that means Retro
Diffusion or locally rendered placeholders. That indirection is what lets a
game be built offline and then re-skinned by setting one API key.

The budget is a hard stop, not advice. An agent in a retry loop can spend real
money quickly, and the cheapest place to notice is before the request.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from godot_agent.assets.base import (
    AssetProviderError,
    Provider,
    ProviderInfo,
)
from godot_agent.assets.providers import (
    MeshyProvider,
    OpenAIImageProvider,
    PlaceholderProvider,
    RetroDiffusionProvider,
)
from godot_agent.config import get_settings

__all__ = [
    "BudgetExceededError",
    "SessionBudget",
    "available_providers",
    "get_budget",
    "resolve_provider",
]

#: Every provider this package knows how to build, by name.
_PROVIDERS: dict[str, type[Provider]] = {
    "placeholder": PlaceholderProvider,
    "retrodiffusion": RetroDiffusionProvider,
    "openai": OpenAIImageProvider,
    "meshy": MeshyProvider,
}

#: Fallback order per kind when the configured provider is unusable.
_BY_KIND: dict[str, tuple[str, ...]] = {
    "image": ("openai", "placeholder"),
    "pixel": ("retrodiffusion", "placeholder"),
    "model3d": ("meshy",),
}


class BudgetExceededError(AssetProviderError):
    """A generation was refused because it would exceed the session budget."""


@dataclass(slots=True)
class SessionBudget:
    """Tracks paid asset spend for one session."""

    limit_usd: float
    spent_usd: float = 0.0
    charges: list[tuple[str, float]] = field(default_factory=list)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.limit_usd - self.spent_usd)

    def check(self, cost: float, description: str) -> None:
        """Refuse a charge that would exceed the limit.

        Raises:
            BudgetExceededError: naming the limit, so the agent reports the
                real reason rather than retrying into the same wall.
        """
        if cost <= 0:
            return
        if self.spent_usd + cost > self.limit_usd:
            raise BudgetExceededError(
                f"{description} would cost ${cost:.2f}, taking this session to "
                f"${self.spent_usd + cost:.2f} against a ${self.limit_usd:.2f} limit. "
                "Raise GODOT_AGENT_ASSET_BUDGET, or use the 'placeholder' provider, "
                "which is free."
            )

    def record(self, cost: float, description: str) -> None:
        """Record a charge that has already happened."""
        if cost <= 0:
            return
        self.spent_usd += cost
        self.charges.append((description, cost))


_budget: SessionBudget | None = None


def get_budget() -> SessionBudget:
    """The process-wide budget, created from settings on first use."""
    global _budget
    if _budget is None:
        _budget = SessionBudget(limit_usd=get_settings().asset_budget_usd)
    return _budget


def reset_budget() -> None:
    """Drop the tracked spend. Intended for tests."""
    global _budget
    _budget = None


def available_providers() -> list[ProviderInfo]:
    """Every known provider and whether it is usable right now."""
    return [cls.info for cls in _PROVIDERS.values()]  # type: ignore[attr-defined]


def resolve_provider(kind: str, name: str | None = None) -> Provider:
    """Return a usable provider for ``kind``.

    Args:
        kind: ``image``, ``pixel`` or ``model3d``.
        name: Force a specific provider. Without it, the configured choice is
            used, falling back through :data:`_BY_KIND` to whatever is
            actually configured.

    Raises:
        AssetProviderError: if nothing for that kind is configured, listing the
            environment variables that would fix it.
    """
    if name:
        factory = _PROVIDERS.get(name)
        if factory is None:
            raise AssetProviderError(
                f"unknown asset provider {name!r}; known: {', '.join(sorted(_PROVIDERS))}"
            )
        return factory()

    settings = get_settings()
    preferred = {
        "image": settings.providers.image,
        "pixel": settings.providers.pixel,
        "model3d": settings.providers.model3d,
    }.get(kind)

    order = [preferred, *_BY_KIND.get(kind, ())]
    for candidate in order:
        factory = _PROVIDERS.get(candidate or "")
        if factory is None:
            continue
        info: ProviderInfo = factory.info  # type: ignore[attr-defined]
        if info.serves(kind) and info.configured:
            return factory()

    needed = sorted(
        {
            f.info.env_var  # type: ignore[attr-defined]
            for f in _PROVIDERS.values()
            if f.info.serves(kind) and f.info.env_var  # type: ignore[attr-defined]
        }
    )
    raise AssetProviderError(
        f"No {kind} provider is configured. Set one of: {', '.join(needed)}. "
        + (
            "For 2D you can also use provider='placeholder', which renders "
            "locally and needs no key."
            if kind in ("image", "pixel")
            else ""
        )
    )


def is_configured(name: str) -> bool:
    """True when the named provider has whatever credentials it needs."""
    factory = _PROVIDERS.get(name)
    if factory is None:
        return False
    info: ProviderInfo = factory.info  # type: ignore[attr-defined]
    return info.configured


def _env_summary() -> dict[str, bool]:
    """Which provider keys are present. Used by the doctor output."""
    return {
        info.env_var: bool(os.environ.get(info.env_var))
        for info in available_providers()
        if info.env_var
    }
