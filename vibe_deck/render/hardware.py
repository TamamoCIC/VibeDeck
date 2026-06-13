"""
Hardware Render Engine — drives a real Elgato Stream Deck.

Consumes LayoutFrame snapshots and renders them onto physical hardware.
Supports hot-plug detection and auto-recovery.

Rendering pipeline (per key):
  1. Get base image — sprite frame from AnimationEngine, or solid color
  2. Render icon emoji centered on the key
  3. Render label text at bottom with dark background bar
  4. Render badge pill in top-right corner
  5. Apply procedural effect (pulse/blink/crawl/progress) if active
  6. Push to hardware via USB HID (with frame diffing)
"""

from __future__ import annotations

import asyncio
import logging
import time as _time_mod
from io import BytesIO
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from ..core.layout import LayoutFrame
from ..core.types import AnimationType, DisplayState, WidgetState

log = logging.getLogger("vibe_deck.render.hardware")

try:
    from StreamDeck.DeviceManager import DeviceManager
    from StreamDeck.Devices.StreamDeck import StreamDeck as _SDBase

    HAS_DECK = True
except ImportError:
    DeviceManager = None  # type: ignore
    _SDBase = None  # type: ignore
    HAS_DECK = False


KEY_SIZE = {
    "Stream Deck XL": (96, 96),
    "Stream Deck": (72, 72),
    "Stream Deck Mini": (80, 80),
    "Stream Deck Neo": (80, 80),
    "Stream Deck Plus": (100, 100),
    "Stream Deck Pedal": (0, 0),
}

# Stream Deck model → default template filename
_DECK_MODEL_TO_TEMPLATE: dict[str, str] = {
    "Stream Deck XL": "default-streamdeck-xl.yaml",
    "Stream Deck": "default-streamdeck.yaml",
    "Stream Deck Mini": "default-streamdeck-mini.yaml",
    "Stream Deck Neo": "default-streamdeck.yaml",
    "Stream Deck Plus": "default-streamdeck.yaml",
    "Stream Deck Pedal": "default-streamdeck-mini.yaml",
}

# Phone grid → default template filename
_PHONE_GRID_TO_TEMPLATE: dict[str, str] = {
    "4x8": "default-phone-4x8.yaml",
    "3x5": "default-phone-3x5.yaml",
    "3x4": "default-phone-3x4.yaml",
}


def get_deck_template(deck_type: str) -> str | None:
    """Get the default template filename for a Stream Deck model."""
    return _DECK_MODEL_TO_TEMPLATE.get(deck_type)


def get_phone_template(grid: str) -> str | None:
    """Get the default template filename for a phone grid."""
    return _PHONE_GRID_TO_TEMPLATE.get(grid)


# ── Font & text helpers ──────────────────────────


