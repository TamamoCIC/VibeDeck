"""
PIL Renderer — composes Layer stacks into StandardFrame bitmaps.

This is the canonical rendering engine for VibeDeck.  It takes a
``LayoutFrame`` (widget states mapped to key positions), runs each
key through the layer stack, applies effects as post-processing,
and emits a ``StandardFrame`` with dual-format (JPEG + PNG) encoded
bytes ready for transport.

See `docs/adr/0002-standard-frame-pipeline.md`.
"""

from __future__ import annotations

import io
import logging
import time as _time_mod
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw

from ..core.layout import LayoutFrame
from ..core.types import AnimationType
from .animation import AnimationEngine
from .layers import (
    BackdropLayer,
    BadgeLayer,
    IconLayer,
    LabelLayer,
    SpriteLayer,
    has_active_effect,
)
from .standard_frame import StandardFrame, KeyImage

log = logging.getLogger("vibe_deck.render.renderer")

# Image encoding defaults
JPEG_QUALITY = 94
PNG_FORMAT = "PNG"
JPEG_FORMAT = "JPEG"


class PILRenderer:
    """Compose a LayoutFrame into a fully-rendered StandardFrame.

    Parameters:
        animation_engine: Shared ``AnimationEngine`` for sprite clips
            and procedural effects.  Created lazily if omitted.
        jpeg_quality: JPEG encoding quality (1-100).  Stream Deck
            firmware expects JPEG; lower quality = smaller packets.
    """

    def __init__(
        self,
        animation_engine: AnimationEngine | None = None,
        jpeg_quality: int = JPEG_QUALITY,
    ) -> None:
        if animation_engine is not None:
            self._anim = animation_engine
        else:
            self._anim = AnimationEngine()
            # Load sprite clips from assets if present
            from ..render.animation_loader import load_clips
            from ..render.hardware import KEY_SIZE as HW_KEY_SIZES

            assets_dir = Path(__file__).parent.parent / "assets"
            all_key_sizes = [
                sz for sz in set(HW_KEY_SIZES.values()) if sz[0] > 0 and sz[1] > 0
            ]
            clips = load_clips(assets_dir, all_key_sizes)
            for name, clip in clips.items():
                self._anim.register_clip(clip)

        self._jpeg_quality = jpeg_quality

        # Default layer stack (z-order: bottom → top)
        self._layers = [
            BackdropLayer(),
            SpriteLayer(self._anim),
            IconLayer(),
            LabelLayer(),
            BadgeLayer(),
        ]

    # ── Public API ───────────────────────────────────

    def render(self, frame: LayoutFrame) -> StandardFrame:
        """Render a complete LayoutFrame into a StandardFrame.

        Args:
            frame: The layout snapshot to render.

        Returns:
            ``StandardFrame`` with every key's JPEG + PNG bytes populated.
        """
        now = _time_mod.monotonic()
        key_size = self._key_size_for(frame)
        sf = StandardFrame.for_grid(frame.rows, frame.cols, key_size)

        for i, key_image in enumerate(sf.keys):
            widget_id = frame.keymap[i] if i < len(frame.keymap) else None
            key_image.widget_id = widget_id

            if widget_id and widget_id in frame.widgets:
                ws = frame.widgets[widget_id]
                # Suppress backdrop+icon when sprite is active
                sprite_active = bool(
                    ws.display.sprite and ws.display.sprite != "none"
                )
                img = self._composite_key(ws, key_size, now, skip_static=sprite_active)
                # Post-process: procedural effect
                if has_active_effect(ws.display):
                    img = self._anim.apply_effect(img, ws.display.animation, now)
            else:
                img = Image.new("RGB", key_size, "#000000")

            key_image.jpeg = self._encode(img, JPEG_FORMAT, self._jpeg_quality)
            key_image.png = self._encode(img, PNG_FORMAT)

        return sf

    # ── Internals ────────────────────────────────────

    def _composite_key(
        self,
        ws: "WidgetState",
        key_size: tuple[int, int],
        now: float,
        skip_static: bool = False,
    ) -> Image.Image:
        """Run the layer stack for one key.

        Args:
            ws: The widget state (holds DisplayState + meta).
            key_size: (w, h) pixel dimensions for this key.
            now: ``time.monotonic()`` for animation frame selection.
            skip_static: If True, skip Backdrop + Icon (sprite replaces them).

        Returns:
            A fully-composited RGB PIL Image.
        """
        w, h = key_size

        # Start with a transparent RGBA canvas
        composite = Image.new("RGBA", (w, h), (0, 0, 0, 0))

        for layer in self._layers:
            if skip_static and layer.name in ("backdrop", "icon"):
                continue
            try:
                rgba = layer.render(ws.display, key_size, now)
            except Exception:
                log.debug("[RENDER] layer %r threw — skipping", layer.name, exc_info=True)
                continue
            if rgba is None:
                continue
            if rgba.mode != "RGBA":
                rgba = rgba.convert("RGBA")
            composite = Image.alpha_composite(composite, rgba)

        # Composite over a black background (defensive — transparent pixels
        # land on black instead of whatever the codec defaults to)
        bg = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        merged = Image.alpha_composite(bg, composite)
        return merged.convert("RGB")

    @staticmethod
    def _key_size_for(frame: LayoutFrame) -> tuple[int, int]:
        """Infer key pixel size from the grid name.

        Grids with known hardware counterparts use their canonical sizes.
        Unknown grids default to 72×72.
        """
        from .hardware import KEY_SIZE
        from .sim import KEY_SIZES as SIM_KEY_SIZES

        grid = f"{frame.rows}x{frame.cols}"
        return SIM_KEY_SIZES.get(grid) or KEY_SIZE.get(frame.display_name) or (72, 72)

    @staticmethod
    def _encode(img: Image.Image, fmt: str, quality: int = JPEG_QUALITY) -> bytes:
        """Encode a PIL Image to the requested format."""
        buf = io.BytesIO()
        if fmt == JPEG_FORMAT:
            # Ensure RGB mode for JPEG
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(buf, format=JPEG_FORMAT, quality=quality)
        else:
            img.save(buf, format=PNG_FORMAT)
        return buf.getvalue()
