"""
VibeDeck Core Types — canonical data structures for the Widget State Protocol.

All types use Pydantic v2 for validation, serialization, and JSON Schema export.
These are the "type system" that all other modules depend on.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# Module-level mapping: Stream Deck model names → grid dimensions
_DECK_MODEL_SIZES: dict[str, tuple[int, int]] = {
    "Stream Deck Mini": (3, 2),
    "Stream Deck": (3, 5),
    "Stream Deck XL": (4, 8),
    "Stream Deck Neo": (2, 4),
    "Stream Deck Plus": (2, 4),
    "Stream Deck Pedal": (1, 3),
}


# ── Enums ────────────────────────────────────────


class WidgetType(str, Enum):
    """The kind of Widget."""
    AGENT = "agent"
    SYSTEM = "system"
    COMMAND = "command"
    APPROVAL = "approval"


class AnimationType(str, Enum):
    """Animation classes available for display."""
    NONE = "none"
    PULSE = "pulse"
    CRAWL = "crawl"
    BLINK = "blink"
    PROGRESS = "progress"


# ── Display State ────────────────────────────────


class DisplayState(BaseModel):
    """
    What a Widget looks like on a single key at a given moment.

    All fields are optional — the renderer applies sensible defaults
    for any missing values.
    """

    icon: str = Field(default="", description="Emoji or icon key name")
    color: str = Field(default="#000000", pattern=r"^#[0-9a-fA-F]{6}$", description="Background hex color")
    animation: AnimationType = Field(default=AnimationType.NONE, description="Key animation style")
    label: str = Field(default="", max_length=12, description="Short text overlay on the key")
    badge: Optional[str] = Field(default=None, description="Badge text (number or icon) in corner")
    sprite: str = Field(default="none", description="Sprite clip name for pixel-art animation (none = disabled)")

    model_config = {"json_schema_extra": {
        "examples": [{
            "icon": "🐙",
            "color": "#22c55e",
            "animation": "crawl",
            "label": "Running",
            "badge": None,
            "sprite": "mascot_walk",
        }]
    }}


# ── Widget State ─────────────────────────────────


class WidgetState(BaseModel):
    """
    A Widget's full state at a point in time.

    Represents one key on the Deck — its display, its type,
    and arbitrary metadata that the adapter may attach.
    """

    id: str = Field(description="Unique Widget identifier within a layout")
    type: WidgetType = Field(default=WidgetType.AGENT, description="Widget category")
    display: DisplayState = Field(default_factory=DisplayState, description="Current visual state")
    meta: dict = Field(default_factory=dict, description="Arbitrary adapter metadata")

    def update_display(
        self,
        icon: str | None = None,
        color: str | None = None,
        animation: str | None = None,
        label: str | None = None,
        badge: str | None = None,
    ) -> None:
        """Partially update display state (None = keep current value)."""
        updates = {}
        if icon is not None:
            updates["icon"] = icon
        if color is not None:
            updates["color"] = color
        if animation is not None:
            updates["animation"] = AnimationType(animation)
        if label is not None:
            updates["label"] = label
        if badge is not None:
            updates["badge"] = badge
        self.display = self.display.model_copy(update=updates)

    model_config = {"json_schema_extra": {
        "examples": [{
            "id": "claude-code-1",
            "type": "agent",
            "display": {
                "icon": "🐙",
                "color": "#22c55e",
                "animation": "crawl",
                "label": "Running",
                "badge": None,
            },
            "meta": {"agent": "Claude Code", "session_id": "abc123", "status": "running"},
        }]
    }}


# ── Layout Frame ─────────────────────────────────


class LayoutFrame(BaseModel):
    """
    Snapshot of a Terminal at one moment.

    Maps key indices to Widgets. This is the canonical
    representation pushed to all render targets (physical
    and virtual Terminals).
    """

    display_name: str = Field(default="4x8", description="Human-readable grid name (e.g. '4x8', '3x5')")
    rows: int = Field(gt=0, description="Number of key rows")
    cols: int = Field(gt=0, description="Number of key columns")
    widgets: dict[str, WidgetState] = Field(default_factory=dict, description="Widget ID → Widget State")
    keymap: list[Optional[str]] = Field(default_factory=list, description="Key index → Widget ID")

    @property
    def key_count(self) -> int:
        """Total number of physical keys."""
        return self.rows * self.cols

    def get_widget_at(self, key_index: int) -> Optional[WidgetState]:
        """Get the Widget at a physical key index, or None."""
        if key_index < 0 or key_index >= len(self.keymap):
            return None
        widget_id = self.keymap[key_index]
        if widget_id is None:
            return None
        return self.widgets.get(widget_id)

    def place_widget(self, widget: WidgetState, key_index: int) -> None:
        """Place or move a Widget to a specific key index."""
        # Remove from old position
        self.keymap = [None if wid == widget.id else wid for wid in self.keymap]
        # Ensure keymap is large enough
        while len(self.keymap) <= key_index:
            self.keymap.append(None)
        # Place
        self.keymap[key_index] = widget.id
        self.widgets[widget.id] = widget

    def first_empty_key(self) -> int | None:
        """Return the index of the first empty key slot, or None if full."""
        for i, wid in enumerate(self.keymap):
            if wid is None:
                return i
        return None

    def remove_widget(self, widget_id: str) -> None:
        """Remove a Widget from the layout."""
        self.keymap = [None if wid == widget_id else wid for wid in self.keymap]
        self.widgets.pop(widget_id, None)

    @classmethod
    def for_grid(cls, rows: int, cols: int, display_name: str | None = None) -> "LayoutFrame":
        """Create an empty frame for a grid of given dimensions."""
        name = display_name or f"{rows}x{cols}"
        return cls(
            display_name=name,
            rows=rows,
            cols=cols,
            keymap=[None] * (rows * cols),
        )

    @classmethod
    def for_deck(cls, deck_type: str) -> "LayoutFrame":
        """Create an empty frame for a specific Stream Deck model (legacy).

        Prefer for_grid() for new code. This is retained for hardware auto-detection.
        """
        rows, cols = _DECK_MODEL_SIZES.get(deck_type, (3, 5))
        return cls.for_grid(rows, cols, display_name=deck_type)

    def to_yaml(self, path: str) -> None:
        """Write the frame to a YAML layout file."""
        import yaml

        widgets_raw = []
        for i, wid in enumerate(self.keymap):
            if wid is None:
                continue
            ws = self.widgets[wid]
            widgets_raw.append({
                "id": ws.id,
                "type": ws.type.value,
                "key_index": i,
                "icon": ws.display.icon,
                "color": ws.display.color,
                "animation": ws.display.animation.value,
                "label": ws.display.label,
                "badge": ws.display.badge,
                "meta": ws.meta,
            })

        d = {
            "name": "untitled",
            "display_name": self.display_name,
            "rows": self.rows,
            "cols": self.cols,
            "widgets": widgets_raw,
        }

        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(d, f, default_flow_style=False, allow_unicode=True, indent=2)

    @classmethod
    def from_yaml(cls, path: str) -> "LayoutFrame":
        """Load a LayoutFrame from a YAML layout file.

        Supports both legacy format (deck_type) and new format (display_name + rows + cols).
        """
        import yaml

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)

        # Handle legacy format: deck_type used to imply grid dimensions
        display_name = raw.get("display_name") or raw.get("deck_type", "4x8")
        rows, cols = raw.get("rows"), raw.get("cols")
        if rows is None or cols is None:
            rows, cols = _DECK_MODEL_SIZES.get(display_name, (3, 5))

        frame = cls(
            display_name=display_name,
            rows=rows,
            cols=cols,
            keymap=[None] * (rows * cols),
        )

        for w_raw in raw.get("widgets", []):
            ws = WidgetState(
                id=w_raw["id"],
                type=WidgetType(w_raw.get("type", "agent")),
                display=DisplayState(
                    icon=w_raw.get("icon", ""),
                    color=w_raw.get("color", "#000000"),
                    animation=AnimationType(w_raw.get("animation", "none")),
                    label=w_raw.get("label", ""),
                    badge=w_raw.get("badge"),
                ),
                meta=w_raw.get("meta", {}),
            )
            key = w_raw.get("key_index")
            if key is not None and key < frame.key_count:
                frame.place_widget(ws, key)

        return frame

    model_config = {"json_schema_extra": {
        "examples": [{
            "display_name": "4x8",
            "rows": 4,
            "cols": 8,
            "widgets": {},
            "keymap": [None] * 32,
        }]
    }}
