"""
VibeDeck Core — Stream Deck device abstraction layer.

Wraps the community-maintained python-elgato-streamdeck library
(https://github.com/abcminiuser/python-elgato-streamdeck) into a
VibeDeck-friendly controller.

Install Stream Deck support:
    pip install vibe-deck[deck]
"""

from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Callable, Optional

from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger("vibe_deck")

try:
    from StreamDeck.DeviceManager import DeviceManager
    from StreamDeck.Devices.StreamDeck import StreamDeck as _SDBase

    HAS_DECK = True
except ImportError:
    DeviceManager = None  # type: ignore
    _SDBase = None  # type: ignore
    HAS_DECK = False


# ── Exceptions ────────────────────────────────


class VibeDeckError(Exception):
    """Base exception for VibeDeck."""


class NoDeviceFound(VibeDeckError):
    """No Stream Deck hardware detected."""


class DeckNotConnected(VibeDeckError):
    """The deck is not connected or has been disconnected."""


# ── Controller ────────────────────────────────

KEY_SIZE = {
    "Stream Deck XL": (96, 96),
    "Stream Deck": (72, 72),
    "Stream Deck Mini": (80, 80),
    "Stream Deck Neo": (80, 80),
    "Stream Deck Plus": (100, 100),
    "Stream Deck Pedal": (0, 0),  # no screen
}


class DeckController:
    """
    High-level controller for a single Elgato Stream Deck device.

    Basic usage::

        with DeckController() as deck:
            deck.set_brightness(80)
            deck.set_key_image(0, Image.new("RGB", (96, 96), "blue"))
            for event in deck.listen():
                print(f"Key {event.key} {'pressed' if event.pressed else 'released'}")

    Tip: use the context manager (``with``) to auto-close the device.
    """

    def __init__(self, device_index: int = 0) -> None:
        if not HAS_DECK:
            raise VibeDeckError(
                "Stream Deck library not installed. "
                "Run: pip install vibe-deck[deck]"
            )
        self._device_index = device_index
        self._deck: Optional["_SDBase"] = None

    # ── Properties ────────────────────────────

    @property
    def deck(self) -> "_SDBase":
        """The underlying StreamDeck device (raises if not connected)."""
        if self._deck is None:
            raise DeckNotConnected("Deck not opened. Call .open() first.")
        return self._deck

    @property
    def key_count(self) -> int:
        """Number of physical keys on this device."""
        return self.deck.key_count()

    @property
    def key_size(self) -> tuple[int, int]:
        """(width, height) in pixels for a single key image."""
        return KEY_SIZE.get(self.deck.deck_type(), (72, 72))

    @property
    def deck_type(self) -> str:
        """Human-readable device type (e.g. 'Stream Deck XL')."""
        return self.deck.deck_type()

    @property
    def serial(self) -> str:
        """Device serial number."""
        return self.deck.get_serial_number()

    @property
    def firmware(self) -> str:
        """Firmware version string."""
        return self.deck.get_firmware_version()

    # ── Lifecycle ─────────────────────────────

    def open(self) -> "DeckController":
        """Discover and open the Stream Deck device."""
        decks = DeviceManager().enumerate()
        if not decks:
            raise NoDeviceFound(
                "No Stream Deck hardware detected. "
                "Check USB connection and udev rules."
            )
        if self._device_index >= len(decks):
            raise NoDeviceFound(
                f"Device index {self._device_index} out of range "
                f"(found {len(decks)} device(s))."
            )

        self._deck = decks[self._device_index]
        self._deck.open()
        self._deck.reset()
        log.info("Opened %s (serial=%s)", self.deck_type, self.serial)
        return self

    def close(self) -> None:
        """Close and reset the deck."""
        if self._deck is not None:
            try:
                self._deck.reset()
                self._deck.close()
            except Exception:
                pass
            self._deck = None
            log.info("Deck closed")

    def __enter__(self) -> "DeckController":
        return self.open()

    def __exit__(self, *args) -> None:
        self.close()

    # ── Display ───────────────────────────────

    def set_brightness(self, percent: int) -> None:
        """Set panel brightness (0-100)."""
        self.deck.set_brightness(max(0, min(100, percent)))

    def set_key_image(self, key: int, image: Image.Image) -> None:
        """
        Set a single key's image.

        Args:
            key: Zero-based key index.
            image: PIL Image in RGB mode. Will be auto-resized to match
                   the device's key dimensions.
        """
        size = self.key_size
        if image.size != size:
            image = image.resize(size, Image.LANCZOS)

        native = self.deck.key_image_format()
        native.convert(image)
        self.deck.set_key_image(key, image.tobytes() if hasattr(image, "tobytes") else image.tobytes())

    def set_key_color(self, key: int, color: str | tuple[int, int, int]) -> None:
        """Fill a key with a solid color."""
        if isinstance(color, str):
            fill = color
        else:
            fill = tuple(color)
        img = Image.new("RGB", self.key_size, fill)
        self.set_key_image(key, img)

    def set_key_label(self, key: int, text: str, fg: str = "white", bg: str = "black") -> None:
        """Draw text on a key with auto-sized font."""
        size = self.key_size
        img = Image.new("RGB", size, bg)
        draw = ImageDraw.Draw(img)

        # Auto-size font to fit
        font_size = max(10, min(size) // max(len(text) // 2, 1))
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", font_size)
        except (IOError, OSError):
            font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        x = (size[0] - tw) // 2
        y = (size[1] - th) // 2
        draw.text((x, y), text, fill=fg, font=font)
        self.set_key_image(key, img)

    def clear_all(self) -> None:
        """Reset all keys to black."""
        for k in range(self.key_count):
            self.set_key_color(k, "black")

    def reset(self) -> None:
        """Hardware reset (returns to Elgato logo)."""
        self.deck.reset()

    # ── Events ────────────────────────────────

    class KeyEvent:
        """Represents a single key press or release."""

        def __init__(self, deck: "DeckController", key: int, pressed: bool) -> None:
            self.deck = deck
            self.key = key
            self.pressed = pressed

    def listen(
        self,
        callback: Optional[Callable[["KeyEvent"], None]] = None,
    ) -> None:
        """
        Block and listen for key events.

        If *callback* is provided, each event is dispatched to it.
        Otherwise events are yielded to the caller (generator mode).

        Generator mode example::

            for event in deck.listen():
                if event.pressed:
                    deck.set_key_color(event.key, "green")
        """
        if callback is not None:
            self.deck.set_key_callback(
                lambda d, k, s: callback(self.KeyEvent(self, k, bool(s)))
            )
            try:
                while True:
                    import time
                    time.sleep(1)
            except KeyboardInterrupt:
                pass
        else:
            import queue
            q: queue.Queue = queue.Queue()

            self.deck.set_key_callback(
                lambda d, k, s: q.put(self.KeyEvent(self, k, bool(s)))
            )

            try:
                while True:
                    yield q.get()
            except KeyboardInterrupt:
                pass

    # ── Discovery (static) ─────────────────────

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
