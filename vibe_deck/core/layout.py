"""
VibeDeck Layout Engine — owns layout state and recomputes LayoutFrames.

Consumes Widget state updates from the MessageBus, maintains the current
LayoutFrame snapshot, and publishes frame updates when Widget states change.
"""

from __future__ import annotations

import logging

from .types import DisplayState, LayoutFrame, WidgetState

log = logging.getLogger("vibe_deck.core.layout")


class LayoutEngine:
    """
    Manages the current layout and produces LayoutFrames.

    Consumes Widget state updates from the MessageBus, recomputes the
    frame, and publishes it for Render targets.
    """

    def __init__(self, display_name: str = "4x8", rows: int = 4, cols: int = 8) -> None:
        self._frame = LayoutFrame.for_grid(rows, cols, display_name)
        self._layout_name: str | None = None

    @property
    def frame(self) -> LayoutFrame:
        return self._frame

    @property
    def layout_name(self) -> str | None:
        return self._layout_name

    def update_widget(self, widget: WidgetState) -> LayoutFrame:
        """Update or add a Widget in the current frame. Returns the new frame."""
        existing = self._frame.widgets.get(widget.id)
        if existing:
            existing.display = widget.display
            existing.meta.update(widget.meta)
            if existing.type != widget.type:
                existing.type = widget.type
        else:
            self._frame.widgets[widget.id] = widget
        return self._frame

    def load_layout(self, path: str) -> LayoutFrame:
        """Load a YAML layout file and replace the current frame."""
        self._frame = LayoutFrame.from_yaml(path)
        self._layout_name = path
        return self._frame

    def save_layout(self, path: str) -> None:
        """Write the current frame to a YAML layout file."""
        self._frame.to_yaml(path)
        self._layout_name = path
