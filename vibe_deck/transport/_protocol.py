"""Transport protocol — thin adapter from StandardFrame to device."""

from __future__ import annotations

from typing import Protocol

from ..render.standard_frame import StandardFrame


class Transport(Protocol):
    """Push a StandardFrame to an output device.

    A Transport is intentionally thin — it does NOT render text, draw
    shapes, or composite layers.  Those responsibilities belong to the
    PIL Renderer.  The Transport only handles device-specific encoding
    differences (e.g. image flipping for Stream Deck XL) and delivery
    (USB HID, SSE, WebSocket).
    """

    def push(self, frame: StandardFrame) -> None:
        """Deliver a frame to the device."""
        ...

    def close(self) -> None:
        """Release the device."""
        ...
