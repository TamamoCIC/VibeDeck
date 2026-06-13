"""Backdrop layer — solid color fill."""

from __future__ import annotations

from PIL import Image

from ...core.types import DisplayState
from ._protocol import Layer


class BackdropLayer:
    """Fill the key with the Widget's declared background color."""

    name = "backdrop"

    def render(
        self, state: DisplayState, key_size: tuple[int, int], now: float
    ) -> Image.Image:
        w, h = key_size
        bg = state.color.lstrip("#") if state.color else "000000"
        return Image.new("RGBA", (w, h), f"#{bg}FF")
