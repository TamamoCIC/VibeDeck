"""
Hardware Render Engine — drives a real Elgato Stream Deck.

Consumes LayoutFrame snapshots and renders them onto physical hardware.
Supports hot-plug detection and auto-recovery.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from PIL import Image

from ..core.layout import LayoutFrame

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


class HardwareRenderer:
    """
    Renders LayoutFrames onto a physical Elgato Stream Deck.

    Features:
    - Frame diffing: only updates keys that changed
    - Hot-plug recovery: detects USB disconnect/reconnect
    - Animation engine: frame-swapping for crawl/pulse effects
    """

    def __init__(self, device_index: int = 0) -> None:
        self._device_index = device_index
        self._deck: Optional["_SDBase"] = None
        self._last_frame_hashes: list[int | None] = []
        self._key_callback: Optional[callable] = None

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
        # Map known models to their grid
        _grid_map = {
            "Stream Deck XL": "4x8",
            "Stream Deck": "3x5",
            "Stream Deck Mini": "3x2",
            "Stream Deck Neo": "2x4",
            "Stream Deck Plus": "2x4",
            "Stream Deck Pedal": "1x3",
        }
        return _grid_map.get(kt, f"{self.key_count}keys")

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

    # ── Rendering ─────────────────────────────────

    def render_frame(self, frame: LayoutFrame) -> None:
        """Push a LayoutFrame to the hardware."""
        if not self._deck:
            return

        size = self.key_size
        native_fmt = self._deck.key_image_format() if hasattr(self._deck, "key_image_format") else None

        for i, widget_id in enumerate(frame.keymap):
            if i >= self.key_count:
                break

            # Build PIL image from frame data
            # For hardware, we generate a simple image based on the Widget
            if widget_id and widget_id in frame.widgets:
                ws = frame.widgets[widget_id]
                color = ws.display.color.lstrip("#") if ws.display.color else "000000"
                img = Image.new("RGB", size, f"#{color}")
                # Future: render text/icon overlay. For now, solid color.
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

            # Convert to native format and push
            if native_fmt:
                native_fmt.convert(img)
                self._deck.set_key_image(i, img.tobytes())
            else:
                self._deck.set_key_image(i, img.tobytes() if hasattr(img, "tobytes") else img.tobytes())

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
