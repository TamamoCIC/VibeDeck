"""Icon layer — geometric shapes replacing emoji glyphs.

PIL/FreeType cannot render color emoji fonts (Segoe UI Emoji only
exposes monochrome outlines).  This layer draws clean white vector
shapes (circles, triangles, hexagons, bars) mapped from the widget's
``icon`` emoji code-point.
"""

from __future__ import annotations

import math
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ...core.types import DisplayState
from ._protocol import Layer


def _load_icon_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font for text-based fallback icons (e.g. "?", "+")."""
    from vibe_deck.platform import font_paths as _platform_font_paths

    for fp in _platform_font_paths():
        try:
            return ImageFont.truetype(fp, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


class IconLayer:
    """Draw a geometric icon shape centered on the key."""

    name = "icon"

    def render(
        self, state: DisplayState, key_size: tuple[int, int], now: float
    ) -> Optional[Image.Image]:
        if not state.icon:
            return None

        w, h = key_size
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        icon = state.icon

        cx, cy = w // 2, h // 2
        r = min(w, h) // 5
        rr = r + 2

        # ── Filled circle → Running / Thinking ──
        if icon in ("🐙",):
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 255, 255, 255))
            return img

        # ── Empty ring → Offline ──
        if icon in ("⚫",):
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline=(255, 255, 255, 255), width=2)
            return img

        # ── Warning triangle → Error ──
        if icon in ("⚠️",):
            # Build an upward-pointing equilateral triangle
            ir = r + 3
            hh = int(ir * 1.5)
            half_base = int(ir * 0.866)
            pts = [
                (cx, cy - hh // 2),
                (cx - half_base, cy + hh // 2),
                (cx + half_base, cy + hh // 2),
            ]
            draw.polygon(pts, outline=(255, 255, 255, 255), width=2)
            # "!" in center
            try:
                ex_font = _load_icon_font(r)
                draw.text((cx - 4, cy - r // 2 - 2), "!",
                          fill=(255, 255, 255, 255), font=ex_font)
            except Exception:
                pass
            return img

        # ── Question mark → Asking... ──
        if icon in ("❓",):
            try:
                q_font = _load_icon_font(rr * 2)
                draw.text((cx - rr // 2 - 2, cy - rr), "?",
                          fill=(255, 255, 255, 255), font=q_font)
            except Exception:
                draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                             outline=(255, 255, 255, 255), width=2)
            return img

        # ── Hexagon (shield) → Approval needed ──
        if icon in ("🛡️",):
            pts = [
                (cx, cy - r - 3),
                (cx + r + 3, cy - r // 2),
                (cx + r + 3, cy + r // 2),
                (cx, cy + r + 3),
                (cx - r - 3, cy + r // 2),
                (cx - r - 3, cy - r // 2),
            ]
            draw.polygon(pts, outline=(255, 255, 255, 255), width=2)
            return img

        # ── Double vertical bars → Paused ──
        if icon in ("⏸️", "⏸"):
            bar_w = max(3, r // 2)
            bar_h = r * 2
            gap = r // 2
            draw.rectangle(
                [cx - gap - bar_w, cy - r, cx - gap, cy + r],
                fill=(255, 255, 255, 255),
            )
            draw.rectangle(
                [cx + gap, cy - r, cx + gap + bar_w, cy + r],
                fill=(255, 255, 255, 255),
            )
            return img

        # ── Plus / New → Starting ──
        if icon in ("🆕",):
            try:
                s_font = _load_icon_font(rr * 2)
                draw.text((cx - rr, cy - rr), "+",
                          fill=(255, 255, 255, 255), font=s_font)
            except Exception:
                draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                             outline=(255, 255, 255, 255), width=2)
            return img

        # ── Fallback: try font glyph, then empty ring ──
        try:
            fb = _load_icon_font(rr * 2)
            bbox = draw.textbbox((0, 0), icon, font=fb)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw // 2, cy - th // 2), icon,
                      fill=(255, 255, 255, 255), font=fb)
        except Exception:
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline=(255, 255, 255, 255), width=2)
        return img
