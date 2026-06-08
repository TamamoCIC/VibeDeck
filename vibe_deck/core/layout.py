"""
VibeDeck Layout Engine — owns layout state and recomputes LayoutFrames.

Consumes Widget state updates from the MessageBus, maintains one
LayoutFrame per connected Terminal, and publishes frame updates
when Widget states change.
"""

from __future__ import annotations

import logging

from .types import DisplayState, LayoutFrame, WidgetState

log = logging.getLogger("vibe_deck.core.layout")

DEFAULT_TERMINAL = "default"


class LayoutEngine:
    """
    Manages per-terminal layouts and produces LayoutFrames.

    Each connected Terminal (physical or virtual) gets its own
    LayoutFrame, registered via register_terminal(). The engine
    routes Widget updates to the correct frame based on terminal_id.

    For backward compatibility, callers that don't specify a
    terminal_id target the "default" terminal.
    """

    def __init__(self, display_name: str = "4x8", rows: int = 4, cols: int = 8) -> None:
        self._frames: dict[str, LayoutFrame] = {}
        self._layout_name: str | None = None
        # Auto-register the default terminal
        self.register_terminal(DEFAULT_TERMINAL, rows, cols, display_name)

    # ── Properties ────────────────────────────────

    @property
    def frame(self) -> LayoutFrame:
        """The default terminal's frame (backward compat)."""
        return self._frames[DEFAULT_TERMINAL]

    @property
    def layout_name(self) -> str | None:
        return self._layout_name

    # ── Terminal management ───────────────────────

    def register_terminal(
        self, terminal_id: str, rows: int, cols: int, display_name: str | None = None
    ) -> LayoutFrame:
        """Create a new LayoutFrame for a Terminal.

        If the terminal already exists, returns the existing frame
        (does not overwrite).
        """
        if terminal_id in self._frames:
            log.debug("Terminal %r already registered, reusing", terminal_id)
            return self._frames[terminal_id]

        frame = LayoutFrame.for_grid(rows, cols, display_name or f"{rows}x{cols}")
        self._frames[terminal_id] = frame
        log.info("Terminal %r registered (%dx%d)", terminal_id, rows, cols)
        return frame

    def unregister_terminal(self, terminal_id: str) -> None:
        """Remove a Terminal and its LayoutFrame."""
        if terminal_id == DEFAULT_TERMINAL:
            log.warning("Cannot unregister the default terminal; clearing instead")
            self._frames[DEFAULT_TERMINAL] = LayoutFrame.for_grid(4, 8, "4x8")
            return
        self._frames.pop(terminal_id, None)
        log.info("Terminal %r unregistered", terminal_id)

    def get_frame(self, terminal_id: str) -> LayoutFrame | None:
        """Get the LayoutFrame for a specific terminal, or None."""
        return self._frames.get(terminal_id)

    def list_terminals(self) -> list[str]:
        """Return all registered terminal IDs."""
        return list(self._frames.keys())

    # ── Widget operations ─────────────────────────

    def update_widget(
        self, widget: WidgetState, terminal_id: str = DEFAULT_TERMINAL
    ) -> LayoutFrame | None:
        """Update or add a Widget in the specified terminal's frame.

        Returns the updated frame, or None if the terminal is unknown.
        """
        frame = self._frames.get(terminal_id)
        if frame is None:
            log.warning("update_widget: unknown terminal %r", terminal_id)
            return None

        existing = frame.widgets.get(widget.id)
        if existing:
            existing.display = widget.display
            existing.meta.update(widget.meta)
            if existing.type != widget.type:
                existing.type = widget.type
        else:
            frame.widgets[widget.id] = widget
        return frame

    def remove_widget(self, widget_id: str, terminal_id: str = DEFAULT_TERMINAL) -> None:
        """Remove a Widget from a terminal's frame."""
        frame = self._frames.get(terminal_id)
        if frame is None:
            return
        frame.remove_widget(widget_id)

    # ── Layout persistence ────────────────────────

    def load_layout(self, path: str, terminal_id: str = DEFAULT_TERMINAL) -> LayoutFrame | None:
        """Load a YAML layout file into a terminal's frame."""
        frame = LayoutFrame.from_yaml(path)
        self._frames[terminal_id] = frame
        self._layout_name = path
        return frame

    def save_layout(self, path: str, terminal_id: str = DEFAULT_TERMINAL) -> None:
        """Write a terminal's current frame to a YAML layout file."""
        frame = self._frames.get(terminal_id)
        if frame is None:
            raise ValueError(f"Unknown terminal: {terminal_id!r}")
        frame.to_yaml(path)
        self._layout_name = path
