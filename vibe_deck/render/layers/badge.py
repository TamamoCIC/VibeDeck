"""Badge layer — red notification pill in the top-right corner."""

from __future__ import annotations

from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ...core.types import DisplayState
from ._protocol import Layer


def _badge_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    from vibe_deck.platform import font_paths as _platform_font_paths

    _paths = list(_platform_font_paths())
    _paths.sort(key=lambda p: "emoj" in p.lower())  # emoji last
    for fp in _paths:
        try:
            return ImageFont.truetype(fp, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


class BadgeLayer:
    """Draw a red pill badge in the top-right corner."""

    name = "badge"

    def render(
        self, state: DisplayState, key_size: tuple[int, int], now: float
    ) -> Optional[Image.Image]:
        if not state.badge:
            return None

        w, h = key_size
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        badge = state.badge
        badge_size = min(w, h) // 4
        font = _badge_font(badge_size)

        try:
            bbox = draw.textbbox((0, 0), badge, font=font)
        except Exception:
            bbox = (0, 0, badge_size, badge_size)
        bw = bbox[2] - bbox[0]
        bh = bbox[3] - bbox[1]
        bx = w - bw - 4
        by = 2

        # Red pill background
        draw.ellipse(
            [bx - 4, by - 1, bx + bw + 4, by + bh + 3],
            fill=(239, 68, 68, 255),  # #EF4444
        )
        try:
            draw.text((bx, by), badge,
                      fill=(255, 255, 255, 255), font=font)
        except Exception:
            pass

        return img
