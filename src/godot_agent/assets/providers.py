"""Concrete asset providers.

``placeholder`` is not a stub: it renders real, correctly sized PNGs locally
with Pillow. That matters more than it sounds. It means the whole pipeline --
generate, import, reference from a scene, run the game -- is exercisable
offline, in tests and in CI, and a game can be built end to end before anyone
has paid for an API key. Swapping in a paid provider then changes only the art.

The remote providers are written against their published request shapes:
Retro Diffusion v2 (``X-RD-Token``, async task polling) and Meshy OpenAPI v2
(``Bearer``, preview/refine task polling).
"""

from __future__ import annotations

import base64
import hashlib
import io
import time
from typing import Any

import httpx

from godot_agent.assets.base import (
    AssetProviderError,
    GeneratedAsset,
    ImageRequest,
    ModelRequest,
    ProviderInfo,
    require_key,
)

__all__ = [
    "MeshyProvider",
    "OpenAIImageProvider",
    "PlaceholderProvider",
    "RetroDiffusionProvider",
]

#: Seconds between polls of an asynchronous generation task.
_POLL_INTERVAL = 2.0

#: Give up on a task after this long.
_POLL_TIMEOUT = 600.0


def _poll(
    client: httpx.Client,
    url: str,
    *,
    is_done,
    timeout: float = _POLL_TIMEOUT,
) -> dict[str, Any]:
    """Poll ``url`` until ``is_done`` accepts the payload."""
    deadline = time.monotonic() + timeout
    while True:
        response = client.get(url)
        response.raise_for_status()
        payload = response.json()
        if is_done(payload):
            return payload
        if time.monotonic() > deadline:
            raise AssetProviderError(f"generation task timed out after {timeout:.0f}s: {url}")
        time.sleep(_POLL_INTERVAL)


# -- placeholder ----------------------------------------------------------


