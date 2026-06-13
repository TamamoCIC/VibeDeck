"""
Layer protocol — each Layer is an independent compositing unit.

Design principle: every Layer receives the same inputs and produces
an RGBA PIL Image (or None).  Layers are stateless and know nothing
about each other.  The Renderer stacks them in z-order.

See `docs/adr/0002-standard-frame-pipeline.md`.
"""

from __future__ import annotations

from typing import Optional, Protocol

from PIL import Image

from ...core.types import DisplayState


class Layer(Protocol):
    """A composable rendering module for one visual element of a key.

    Each ``render()`` call receives the current display state, the
    key pixel dimensions, and a ``time.monotonic()`` timestamp for
    animation frame selection.  It returns an RGBA image whose
    transparent pixels are see-through — the caller composites it
    on top of whatever is below in the stack.
    """

    name: str  # unique layer identifier (e.g. "icon", "label")

    def render(
        self,
        state: DisplayState,
        key_size: tuple[int, int],
        now: float,
    ) -> Optional[Image.Image]:
        """Produce this layer's contribution for one key.

        Returns:
            RGBA PIL Image, or None if the layer has nothing to show
            (e.g. no label text, no badge).
        """
        ...
