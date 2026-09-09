"""Provider-agnostic asset generation.

One interface, several backends. The agent asks for "a 32x32 idle sprite"; which
service produces it is configuration, never something that leaks into a prompt
or a scene file.

Two things every provider must get right:

* **Report cost before spending it.** ``estimate_cost`` runs without generating,
  so the budget guard can refuse a request rather than discover the charge
  afterwards.
* **Fail as "not configured", not as a crash.** A missing API key is the normal
  state for most providers, and the agent should be told which environment
  variable to set rather than handed a traceback.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "AssetProviderError",
    "GeneratedAsset",
    "ImageRequest",
    "ModelRequest",
    "NotConfiguredError",
    "Provider",
    "ProviderInfo",
]


class AssetProviderError(RuntimeError):
    """A provider failed to produce an asset."""


class NotConfiguredError(AssetProviderError):
    """A provider is unusable because its credentials are missing.

    Carries the environment variable to set, so the agent can tell the user
    exactly what is needed instead of guessing.
    """

    def __init__(self, provider: str, env_var: str) -> None:
        super().__init__(
            f"The {provider!r} asset provider is not configured: set {env_var} "
            f"in the environment to use it."
        )
        self.provider = provider
        self.env_var = env_var


@dataclass(slots=True)
class ImageRequest:
    """A request for one or more 2D images."""

    prompt: str
    width: int = 64
    height: int = 64
    count: int = 1
    #: Provider style id. Meaning is provider-specific; leave unset for default.
    style: str | None = None
    #: Ask for a transparent background rather than a flat one.
    transparent: bool = False
    #: Deterministic seed, where the provider supports one.
    seed: int | None = None
    #: Extra provider-specific fields, passed through untouched.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelRequest:
    """A request for a single 3D model."""

    prompt: str
    #: Ask for a rigged model, where the provider supports it.
    rigged: bool = False
    #: Generate PBR textures rather than untextured geometry.
    textured: bool = True
    target_polycount: int = 30000
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class GeneratedAsset:
    """One generated file, in memory.

    Held as bytes rather than written directly so the caller decides where it
    lands -- assets must go through the importer, never straight into the
    project.
    """

    data: bytes
    #: File extension including the dot, e.g. ``.png`` or ``.glb``.
    suffix: str
    provider: str
    #: What the provider charged, in USD, where it reports it.
    cost_usd: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """What a provider is and what it needs to run."""

    name: str
    #: Every asset kind this provider can serve: "image", "pixel", "model3d".
    kinds: tuple[str, ...]
    env_var: str | None
    description: str

    @property
    def configured(self) -> bool:
        """True when the provider can actually be used right now."""
        return self.env_var is None or bool(os.environ.get(self.env_var))

    def serves(self, kind: str) -> bool:
        """True when this provider can produce assets of ``kind``."""
        return kind in self.kinds


@runtime_checkable
class Provider(Protocol):
    """What every asset provider implements."""

    info: ProviderInfo

    def estimate_cost(self, request: ImageRequest | ModelRequest) -> float:
        """Return the USD cost of ``request`` without generating anything."""
        ...

    def generate(self, request: ImageRequest | ModelRequest) -> list[GeneratedAsset]:
        """Produce the assets.

        Raises:
            NotConfiguredError: if credentials are missing.
            AssetProviderError: if the provider rejected or failed the request.
        """
        ...


def require_key(provider: str, env_var: str) -> str:
    """Return the API key for ``env_var`` or explain that it is missing."""
    key = os.environ.get(env_var)
    if not key:
        raise NotConfiguredError(provider, env_var)
    return key
