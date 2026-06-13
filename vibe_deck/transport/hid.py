"""
HID Transport — push StandardFrame JPEG bytes to a physical Stream Deck.

Handles device-specific concerns (flip, key callback threading, hot-plug)
but does NO rendering — it only pushes the pre-encoded JPEG bytes from
the StandardFrame.
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO
from typing import Optional

from PIL import Image

from ..render.standard_frame import StandardFrame
from ._protocol import Transport

log = logging.getLogger("vibe_deck.transport.hid")

try:
    from StreamDeck.DeviceManager import DeviceManager
    from StreamDeck.Devices.StreamDeck import StreamDeck as _SDBase

    HAS_DECK = True
except ImportError:
    DeviceManager = None  # type: ignore
    _SDBase = None  # type: ignore
    HAS_DECK = False


class HIDTransport:
    """Deliver StandardFrame JPEG bytes to an Elgato Stream Deck via USB HID.

    Features:
    - Device-specific image flipping (required by some Stream Deck models)
    - Frame diffing inside the transport (not in StandardFrame)
    - Thread-safe key callback → asyncio message bus bridge
    - Hot-plug detection and auto-recovery
    """

    def __init__(
        self,
        device_index: int = 0,
        key_callback: Optional[callable] = None,
    ) -> None:
        self._device_index = device_index
        self._deck: Optional["_SDBase"] = None
        self._last_frame_hashes: list[int | None] = []
        self._key_callback = key_callback

    # ── Properties ──────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._deck is not None

    @property
    def deck_type(self) -> str:
        if not self._deck:
            return "unknown"
        return self._deck.deck_type()

    @property
    def key_count(self) -> int:
        if not self._deck:
            return 0
        return self._deck.key_count()

    # ── Lifecycle ───────────────────────────────────

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

            if self._key_callback:
                self._deck.set_key_callback(
                    lambda d, k, s: self._key_callback(k, bool(s))
                )

            log.info("Opened %s (serial=%s)", self.deck_type,
                     self._deck.get_serial_number())
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

    # ── Transport.push ──────────────────────────────

    def push(self, frame: StandardFrame) -> None:
        """Push a StandardFrame to the hardware.

        For each key, extract the JPEG bytes, apply model-specific
        flips, and send via HID.  Keys that are unchanged from the
        last frame are skipped (transport-side diff).
        """
        if not self._deck:
            return

        # Read device-specific format requirements
        fmt_info: dict = (
            self._deck.key_image_format()
            if hasattr(self._deck, "key_image_format")
            else {}
        )
        flip_h, flip_v = fmt_info.get("flip", (False, False))

        for key_img in frame.keys:
            i = key_img.index
            if i >= self.key_count:
                break

            data = key_img.jpeg
            if not data:
                continue

            # Transport-side diff: skip unchanged keys
            img_hash = hash(data)
            if self._last_frame_hashes[i] == img_hash:
                continue
            self._last_frame_hashes[i] = img_hash

            # Apply model-specific flips
            if flip_h or flip_v:
                img = Image.open(BytesIO(data))
                if flip_h:
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                if flip_v:
                    img = img.transpose(Image.FLIP_TOP_BOTTOM)
                buf = BytesIO()
                img.save(buf, format="JPEG", quality=94)
                data = buf.getvalue()

            self._deck.set_key_image(i, data)

    def set_brightness(self, percent: int) -> None:
        """Set panel brightness (0-100)."""
        if self._deck:
            self._deck.set_brightness(max(0, min(100, percent)))

    # ── Hot-plug ────────────────────────────────────

    async def hotplug_loop(self, poll_interval: float = 2.0) -> None:
        """Monitor for device connect/disconnect events.

        Call this as a background asyncio task.
        """
        while True:
            await asyncio.sleep(poll_interval)
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