def _load_font(size: int, prefer_emoji: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a font at the given size.

    When *prefer_emoji* is True, emoji-capable fonts (seguiemj.ttf) are
    tried first so icons like 🐙 render as real glyphs instead of "?".
    """
    from vibe_deck.platform import font_paths as _platform_font_paths

    _paths = list(_platform_font_paths())
    if not prefer_emoji:
        # Move emoji font to the end for labels — emoji glyphs are too
        # large relative to text and make labels look unbalanced.
        _paths.sort(key=lambda p: "emoj" in p.lower())  # emoji last

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


# ── Hardware Renderer ────────────────────────────


class HardwareRenderer:
    """
    Renders LayoutFrames onto a physical Elgato Stream Deck.

    Features:
    - Full key rendering: color, icon emoji, label text, badge pill
    - Animation engine: procedural effects + sprite clip playback
    - Frame diffing: only updates keys that changed
    - Hot-plug recovery: detects USB disconnect/reconnect
    """

    def __init__(
        self,
        device_index: int = 0,
        animation_engine: Optional["AnimationEngine"] = None,
    ) -> None:
        self._device_index = device_index
        self._deck: Optional["_SDBase"] = None
        self._last_frame_hashes: list[int | None] = []
        self._key_callback: Optional[callable] = None

        # Lazy import to avoid circular dependency at module level
        if animation_engine is not None:
            self._anim_engine = animation_engine
        else:
            from .animation import AnimationEngine as AE
            self._anim_engine = AE()

    # ── Properties ────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._deck is not None

    @property
    def deck_type(self) -> str:
        """Raw Elgato model name from hardware SDK."""
        if not self._deck:
            return "unknown"
        return self._deck.deck_type()

    @property
    def grid_name(self) -> str:
        """Grid dimensions string (e.g. '4x8' for XL)."""
        kc = self.key_count
        kt = self.deck_type
        _grid_map = {
            "Stream Deck XL": "4x8",
            "Stream Deck": "3x5",
            "Stream Deck Mini": "3x2",
            "Stream Deck Neo": "2x4",
            "Stream Deck Plus": "2x4",
            "Stream Deck Pedal": "1x3",
        }
        return _grid_map.get(kt, f"{kc}keys")

    @property
    def key_count(self) -> int:
        if not self._deck:
            return 0
        return self._deck.key_count()

    @property
    def key_size(self) -> tuple[int, int]:
        return KEY_SIZE.get(self.deck_type, (72, 72))

    # ── Lifecycle ─────────────────────────────────

    def open(self) -> bool:
        """Connect to the Stream Deck. Returns True on success."""
        if not HAS_DECK:
            log.warning("Stream Deck library not installed")
            return False

        try:
            decks = DeviceManager().enumerate()
            if not decks or self._device_index >= len(decks):
                log.debug("No Stream Deck found (index=%d)", self._device_index)
                return False

            self._deck = decks[self._device_index]
            self._deck.open()
            self._deck.reset()
            self._last_frame_hashes = [None] * self._deck.key_count()
            log.info("Opened %s (serial=%s)", self.deck_type, self._deck.get_serial_number())
            return True
        except Exception:
            log.exception("Failed to open Stream Deck")
            self._deck = None
            return False

    def close(self) -> None:
        """Disconnect from the Stream Deck."""
        if self._deck is not None:
            try:
                self._deck.reset()
                self._deck.close()
            except Exception:
                pass
            self._deck = None
            log.info("Deck closed")

    def set_key_callback(self, callback: callable) -> None:
        """Register a callback for physical key presses."""
        self._key_callback = callback
        if self._deck:
            self._deck.set_key_callback(
                lambda d, k, s: callback(k, bool(s))
            )

    # ── Key Image Rendering ───────────────────────

    def _draw_icon_shape(
        draw: ImageDraw.ImageDraw, icon: str, w: int, h: int
    ) -> None:
        """Draw a geometric icon shape to replace emoji that PIL can't render.

        Maps common VibeDeck emoji icons to clean PIL vector shapes.
        All shapes are filled white, sized relative to the key dimensions.
        """
        cx, cy = w // 2, h // 2
        r = min(w, h) // 5          # radius for circles
        rr = r + 2                   # slightly larger for outer rings

        # ── Filled circle → Running / Thinking / Idle ──
        if icon in ("🐙",):
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill="white")
            return

        # ── Empty circle → Offline ──
        if icon in ("⚫",):
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline="white", width=2)
            return

        # ── Warning triangle → Error / Warning ──
        if icon in ("⚠️",):
            import math
            ir = r + 3
            pts = [
                (cx, cy - ir),
                (cx - int(ir * math.cos(math.pi / 6)), cy + int(ir * math.sin(math.pi / 6))),
                (cx + int(ir * math.cos(math.pi / 6)), cy + int(ir * math.sin(math.pi / 6))),
            ]
            draw.polygon(pts, outline="white", width=2)
            # "!" in center
            try:
                ex_font = _load_font(r, prefer_emoji=False)
                draw.text((cx - 4, cy - r // 2 - 2), "!", fill="white", font=ex_font)
            except Exception:
                pass
            return

        # ── Question mark → Asking... ──
        if icon in ("❓",):
            try:
                q_font = _load_font(rr * 2, prefer_emoji=False)
                draw.text((cx - rr // 2 - 2, cy - rr), "?", fill="white", font=q_font)
            except Exception:
                draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                             outline="white", width=2)
            return

        # ── Shield → Approval needed ──
        if icon in ("🛡️",):
            pts = [
                (cx, cy - r - 3),
                (cx + r + 3, cy - r // 2),
                (cx + r + 3, cy + r // 2),
                (cx, cy + r + 3),
                (cx - r - 3, cy + r // 2),
                (cx - r - 3, cy - r // 2),
            ]
            draw.polygon(pts, outline="white", width=2)
            return

        # ── Double bars → Paused ──
        if icon in ("⏸️", "⏸",):
            bar_w = max(3, r // 2)
            bar_h = r * 2
            gap = r // 2
            draw.rectangle([cx - gap - bar_w, cy - r, cx - gap, cy + r], fill="white")
            draw.rectangle([cx + gap, cy - r, cx + gap + bar_w, cy + r], fill="white")
            return

        # ── Star / sparkle → New / Starting ──
        if icon in ("🆕",):
            try:
                s_font = _load_font(rr * 2, prefer_emoji=False)
                draw.text((cx - rr, cy - rr), "+", fill="white", font=s_font)
            except Exception:
                draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                             outline="white", width=2)
            return

        # ── Fallback: try font glyph, then simple circle ──
        try:
            fallback_font = _load_font(rr * 2, prefer_emoji=True)
            bbox = draw.textbbox((0, 0), icon, font=fallback_font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            draw.text((cx - tw // 2, cy - th // 2), icon, fill="white", font=fallback_font)
        except Exception:
            draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                         outline="white", width=2)

    def _render_key_image(
        self,
        ws: WidgetState,
        size: tuple[int, int],
        now: float,
    ) -> Image.Image:
        """Render a fully composed key image for a widget.

        Pipeline:
        1. Base: sprite frame from AnimationEngine, or solid color fill
        2. Icon emoji centered (only on non-sprite base)
        3. Label text at bottom with dark background
        4. Badge pill in top-right corner
        5. Procedural effect applied on top (if active)

        Args:
            ws: The widget state with display and meta fields.
            size: (width, height) of the key in pixels.
            now: time.monotonic() for animation frame selection.

        Returns:
            PIL RGB Image ready for hardware push.
        """
        w, h = size
        display = ws.display

        # ── Step 1: Base image ─────────────────
        sprite_name = display.sprite if display.sprite else "none"
        sprite_frame = self._anim_engine.get_sprite_frame(ws.id, sprite_name, now, target_size=size)

        if sprite_frame is not None:
            # Use sprite frame as base, resize if needed
            if sprite_frame.size != size:
                img = sprite_frame.resize(size, Image.NEAREST)
            else:
                img = sprite_frame.copy()
            is_sprite = True
        else:
            # Solid color background
            bg_color = display.color.lstrip("#") if display.color else "000000"
            img = Image.new("RGB", size, f"#{bg_color}")
            is_sprite = False

        draw = ImageDraw.Draw(img)

        # ── Step 2: Icon (only on non-sprite base) ──
        # Render geometric shapes instead of emoji font glyphs —
        # PIL can't render color emoji (Segoe UI Emoji only exposes
        # monochrome outlines), so we draw clean PIL vector shapes.
        if not is_sprite and display.icon:
            _draw_icon_shape(draw, display.icon, w, h)

        # ── Step 3: Label at bottom ────────────
        if display.label:
            label_font_size = max(10, min(w // 8, 16))
            label_font = _load_font(label_font_size, prefer_emoji=False)
            lines = _split_text(display.label)
            y_offset = h - (len(lines) * (label_font_size + 2)) - 3
            for line in lines:
                try:
                    l_bbox = draw.textbbox((0, 0), line, font=label_font)
                except Exception:
                    l_bbox = (0, 0, label_font_size * len(line), label_font_size)
                l_w = l_bbox[2] - l_bbox[0]
                l_x = (w - l_w) // 2
                # Text shadow for readability (no heavy black rectangle)
                try:
                    draw.text((l_x + 1, y_offset + 1), line, fill=(0, 0, 0), font=label_font)
                except Exception:
                    pass
                try:
                    draw.text((l_x, y_offset), line, fill="white", font=label_font)
                except Exception:
                    pass
                y_offset += label_font_size + 2

        # ── Step 4: Badge in top-right ─────────
        if display.badge:
            badge_text = display.badge
            badge_size = min(w, h) // 4
            badge_font = _load_font(badge_size, prefer_emoji=False)
            try:
                b_bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
            except Exception:
                b_bbox = (0, 0, badge_size, badge_size)
            b_w = b_bbox[2] - b_bbox[0]
            b_h = b_bbox[3] - b_bbox[1]
            bx = w - b_w - 4
            by = 2
            # Badge pill background (red)
            draw.ellipse(
                [bx - 4, by - 1, bx + b_w + 4, by + b_h + 3],
                fill="#EF4444",
            )
            try:
                draw.text((bx, by), badge_text, fill="white", font=badge_font)
            except Exception:
                pass

        # ── Step 5: Procedural effect ──────────
        if display.animation in (
            AnimationType.PULSE,
            AnimationType.BLINK,
            AnimationType.CRAWL,
            AnimationType.PROGRESS,
        ):
            img = self._anim_engine.apply_effect(img, display.animation, now)

        return img

    # ── Frame Rendering ────────────────────────────

    def render_frame(self, frame: LayoutFrame) -> None:
        """Push a LayoutFrame to the hardware."""
        if not self._deck:
            return

        size = self.key_size
        now = _time_mod.monotonic()

        # Read native image format from the device.
        # Stream Deck XL returns {'format':'JPEG','flip':(True,True),'size':(96,96)}
        fmt_info: dict = (
            self._deck.key_image_format()
            if hasattr(self._deck, "key_image_format")
            else {}
        )
        want_format = fmt_info.get("format", "").upper()
        flip_h, flip_v = fmt_info.get("flip", (False, False))

        for i, widget_id in enumerate(frame.keymap):
            if i >= self.key_count:
                break

            if widget_id and widget_id in frame.widgets:
                ws = frame.widgets[widget_id]
                img = self._render_key_image(ws, size, now)
            else:
                img = Image.new("RGB", size, "#000000")

            # Diff check: skip if unchanged
            img_hash = hash(img.tobytes())
            if self._last_frame_hashes[i] == img_hash:
                continue
            self._last_frame_hashes[i] = img_hash

            # Resize if needed
            if img.size != size:
                img = img.resize(size, Image.LANCZOS)

            # Apply flips — some models (XL) need both axes flipped
            if flip_h:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            if flip_v:
                img = img.transpose(Image.FLIP_TOP_BOTTOM)

            # Convert to expected format and push
            if want_format == "JPEG":
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=94)
                data = buf.getvalue()
            else:
                data = img.tobytes()
            self._deck.set_key_image(i, data)

    def set_brightness(self, percent: int) -> None:
        """Set panel brightness (0-100)."""
        if self._deck:
            self._deck.set_brightness(max(0, min(100, percent)))

    def reset(self) -> None:
        """Hardware reset (returns to Elgato logo)."""
        if self._deck:
            self._deck.reset()

    # ── Hot-plug detection ────────────────────────

    async def hotplug_loop(self, poll_interval: float = 2.0) -> None:
        """
        Monitor for device connect/disconnect events.

        Call this as a background asyncio task.
        """
        was_connected = self.connected

        while True:
            await asyncio.sleep(poll_interval)
            is_connected = self.connected

            if was_connected and not is_connected:
                log.info("Stream Deck disconnected")
                was_connected = False

            elif not was_connected and is_connected:
                log.info("Stream Deck reconnected")
                was_connected = True

            # Try to reconnect if not connected
            if not self.connected:
                self.open()

    @staticmethod
    def discover() -> list[dict]:
        """List all connected Stream Deck devices."""
        if not HAS_DECK:
            return []

        decks = DeviceManager().enumerate()
        result = []
        for i, d in enumerate(decks):
            try:
                d.open()
                info = {
                    "index": i,
                    "type": d.deck_type(),
                    "serial": d.get_serial_number(),
                    "firmware": d.get_firmware_version(),
                    "key_count": d.key_count(),
                    "vendor_id": hex(d.vendor_id()),
                    "product_id": hex(d.product_id()),
                }
                d.close()
                result.append(info)
            except Exception:
                result.append({"index": i, "type": str(d), "error": True})
        return result
