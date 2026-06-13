"""Label layer — bottom text with shadow."""

from __future__ import annotations

from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ...core.types import DisplayState
from ._protocol import Layer


def _label_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a text font (non-emoji) for labels."""
    from vibe_deck.platform import font_paths as _platform_font_paths

    _paths = list(_platform_font_paths())
    # Emoji fonts last — they make text look unbalanced
    _paths.sort(key=lambda p: "emoj" in p.lower())

    for fp in _paths:
        try:
            return ImageFont.truetype(fp, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _split_text(label: str, max_chars: int = 6) -> list[str]:
    """Split a label into lines for fitting on a small key."""
    if len(label) <= max_chars:
        return [label]
    mid = len(label) // 2
    return [label[:mid], label[mid:]]


class LabelLayer:
    """Draw label text at the bottom of the key with a drop-shadow."""

    name = "label"

    def render(
        self, state: DisplayState, key_size: tuple[int, int], now: float
    ) -> Optional[Image.Image]:
        if not state.label:
            return None

        w, h = key_size
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        font_size = max(10, min(w // 8, 16))
        font = _label_font(font_size)
        lines = _split_text(state.label)

        line_h = font_size + 2
        total_h = len(lines) * line_h
        y = h - total_h - 3

        for line in lines:
            try:
                bbox = draw.textbbox((0, 0), line, font=font)
            except Exception:
                bbox = (0, 0, font_size * len(line), font_size)
            lw = bbox[2] - bbox[0]
            lx = (w - lw) // 2

            # Shadow (dark, offset)
            try:
                draw.text((lx + 1, y + 1), line,
                          fill=(0, 0, 0, 180), font=font)
            except Exception:
                pass
            # Text (white)
            try:
                draw.text((lx, y), line,
                          fill=(255, 255, 255, 255), font=font)
            except Exception:
                pass
            y += line_h

        return img
