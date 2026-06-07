"""
VibeDeck Layout Engine — owns layout state and recomputes LayoutFrames.

Loads YAML layout files from ~/.vibe-deck/layouts/, maintains the current
LayoutFrame snapshot, and publishes frame updates when Widget states change.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DisplayState:
    """What a Widget looks like right now."""

    icon: str = ""              # emoji or icon key
    color: str = "#000000"      # hex background color
    animation: str = "none"     # "none" | "pulse" | "crawl" | "blink" | "progress"
    label: str = ""             # short text overlay (max 12 chars)
    badge: str | None = None    # optional numeric/icon badge


@dataclass
class WidgetState:
    """A Widget's full state at a point in time."""

    id: str
    type: str = "agent"  # "agent" | "system" | "command" | "approval"
    display: DisplayState = field(default_factory=DisplayState)
    meta: dict = field(default_factory=dict)

    def update_display(
        self,
        icon: str | None = None,
        color: str | None = None,
        animation: str | None = None,
        label: str | None = None,
        badge: str | None = None,
    ) -> None:
        """Partially update the display state (None = keep current)."""
        if icon is not None:
            self.display.icon = icon
        if color is not None:
            self.display.color = color
        if animation is not None:
            self.display.animation = animation
        if label is not None:
            self.display.label = label
        if badge is not None:
            self.display.badge = badge


@dataclass
class LayoutFrame:
    """Snapshot of the entire Deck at one moment."""

    deck_type: str = "Stream Deck XL"
    rows: int = 4
    cols: int = 8
    widgets: dict[str, WidgetState] = field(default_factory=dict)
    keymap: list[str | None] = field(default_factory=list)  # index → widget_id

    def get_widget_at(self, key_index: int) -> WidgetState | None:
        """Get the Widget at a physical key index."""
        if key_index < 0 or key_index >= len(self.keymap):
            return None
        widget_id = self.keymap[key_index]
        if widget_id is None:
            return None
        return self.widgets.get(widget_id)

    def place_widget(self, widget: WidgetState, key_index: int) -> None:
        """Place or move a Widget to a specific key."""
        # Remove from old position
        self.keymap = [None if wid == widget.id else wid for wid in self.keymap]
        # Ensure keymap is large enough
        while len(self.keymap) <= key_index:
            self.keymap.append(None)
        # Place at new position
        self.keymap[key_index] = widget.id
        self.widgets[widget.id] = widget

    def remove_widget(self, widget_id: str) -> None:
        """Remove a Widget from the layout."""
        self.keymap = [None if wid == widget_id else wid for wid in self.keymap]
        self.widgets.pop(widget_id, None)

    @classmethod
    def for_deck(cls, deck_type: str) -> "LayoutFrame":
        """Create an empty frame for a specific device."""
        sizes = {
            "Stream Deck Mini": (3, 2),
            "Stream Deck": (3, 5),
            "Stream Deck XL": (4, 8),
            "Stream Deck Neo": (2, 4),
            "Stream Deck Plus": (2, 4),
            "Stream Deck Pedal": (1, 3),
        }
        rows, cols = sizes.get(deck_type, (3, 5))
        return cls(
            deck_type=deck_type,
            rows=rows,
            cols=cols,
            keymap=[None] * (rows * cols),
        )


class LayoutEngine:
    """
    Manages the current layout and produces LayoutFrames.

    Consumes Widget state updates from the MessageBus, recomputes the
    frame, and publishes it for Render targets.
    """

    def __init__(self, deck_type: str = "Stream Deck XL") -> None:
        self._frame = LayoutFrame.for_deck(deck_type)
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
        import yaml
        with open(path) as f:
            raw = yaml.safe_load(f)

        deck_type = raw.get("deck_type", "Stream Deck XL")
        self._frame = LayoutFrame.for_deck(deck_type)
        self._layout_name = raw.get("name", path)

        # Restore widgets from YAML
        for w_raw in raw.get("widgets", []):
            ws = WidgetState(
                id=w_raw["id"],
                type=w_raw.get("type", "agent"),
                display=DisplayState(
                    icon=w_raw.get("icon", ""),
                    color=w_raw.get("color", "#000000"),
                    animation=w_raw.get("animation", "none"),
                    label=w_raw.get("label", ""),
                    badge=w_raw.get("badge"),
                ),
                meta=w_raw.get("meta", {}),
            )
            key = w_raw.get("key_index")
            if key is not None and key < len(self._frame.keymap):
                self._frame.place_widget(ws, key)

        return self._frame

    def save_layout(self, path: str) -> None:
        """Write the current frame to a YAML layout file."""
        import yaml

        widgets_raw = []
        for i, wid in enumerate(self._frame.keymap):
            if wid is None:
                continue
            ws = self._frame.widgets[wid]
            widgets_raw.append({
                "id": ws.id,
                "type": ws.type,
                "key_index": i,
                "icon": ws.display.icon,
                "color": ws.display.color,
                "animation": ws.display.animation,
                "label": ws.display.label,
                "badge": ws.display.badge,
                "meta": ws.meta,
            })

        d = {
            "name": self._layout_name or "untitled",
            "deck_type": self._frame.deck_type,
            "rows": self._frame.rows,
            "cols": self._frame.cols,
            "widgets": widgets_raw,
        }

        with open(path, "w") as f:
            yaml.safe_dump(d, f, default_flow_style=False, allow_unicode=True, indent=2)
