"""
Web Simulator Render Engine — produces PNG frames for the browser.

Instead of driving real hardware, this renderer generates per-key PNG
images and serves them via HTTP/SSE to the Web Simulator frontend.
"""
from __future__ import annotations

import io
import logging
import time as _time_mod
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..core.layout import DisplayState, LayoutFrame, WidgetState
from ..core.types import AnimationType

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

# Key size presets — maps grid dimensions to pixel size per key
KEY_SIZES: dict[str, tuple[int, int]] = {
    "4x8": (96, 96),      # Stream Deck XL
    "3x5": (72, 72),      # Stream Deck Standard
    "3x2": (80, 80),      # Stream Deck Mini
    "2x4": (80, 80),      # Stream Deck Neo / Plus
    "3x4": (100, 100),    # Phone sparse
    "1x3": (96, 96),      # Stream Deck Pedal
}

# Legacy mapping for Stream Deck model names → grid
_DECK_MODEL_TO_GRID: dict[str, tuple[str, int, int]] = {
    "Stream Deck XL": ("4x8", 4, 8),
    "Stream Deck": ("3x5", 3, 5),
    "Stream Deck Mini": ("3x2", 3, 2),
    "Stream Deck Neo": ("2x4", 2, 4),
    "Stream Deck Plus": ("2x4", 2, 4),
    "Stream Deck Pedal": ("1x3", 1, 3),
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


_EFFECT_ANIMATIONS = frozenset({
    AnimationType.PULSE,
    AnimationType.BLINK,
    AnimationType.CRAWL,
    AnimationType.PROGRESS,
})


class SimRenderer:
    """
    Renders a LayoutFrame into per-key PNG images for the Web Simulator.

    Each key is rendered as a small square image with:
    - Background color from the Widget's display state
    - Icon/emoji centered
    - Label text overlay at bottom
    - Animation hint encoded in metadata (animated by CSS in browser)

    For sprite animations, the AnimationEngine provides pre-rendered frames
    that replace the base visual.  Label and badge are composited on top of
    the sprite frame, then the result is shipped as a PNG via SSE.
    """

    def __init__(
        self,
        rows: int = 4,
        cols: int = 8,
        display_name: str = "4x8",
        animation_engine: Optional["AnimationEngine"] = None,
    ) -> None:
        self.display_name = display_name
        self.rows = rows
        self.cols = cols
        grid_key = f"{rows}x{cols}"
        self.key_size = KEY_SIZES.get(grid_key, (72, 72))

        # Lazy import avoids circular dependency at module level
        if animation_engine is not None:
            self._anim_engine = animation_engine
        else:
            from .animation import AnimationEngine as AE
            self._anim_engine = AE()

    # ── Static key rendering (effects / no animation) ──

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
            self._draw_label(draw, state.label, w, h)

        # Badge in top-right
        if state.badge:
            self._draw_badge(draw, state.badge, w, h)

        # Convert to bytes
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ── Overlay helpers (shared between static and sprite paths) ──

    def _draw_label(self, draw: ImageDraw.Draw, label: str, w: int, h: int) -> None:
        """Draw label text at the bottom of a key image."""
        label_font_size = max(10, min(w // 8, 16))
        label_font = _default_font(label_font_size)
        lines = _split_text(label)
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

    def _draw_badge(self, draw: ImageDraw.Draw, badge_text: str, w: int, h: int) -> None:
        """Draw a red badge pill in the top-right corner."""
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

    # ── Sprite key rendering ──────────────────────

    def _render_sprite_key(self, ws: WidgetState, sprite_name: str, now: float) -> bytes:
        """Render a sprite-animated key: engine frame + label + badge overlays.

        Args:
            ws: The widget state.
            sprite_name: Name of the sprite clip to play.
            now: time.monotonic() for frame selection.

        Returns:
            PNG image bytes.
        """
        w, h = self.key_size

        # Get the current sprite frame from the engine (pre-sized + cached)
        sprite_frame = self._anim_engine.get_sprite_frame(
            ws.id, sprite_name, now, target_size=(w, h),
        )

        if sprite_frame is not None:
            img = sprite_frame.copy()
        else:
            # Fallback: solid color with icon
            bg_color = ws.display.color.lstrip("#") if ws.display.color else "000000"
            img = Image.new("RGB", (w, h), f"#{bg_color}")
            # Render icon on fallback
            if ws.display.icon:
                icon_font_size = min(w, h) // 3
                icon_font = _default_font(icon_font_size)
                draw = ImageDraw.Draw(img)
                try:
                    icon_bbox = draw.textbbox((0, 0), ws.display.icon, font=icon_font)
                except Exception:
                    icon_bbox = (0, 0, icon_font_size, icon_font_size)
                icon_w2 = icon_bbox[2] - icon_bbox[0]
                icon_h2 = icon_bbox[3] - icon_bbox[1]
                icon_x = (w - icon_w2) // 2
                icon_y = (h - icon_h2) // 2 - 5
                try:
                    draw.text((icon_x, icon_y), ws.display.icon, fill="white", font=icon_font)
                except Exception:
                    pass

        # Composite label and badge on top of the sprite frame
        draw = ImageDraw.Draw(img)
        if ws.display.label:
            self._draw_label(draw, ws.display.label, w, h)
        if ws.display.badge:
            self._draw_badge(draw, ws.display.badge, w, h)

        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    # ── Frame rendering ───────────────────────────

    def render_frame(self, frame: LayoutFrame) -> list[dict]:
        """
        Render an entire LayoutFrame.

        Returns a list of dicts, one per key, with PNG data as base64 + metadata.
        This is the format consumed by the Web Simulator frontend.

        Each key dict now includes an ``animation_mode`` field:

        - ``"css"`` — browser uses CSS keyframes for the animation (effect types)
        - ``"sprite"`` — server sends pre-rendered sprite frames as PNG; browser
          displays the image directly
        - ``"none"`` — no animation; static key
        """
        import base64

        now = _time_mod.monotonic()
        keys = []

        for i, widget_id in enumerate(frame.keymap):
            if widget_id and widget_id in frame.widgets:
                ws = frame.widgets[widget_id]
                display = ws.display
                sprite_name = display.sprite if display.sprite else "none"
                has_sprite = sprite_name and sprite_name != "none"
                has_effect = display.animation in _EFFECT_ANIMATIONS

                if has_sprite:
                    # Sprite animation — server generates frame, browser shows image
                    png_bytes = self._render_sprite_key(ws, sprite_name, now)
                    animation_mode = "sprite"
                else:
                    # Static or CSS-effect key
                    png_bytes = self.render_key(display)
                    animation_mode = "css" if has_effect else "none"

                keys.append({
                    "index": i,
                    "widget_id": widget_id,
                    "type": ws.type,
                    "icon": display.icon,
                    "color": display.color,
                    "animation": display.animation,
                    "animation_mode": animation_mode,
                    "label": display.label,
                    "badge": display.badge,
                    "image": base64.b64encode(png_bytes).decode("ascii"),
                })
            else:
                # Empty key — dark
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
                    "animation_mode": "none",
                    "label": "",
                    "badge": None,
                    "image": base64.b64encode(buf.getvalue()).decode("ascii"),
                })

        return keys
