"""
Web Simulator Render Engine — produces PNG frames for the browser.

Instead of driving real hardware, this renderer generates per-key PNG
images and serves them via HTTP/SSE to the Web Simulator frontend.
"""

from __future__ import annotations

import io
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..core.layout import DisplayState, LayoutFrame, WidgetState

log = logging.getLogger("vibe_deck.render.sim")

# Emoji / icon to simple colored square mapping for deterministic rendering
# In production this would use an icon font or sprite sheet
ICON_COLORS: dict[str, str] = {
    "🐙": "#FF6B6B",
    "🦊": "#FFA500",
    "🦞": "#FF4500",
    "🟢": "#22C55E",
    "🔴": "#EF4444",
    "🟡": "#EAB308",
    "🔵": "#3B82F6",
    "⚫": "#6B7280",
    "💬": "#6366F1",
    "💤": "#9CA3AF",
    "📊": "#06B6D4",
    "⚙️": "#64748B",
    "⏰": "#8B5CF6",
    "🖥️": "#14B8A6",
    "✅": "#22C55E",
    "❌": "#EF4444",
    "📋": "#F59E0B",
    "🎤": "#EC4899",
    "📄": "#6B7280",
    "◀": "#64748B",
    "▶": "#64748B",
    "🆕": "#22C55E",
}

# Key size presets
KEY_SIZES: dict[str, tuple[int, int]] = {
    "Stream Deck XL": (96, 96),
    "Stream Deck": (72, 72),
    "Stream Deck Mini": (80, 80),
    "Stream Deck Neo": (80, 80),
    "Stream Deck Plus": (100, 100),
    "Stream Deck Pedal": (96, 96),
}


def _default_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Get a font at the given size. Falls back to default."""
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "C:\\Windows\\Fonts\\consola.ttf",
        "C:\\Windows\\Fonts\\segoeui.ttf",
    ]
    for fp in font_paths:
        try:
            return ImageFont.truetype(fp, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()


def _split_text(label: str, max_chars: int = 6) -> list[str]:
    """Split a label into lines for fitting on a small key."""
    if len(label) <= max_chars:
        return [label]
    # Simple split at word boundaries
    mid = len(label) // 2
    return [label[:mid], label[mid:]]


class SimRenderer:
    """
    Renders a LayoutFrame into per-key PNG images for the Web Simulator.

    Each key is rendered as a small square image with:
    - Background color from the Widget's display state
    - Icon/emoji centered
    - Label text overlay at bottom
    - Animation hint encoded in metadata (animated by CSS in browser)
    """

    def __init__(self, deck_type: str = "Stream Deck XL") -> None:
        self.deck_type = deck_type
        self.key_size = KEY_SIZES.get(deck_type, (72, 72))

    def render_key(self, state: DisplayState, pressed: bool = False) -> bytes:
        """
        Render a single key to PNG bytes.

        Args:
            state: The Widget's current display state.
            pressed: Whether the key is currently pressed (for highlight).

        Returns:
            PNG image bytes.
        """
        w, h = self.key_size
        bg_color = state.color.lstrip("#") if state.color else "000000"

        # Pressed state: lighten the color a bit
        if pressed:
            r = min(255, int(bg_color[0:2], 16) + 40)
            g = min(255, int(bg_color[2:4], 16) + 40)
            b = min(255, int(bg_color[4:6], 16) + 40)
            bg_color = f"{r:02x}{g:02x}{b:02x}"

        img = Image.new("RGB", (w, h), f"#{bg_color}")
        draw = ImageDraw.Draw(img)

        # Icon (emoji rendered as text)
        icon = state.icon or " "
        icon_font_size = min(w, h) // 3
        icon_font = _default_font(icon_font_size)

        # Approximate emoji size
        try:
            icon_bbox = draw.textbbox((0, 0), icon, font=icon_font)
        except Exception:
            icon_bbox = (0, 0, icon_font_size, icon_font_size)
        icon_w = icon_bbox[2] - icon_bbox[0]
        icon_h = icon_bbox[3] - icon_bbox[1]
        icon_x = (w - icon_w) // 2
        icon_y = (h - icon_h) // 2 - 5  # slightly above center to leave room for label
        try:
            draw.text((icon_x, icon_y), icon, fill="white", font=icon_font)
        except Exception:
            pass  # some fonts can't render emoji — skip gracefully

        # Label at bottom
        if state.label:
            label_font_size = max(10, min(w // 8, 16))
            label_font = _default_font(label_font_size)
            lines = _split_text(state.label)
            y_offset = h - (len(lines) * (label_font_size + 2)) - 3
            for line in lines:
                try:
                    l_bbox = draw.textbbox((0, 0), line, font=label_font)
                except Exception:
                    l_bbox = (0, 0, label_font_size * len(line), label_font_size)
                l_w = l_bbox[2] - l_bbox[0]
                l_x = (w - l_w) // 2
                try:
                    # Semi-transparent background for readability
                    draw.rectangle(
                        [l_x - 2, y_offset, l_x + l_w + 2, y_offset + label_font_size + 2],
                        fill=(0, 0, 0, 128),
                    )
                except Exception:
                    pass
                try:
                    draw.text((l_x, y_offset), line, fill="white", font=label_font)
                except Exception:
                    pass
                y_offset += label_font_size + 2

        # Badge in top-right
        if state.badge:
            badge_text = state.badge
            badge_size = min(w, h) // 4
            badge_font = _default_font(badge_size)
            try:
                b_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            except Exception:
                b_bbox = (0, 0, badge_size, badge_size)
            b_w = b_bbox[2] - b_bbox[0]
            b_h = b_bbox[3] - b_bbox[1]
            bx = w - b_w - 4
            by = 2
            # Badge pill background
            draw.ellipse([bx - 4, by - 1, bx + b_w + 4, by + b_h + 3], fill="#EF4444")
            try:
                draw.text((bx, by), badge_text, fill="white", font=badge_font)
            except Exception:
                pass

        # Convert to bytes
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def render_frame(self, frame: LayoutFrame) -> list[dict]:
        """
        Render an entire LayoutFrame.

        Returns a list of dicts, one per key, with PNG data as base64 + metadata.
        This is the format consumed by the Web Simulator frontend.
        """
        import base64

        keys = []
        for i, widget_id in enumerate(frame.keymap):
            if widget_id and widget_id in frame.widgets:
                ws = frame.widgets[widget_id]
                png_bytes = self.render_key(ws.display)
                keys.append({
                    "index": i,
                    "widget_id": widget_id,
                    "type": ws.type,
                    "icon": ws.display.icon,
                    "color": ws.display.color,
                    "animation": ws.display.animation,
                    "label": ws.display.label,
                    "badge": ws.display.badge,
                    "image": base64.b64encode(png_bytes).decode("ascii"),
                })
            else:
                # Empty key — black
                img = Image.new("RGB", self.key_size, "#111111")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                keys.append({
                    "index": i,
                    "widget_id": None,
                    "type": "empty",
                    "icon": "",
                    "color": "#111111",
                    "animation": "none",
                    "label": "",
                    "badge": None,
                    "image": base64.b64encode(buf.getvalue()).decode("ascii"),
                })

        return keys