class PlaceholderProvider:
    """Render deterministic placeholder art locally, with no API key.

    Colour comes from a hash of the prompt, so the same subject keeps the same
    colour across a session and distinct subjects stay visually distinguishable
    -- enough to lay out and play a game before real art exists.
    """

    info = ProviderInfo(
        name="placeholder",
        # Serves pixel work too: placeholder art is already grid-aligned, so
        # a game can be built and played before any provider is configured.
        kinds=("image", "pixel"),
        env_var=None,
        description="Locally rendered placeholder sprites. Free, offline, deterministic.",
    )

    def estimate_cost(self, request: ImageRequest | ModelRequest) -> float:
        return 0.0

    def generate(self, request: ImageRequest | ModelRequest) -> list[GeneratedAsset]:
        if not isinstance(request, ImageRequest):
            raise AssetProviderError("the placeholder provider only generates images")

        from PIL import Image, ImageDraw

        assets: list[GeneratedAsset] = []
        for index in range(request.count):
            digest = hashlib.sha256(f"{request.prompt}:{index}".encode()).digest()
            fill = (digest[0], digest[1], digest[2], 255)
            accent = (255 - digest[0], 255 - digest[1], 255 - digest[2], 255)
            background = (0, 0, 0, 0) if request.transparent else (24, 24, 32, 255)

            image = Image.new("RGBA", (request.width, request.height), background)
            draw = ImageDraw.Draw(image)
            inset = max(1, min(request.width, request.height) // 8)
            draw.rectangle(
                [inset, inset, request.width - inset - 1, request.height - inset - 1],
                fill=fill,
                outline=accent,
            )

            buffer = io.BytesIO()
            image.save(buffer, format="PNG")
            assets.append(
                GeneratedAsset(
                    data=buffer.getvalue(),
                    suffix=".png",
                    provider=self.info.name,
                    cost_usd=0.0,
                    metadata={"prompt": request.prompt, "placeholder": True},
                )
            )
        return assets


# -- Retro Diffusion ------------------------------------------------------


class RetroDiffusionProvider:
    """Pixel art from Retro Diffusion's v2 API.

    Generation is always asynchronous: the POST returns a ``task_id`` that must
    be polled. Results arrive as base64 PNGs, or as hosted URLs for large
    outputs -- both are handled, because reading only ``base64_images`` silently
    loses results that were still charged for.
    """

    BASE_URL = "https://api.retrodiffusion.ai/v2"
    ENV_VAR = "RETRODIFFUSION_API_KEY"

    info = ProviderInfo(
        name="retrodiffusion",
        kinds=("pixel",),
        env_var=ENV_VAR,
        description="True grid-aligned pixel art and tilesets. Paid, per image.",
    )

    #: Sensible default style; the full catalogue is large and version-dependent.
    DEFAULT_STYLE = "rd_fast__default"

    def _client(self) -> httpx.Client:
        key = require_key(self.info.name, self.ENV_VAR)
        return httpx.Client(
            base_url=self.BASE_URL,
            headers={"X-RD-Token": key},
            timeout=60.0,
        )

    def _body(self, request: ImageRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "prompt": request.prompt,
            "prompt_style": request.style or self.DEFAULT_STYLE,
            "width": request.width,
            "height": request.height,
            "num_images": request.count,
        }
        if request.seed is not None:
            body["seed"] = request.seed
        if request.transparent:
            body["remove_bg"] = True
        body.update(request.extra)
        return body

    def estimate_cost(self, request: ImageRequest | ModelRequest) -> float:
        """Ask the API to price the request. The dry run is free and never generates."""
        if not isinstance(request, ImageRequest):
            raise AssetProviderError("retrodiffusion only generates images")
        with self._client() as client:
            response = client.post(
                "/inferences", json={**self._body(request), "check_cost": True}
            )
            response.raise_for_status()
            return float(response.json().get("balance_cost", 0.0))

    def generate(self, request: ImageRequest | ModelRequest) -> list[GeneratedAsset]:
        if not isinstance(request, ImageRequest):
            raise AssetProviderError("retrodiffusion only generates images")

        with self._client() as client:
            accepted = client.post("/inferences", json=self._body(request))
            accepted.raise_for_status()
            task_id = accepted.json().get("task_id")
            if not task_id:
                raise AssetProviderError(f"no task id in response: {accepted.text[:200]}")

            payload = _poll(
                client,
                f"/inferences/tasks/{task_id}",
                is_done=lambda body: body.get("status") in ("succeeded", "failed"),
            )
            if payload.get("status") == "failed":
                raise AssetProviderError(f"generation failed: {payload.get('error')}")

            result = payload.get("result") or {}
            cost = float(result.get("balance_cost", 0.0))
            images = [base64.b64decode(item) for item in result.get("base64_images") or []]
            # Large results come back only as hosted URLs, with base64 empty.
            for url in result.get("output_urls") or []:
                images.append(client.get(url).content)

            if not images:
                raise AssetProviderError("the task succeeded but returned no images")

            per_image = cost / len(images) if images else 0.0
            return [
                GeneratedAsset(
                    data=data,
                    suffix=".png",
                    provider=self.info.name,
                    cost_usd=per_image,
                    metadata={"prompt": request.prompt, "task_id": task_id},
                )
                for data in images
            ]


# -- OpenAI images --------------------------------------------------------


class OpenAIImageProvider:
    """General-purpose images from OpenAI's image API.

    Suited to concept art, UI and textures rather than pixel art -- it produces
    pixel-*styled* output rather than true grid-aligned pixels, so use
    :class:`RetroDiffusionProvider` when the grid matters.
    """

    BASE_URL = "https://api.openai.com/v1"
    ENV_VAR = "OPENAI_API_KEY"
    MODEL = "gpt-image-1"

    info = ProviderInfo(
        name="openai",
        kinds=("image",),
        env_var=ENV_VAR,
        description="General 2D art: concepts, UI, textures. Paid, per image.",
    )

    #: The API accepts a fixed set of sizes; requests are mapped onto them and
    #: the importer resizes down to the exact size the game needs.
    _SIZES = ((1024, 1024), (1024, 1536), (1536, 1024))

    def _size(self, request: ImageRequest) -> str:
        ratio = request.width / max(request.height, 1)
        if ratio > 1.2:
            chosen = (1536, 1024)
        elif ratio < 0.8:
            chosen = (1024, 1536)
        else:
            chosen = (1024, 1024)
        return f"{chosen[0]}x{chosen[1]}"

    def estimate_cost(self, request: ImageRequest | ModelRequest) -> float:
        if not isinstance(request, ImageRequest):
            raise AssetProviderError("openai only generates images")
        # Published list price for a standard-quality square image.
        return 0.04 * request.count

    def generate(self, request: ImageRequest | ModelRequest) -> list[GeneratedAsset]:
        if not isinstance(request, ImageRequest):
            raise AssetProviderError("openai only generates images")

        key = require_key(self.info.name, self.ENV_VAR)
        body: dict[str, Any] = {
            "model": self.MODEL,
            "prompt": request.prompt,
            "n": request.count,
            "size": self._size(request),
        }
        if request.transparent:
            body["background"] = "transparent"
        body.update(request.extra)

        with httpx.Client(base_url=self.BASE_URL, timeout=180.0) as client:
            response = client.post(
                "/images/generations",
                headers={"Authorization": f"Bearer {key}"},
                json=body,
            )
            response.raise_for_status()
            data = response.json().get("data") or []

        if not data:
            raise AssetProviderError("the API returned no images")

        assets: list[GeneratedAsset] = []
        for entry in data:
            encoded = entry.get("b64_json")
            if encoded is None:
                raise AssetProviderError("the API returned no inline image data")
            assets.append(
                GeneratedAsset(
                    data=base64.b64decode(encoded),
                    suffix=".png",
                    provider=self.info.name,
                    cost_usd=self.estimate_cost(request) / len(data),
                    metadata={"prompt": request.prompt},
                )
            )
        return assets


# -- Meshy 3D -------------------------------------------------------------


class MeshyProvider:
    """Text-to-3D models from Meshy's OpenAPI v2.

    Generation is two stages: a ``preview`` task produces untextured geometry,
    and an optional ``refine`` task adds PBR textures. Refining doubles both the
    time and the cost, so it only runs when textures were actually asked for.
    """

    BASE_URL = "https://api.meshy.ai/openapi/v2"
    ENV_VAR = "MESHY_API_KEY"

    info = ProviderInfo(
        name="meshy",
        kinds=("model3d",),
        env_var=ENV_VAR,
        description="Text-to-3D models as GLB, with optional PBR textures. Paid, per model.",
    )

    def _client(self) -> httpx.Client:
        key = require_key(self.info.name, self.ENV_VAR)
        return httpx.Client(
            base_url=self.BASE_URL,
            headers={"Authorization": f"Bearer {key}"},
            timeout=60.0,
        )

    def estimate_cost(self, request: ImageRequest | ModelRequest) -> float:
        if not isinstance(request, ModelRequest):
            raise AssetProviderError("meshy only generates 3D models")
        # Published guidance is roughly $0.10-$0.30 per model; refining costs
        # a second task. Estimated rather than quoted -- Meshy has no dry run.
        return 0.30 if request.textured else 0.15

    def _await_task(self, client: httpx.Client, task_id: str) -> dict[str, Any]:
        payload = _poll(
            client,
            f"/text-to-3d/{task_id}",
            is_done=lambda body: body.get("status")
            in ("SUCCEEDED", "FAILED", "CANCELED"),
        )
        if payload.get("status") != "SUCCEEDED":
            raise AssetProviderError(
                f"Meshy task {task_id} ended as {payload.get('status')}: "
                f"{payload.get('task_error') or ''}"
            )
        return payload

    def generate(self, request: ImageRequest | ModelRequest) -> list[GeneratedAsset]:
        if not isinstance(request, ModelRequest):
            raise AssetProviderError("meshy only generates 3D models")

        with self._client() as client:
            preview = client.post(
                "/text-to-3d",
                json={
                    "mode": "preview",
                    "prompt": request.prompt,
                    "should_remesh": True,
                    "target_polycount": request.target_polycount,
                    "target_formats": ["glb"],
                    **request.extra,
                },
            )
            preview.raise_for_status()
            task_id = preview.json()["result"]
            payload = self._await_task(client, task_id)

            if request.textured:
                refine = client.post(
                    "/text-to-3d",
                    json={
                        "mode": "refine",
                        "preview_task_id": task_id,
                        "enable_pbr": True,
                    },
                )
                refine.raise_for_status()
                payload = self._await_task(client, refine.json()["result"])

            url = (payload.get("model_urls") or {}).get("glb")
            if not url:
                raise AssetProviderError("the task succeeded but returned no GLB url")
            # Meshy's download URLs expire, so fetch immediately.
            data = client.get(url).content

        return [
            GeneratedAsset(
                data=data,
                suffix=".glb",
                provider=self.info.name,
                cost_usd=self.estimate_cost(request),
                metadata={"prompt": request.prompt, "task_id": task_id},
            )
        ]
