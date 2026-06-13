"""
Standard Frame — device-independent intermediate frame format.

A ``StandardFrame`` is a fully-rendered snapshot of a Terminal at one
moment.  Every key holds both a JPEG and a PNG encoding of its fully
composited image (color + icon + label + badge + effects).  It is the
universal currency between the PIL Renderer and all Transports.

See `docs/adr/0002-standard-frame-pipeline.md`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class KeyImage:
    """One fully-rendered key in a StandardFrame.

    ``jpeg`` is for embedded / HID devices (Stream Deck firmware
    expects JPEG).  ``png`` is for web / high-quality consumers.
    """

    index: int
    widget_id: str | None = None
    jpeg: bytes = b""
    png: bytes = b""


@dataclass
class StandardFrame:
    """Device-independent frame — ready-to-push image bytes.

    Carries enough metadata for Transports to deliver it correctly
    (grid size, key pixel dimensions) without having to decode the
    image bytes themselves.
    """

    grid: tuple[int, int]  # (rows, cols)
    key_size: tuple[int, int]  # (width, height) in pixels
    keys: list[KeyImage] = field(default_factory=list)

    @property
    def key_count(self) -> int:
        return self.grid[0] * self.grid[1]

    @classmethod
    def for_grid(cls, rows: int, cols: int, key_size: tuple[int, int]) -> "StandardFrame":
        """Create an empty frame for the given grid."""
        keys = [KeyImage(index=i) for i in range(rows * cols)]
        return cls(grid=(rows, cols), key_size=key_size, keys=keys)
