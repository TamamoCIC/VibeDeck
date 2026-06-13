"""Sprite layer — pixel-art animation clip playback."""

from __future__ import annotations

from typing import Optional

from PIL import Image

from ...core.types import DisplayState
from ._protocol import Layer


class SpriteLayer:
    """Return the current sprite animation frame for the key.

    If the widget has a sprite clip active, this layer returns the
    current frame.  When active, it replaces the backdrop + icon —
    the Renderer suppresses those lower layers when the sprite layer
    produces output.
    """

    name = "sprite"

    def __init__(self, animation_engine: "AnimationEngine") -> None:
        from ..animation import AnimationEngine as AE

        self._engine: AE = animation_engine or AE()

    def render(
        self, state: DisplayState, key_size: tuple[int, int], now: float
    ) -> Optional[Image.Image]:
        sprite_name = state.sprite if state.sprite else "none"
        if sprite_name == "none":
            return None

        frame = self._engine.get_sprite_frame(
            "_sprite", sprite_name, now, target_size=key_size,
        )
        if frame is None:
            return None

        return frame.convert("RGBA") if frame.mode != "RGBA" else frame
