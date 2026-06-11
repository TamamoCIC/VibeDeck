"""
VibeDeck Layout Engine — owns layout state and recomputes LayoutFrames.

Architecture
------------
The engine models two distinct concepts:

  Terminal  — a physical or virtual device with a fixed grid (rows×cols).
              Each Terminal can hold multiple named Layouts, exactly one
              of which is *active* at any time.

  Layout    — a named arrangement of Widgets on a Terminal's grid.
              Layouts are persisted as YAML files.  The active layout
              is what gets pushed to the render target (Stream Deck,
              phone simulator, etc.).

This replaces the pre-refactor model where "Terminal" and "LayoutFrame"
were 1:1 — a terminal WAS its only frame, and widget updates were
broadcast to every terminal (causing state duplication).

Consumes Widget state updates from the MessageBus, maintains
LayoutFrames per Terminal, and publishes frame updates when Widget
states change.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from .types import DisplayState, LayoutFrame, WidgetState

log = logging.getLogger("vibe_deck.core.layout")

DEFAULT_TERMINAL = "default"
ACTIVE_LAYOUT = "_active"   # key for the currently-active frame (not user-facing)
AUTOSAVE_PREFIX = "_autosave"

# Offline display state used when restoring agent widgets from autosave
_OFFLINE_DISPLAY = {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"}


class LayoutEngine:
    """
    Manages Terminals, each with multiple named Layouts.

    Data model::

        _terminals = {
            "default": {
                "Daily Driver": LayoutFrame(4×8, widgets={...}),
                "Minimal":     LayoutFrame(4×8, widgets={...}),
                "_active":     LayoutFrame(4×8, ...),   # currently active
            },
            "phone-01": { ... },
        }

    The "_active" key always points to the currently-displayed frame.
    ``.frame`` and ``.get_frame()`` return the active frame.

    Widget updates are routed to a specific terminal.  An auxiliary
    method ``update_widget_across_terminals`` updates the widget on
    every terminal that *already* has it — but never auto-creates,
    avoiding the duplication that the broadcast patches caused.
    """

    def __init__(self, display_name: str = "4x8", rows: int = 4, cols: int = 8) -> None:
        # terminal_id → {layout_name → LayoutFrame}
        self._terminals: dict[str, dict[str, LayoutFrame]] = {}
        self._layout_name: str | None = None
        # Widget Pool — all known widgets, regardless of terminal placement
        self._pool: dict[str, WidgetState] = {}
        # Auto-register the default terminal
        self.register_terminal(DEFAULT_TERMINAL, rows, cols, display_name)

    # ── Properties ────────────────────────────────

    @property
    def frame(self) -> LayoutFrame:
        """The default terminal's active frame (backward compat)."""
        return self._terminals[DEFAULT_TERMINAL][ACTIVE_LAYOUT]

    @property
    def layout_name(self) -> str | None:
        return self._layout_name

    # ── Internal helpers ──────────────────────────

    def _active_frame(self, terminal_id: str) -> LayoutFrame:
        """Return the active frame for *terminal_id* (KeyError if unknown)."""
        return self._terminals[terminal_id][ACTIVE_LAYOUT]

    def _set_active(self, terminal_id: str, frame: LayoutFrame) -> None:
        """Replace the active frame for *terminal_id*."""
        self._terminals[terminal_id][ACTIVE_LAYOUT] = frame

    # ── Terminal management ───────────────────────

    def register_terminal(
        self, terminal_id: str, rows: int, cols: int, display_name: str | None = None
    ) -> LayoutFrame:
        """Register a Terminal and return its active LayoutFrame.

        If the terminal already exists the existing active frame is
        returned (the call is idempotent — dimensions are NOT changed).

        On first registration the engine tries to restore state from
        the autosave file ``~/.vibe-deck/layouts/_autosave-<id>.yaml``.
        Agent widgets restored this way are reset to *offline* to avoid
        showing stale Running/Thinking states.
        """
        if terminal_id in self._terminals:
            log.debug("Terminal %r already registered, reusing", terminal_id)
            return self._active_frame(terminal_id)

        from ..config import LAYOUTS_DIR

        # Try to restore from autosave
        autosave_path = LAYOUTS_DIR / f"{AUTOSAVE_PREFIX}-{terminal_id}.yaml"
        frame = None
        if autosave_path.exists():
            try:
                frame = LayoutFrame.from_yaml(str(autosave_path))
                # Reset agent widgets to offline — avoid stale Running/Thinking
                for ws in frame.widgets.values():
                    if ws.type.value == "agent":
                        ws.display = DisplayState(**_OFFLINE_DISPLAY)
                log.info("Terminal %r restored from autosave (%d widgets, %s)",
                         terminal_id, len(frame.widgets), autosave_path.name)
            except Exception:
                log.warning("Failed to load autosave for %r, starting fresh", terminal_id,
                            exc_info=True)

        if frame is None:
            frame = LayoutFrame.for_grid(rows, cols, display_name or f"{rows}x{cols}")

        # Initialize terminal with one active layout
        self._terminals[terminal_id] = {ACTIVE_LAYOUT: frame}
        log.info("Terminal %r registered (%dx%d)", terminal_id, rows, cols)
        return frame

    def unregister_terminal(self, terminal_id: str) -> None:
        """Remove a Terminal and all its Layouts."""
        if terminal_id == DEFAULT_TERMINAL:
            log.warning("Cannot unregister the default terminal; clearing instead")
            self._terminals[DEFAULT_TERMINAL] = {ACTIVE_LAYOUT: LayoutFrame.for_grid(4, 8, "4x8")}
            return
        self._terminals.pop(terminal_id, None)
        log.info("Terminal %r unregistered", terminal_id)

    def get_frame(self, terminal_id: str) -> LayoutFrame | None:
        """Return the active LayoutFrame for *terminal_id*, or None."""
        t = self._terminals.get(terminal_id)
        return t[ACTIVE_LAYOUT] if t else None

    def list_terminals(self) -> list[str]:
        """Return all registered terminal IDs."""
        return list(self._terminals.keys())

    # ── Layout management ─────────────────────────

    def list_layouts(self, terminal_id: str = DEFAULT_TERMINAL) -> list[str]:
        """Return the names of saved (named) layouts for a terminal.

        Excludes the internal ``_active`` key."""
        t = self._terminals.get(terminal_id)
        if t is None:
            return []
        return sorted(k for k in t if k != ACTIVE_LAYOUT)

    def switch_layout(self, terminal_id: str, layout_name: str) -> LayoutFrame | None:
        """Switch the active layout for *terminal_id* to *layout_name*.

        The currently-active frame is auto-saved to ``_autosave-<id>.yaml``
        before the switch so no state is lost.  Returns the new active
        frame, or None if the terminal or layout doesn't exist.
        """
        t = self._terminals.get(terminal_id)
        if t is None:
            log.warning("switch_layout: unknown terminal %r", terminal_id)
            return None
        if layout_name not in t:
            log.warning("switch_layout: layout %r not found on terminal %r", layout_name, terminal_id)
            return None

        # Autosave current frame before switching
        self._autosave_terminal(terminal_id)

        t[ACTIVE_LAYOUT] = t[layout_name]
        log.info("Terminal %r switched to layout %r", terminal_id, layout_name)
        return t[ACTIVE_LAYOUT]

    def save_layout_as(
        self, name: str, terminal_id: str = DEFAULT_TERMINAL, *, to_disk: bool = True
    ) -> Path | None:
        """Snapshot the active frame as a named layout.

        The layout is stored in-memory and, when *to_disk* is True,
        persisted to ``~/.vibe-deck/layouts/<name>.yaml``.

        Returns the disk path, or None if the terminal is unknown.
        """
        t = self._terminals.get(terminal_id)
        if t is None:
            log.warning("save_layout_as: unknown terminal %r", terminal_id)
            return None

        # Snapshot the active frame (shallow copy via model_copy)
        frame = t[ACTIVE_LAYOUT].model_copy(deep=True)
        t[name] = frame
        self._layout_name = name

        if to_disk:
            from ..config import LAYOUTS_DIR
            LAYOUTS_DIR.mkdir(parents=True, exist_ok=True)
            safe = name.replace("/", "_").replace("\\", "_")
            path = LAYOUTS_DIR / f"{safe}.yaml"
            frame.to_yaml(str(path))
            log.info("Layout %r saved for terminal %r → %s", name, terminal_id, path)
            return path
        return None

    def load_layout(
        self, path: str, terminal_id: str = DEFAULT_TERMINAL, *, as_name: str | None = None
    ) -> LayoutFrame | None:
        """Load a YAML layout file and set it as the active frame.

        If *as_name* is given the layout is also stored as a named
        layout under that key.
        """
        frame = LayoutFrame.from_yaml(path)
        if terminal_id not in self._terminals:
            self._terminals[terminal_id] = {}
        self._set_active(terminal_id, frame)
        if as_name:
            self._terminals[terminal_id][as_name] = frame
        self._layout_name = path
        return frame

    # ── Widget operations ─────────────────────────

    def update_widget(
        self, widget: WidgetState, terminal_id: str = DEFAULT_TERMINAL
    ) -> LayoutFrame | None:
        """Update or add a Widget in the specified terminal's active frame.

        Returns the updated frame, or None if the terminal is unknown.
        """
        t = self._terminals.get(terminal_id)
        if t is None:
            log.warning("update_widget: unknown terminal %r", terminal_id)
            return None
        frame = t[ACTIVE_LAYOUT]
        existing = frame.widgets.get(widget.id)
        if existing:
            existing.display = widget.display
            existing.meta.update(widget.meta)
            if existing.type != widget.type:
                existing.type = widget.type
        else:
            frame.widgets[widget.id] = widget
        return frame

    def upsert_widget(
        self, widget: WidgetState, terminal_id: str = DEFAULT_TERMINAL
    ) -> LayoutFrame | None:
        """Update or add a Widget, auto-placing new widgets on the first empty key.

        If the widget already exists in the active frame only its
        display/meta are updated (existing key position preserved).
        New widgets are placed on the first empty keymap slot.

        Returns the updated frame, or None if the terminal is unknown.
        """
        t = self._terminals.get(terminal_id)
        if t is None:
            log.warning("upsert_widget: unknown terminal %r", terminal_id)
            return None
        frame = t[ACTIVE_LAYOUT]

        # Keep pool in sync
        self.pool_add(widget)

        existing = frame.widgets.get(widget.id)
        if existing is not None:
            existing.display = widget.display
            existing.meta.update(widget.meta)
            if existing.type != widget.type:
                existing.type = widget.type
            return frame

        # New widget — auto-place on first empty key
        empty = frame.first_empty_key()
        if empty is not None:
            frame.place_widget(widget, empty)
            log.debug("Widget %r auto-placed at key %d on terminal %r",
                      widget.id, empty, terminal_id)
        else:
            # No empty slots — add to dict only, won't render
            frame.widgets[widget.id] = widget
            log.warning("Widget %r has no empty key slot on terminal %r (%d keys full)",
                        widget.id, terminal_id, len(frame.keymap))
        return frame

    def update_widget_across_terminals(self, widget: WidgetState) -> list[str]:
        """Update *widget* on every terminal that already has it.

        This is a targeted broadcast — it only touches terminals where
        the widget already exists in the active frame.  It **never**
        auto-creates the widget on terminals that don't have it (unlike
        the pre-refactor broadcast patch).

        Returns the list of terminal IDs that were updated.
        """
        updated: list[str] = []
        for tid, layouts in self._terminals.items():
            frame = layouts.get(ACTIVE_LAYOUT)
            if frame is None:
                continue
            existing = frame.widgets.get(widget.id)
            if existing is not None:
                existing.display = widget.display
                existing.meta.update(widget.meta)
                if existing.type != widget.type:
                    existing.type = widget.type
                updated.append(tid)
        return updated

    def remove_widget(self, widget_id: str, terminal_id: str = DEFAULT_TERMINAL) -> None:
        """Remove a Widget from a terminal's active frame."""
        t = self._terminals.get(terminal_id)
        if t is None:
            return
        t[ACTIVE_LAYOUT].remove_widget(widget_id)

    # ── Widget Pool ───────────────────────────────

    def pool_add(self, ws: WidgetState) -> None:
        """Add or update a widget in the pool (source of truth)."""
        self._pool[ws.id] = ws

    def pool_remove(self, widget_id: str) -> None:
        """Remove a widget from the pool."""
        self._pool.pop(widget_id, None)

    def pool_get(self, widget_id: str) -> WidgetState | None:
        """Get a widget from the pool by ID."""
        return self._pool.get(widget_id)

    def pool_list(self) -> list[WidgetState]:
        """List all widgets currently in the pool."""
        return list(self._pool.values())

    def pool_activate(
        self, widget_id: str, terminal_id: str, key_index: int | None = None
    ) -> bool:
        """Copy a pool widget onto a terminal's active frame.

        If *key_index* is ``None``, the first empty key on the terminal
        is used.  Returns ``True`` if the widget was placed, ``False``
        if the pool widget is missing, the terminal is unknown, or no
        empty key is available.
        """
        ws = self._pool.get(widget_id)
        if ws is None:
            return False
        frame = self.get_frame(terminal_id)
        if frame is None:
            return False
        if widget_id in frame.widgets:
            return True  # already placed
        if key_index is None:
            key_index = frame.first_empty_key()
        if key_index is None:
            return False
        frame.place_widget(ws, key_index)
        return True

    def pool_deactivate(self, widget_id: str, terminal_id: str) -> None:
        """Remove a pool widget from a terminal (widget stays in pool)."""
        self.remove_widget(widget_id, terminal_id)

    def pool_is_activated(self, widget_id: str, terminal_id: str) -> bool:
        """Return ``True`` if *widget_id* is placed on *terminal_id*."""
        frame = self.get_frame(terminal_id)
        return frame is not None and widget_id in frame.widgets

    def pool_activated_terminals(self, widget_id: str) -> list[str]:
        """Return terminal IDs where this pool widget is currently placed."""
        return [tid for tid in self.list_terminals()
                if self.pool_is_activated(widget_id, tid)]

    # ── Persistence ───────────────────────────────

    def save_layout(self, path: str, terminal_id: str = DEFAULT_TERMINAL) -> None:
        """Write a terminal's active frame to a YAML layout file (legacy).

        Prefer :meth:`save_layout_as` for new code — it manages both
        the in-memory copy and the disk write in one call.
        """
        t = self._terminals.get(terminal_id)
        if t is None:
            raise ValueError(f"Unknown terminal: {terminal_id!r}")
        t[ACTIVE_LAYOUT].to_yaml(path)
        self._layout_name = path

    def autosave_all(self) -> None:
        """Persist every terminal's active frame to an autosave file.

        Each frame is written to::

            ~/.vibe-deck/layouts/_autosave-<terminal_id>.yaml

        The ``_autosave-`` prefix keeps auto-saved state separate from
        user-managed layout files.  Called on daemon shutdown and after
        every widget state update so the layout survives crashes.
        """
        from ..config import LAYOUTS_DIR

        LAYOUTS_DIR.mkdir(parents=True, exist_ok=True)
        for terminal_id, layouts in self._terminals.items():
            self._autosave_terminal(terminal_id)
        self._autosave_pool()

    def _autosave_terminal(self, terminal_id: str) -> None:
        """Write one terminal's active frame to its autosave file."""
        from ..config import LAYOUTS_DIR

        t = self._terminals.get(terminal_id)
        if t is None:
            return
        path = LAYOUTS_DIR / f"{AUTOSAVE_PREFIX}-{terminal_id}.yaml"
        try:
            t[ACTIVE_LAYOUT].to_yaml(str(path))
        except Exception:
            log.exception("Failed to autosave terminal %r", terminal_id)

    def _autosave_pool(self) -> None:
        """Persist the widget pool to ``_autosave-pool.yaml``."""
        from ..config import LAYOUTS_DIR

        LAYOUTS_DIR.mkdir(parents=True, exist_ok=True)
        path = LAYOUTS_DIR / f"{AUTOSAVE_PREFIX}-pool.yaml"
        try:
            import yaml as _yaml
            data = {"widgets": []}
            for ws in self._pool.values():
                data["widgets"].append({
                    "id": ws.id,
                    "type": ws.type.value,
                    "icon": ws.display.icon,
                    "color": ws.display.color,
                    "animation": ws.display.animation.value if hasattr(ws.display.animation, 'value') else str(ws.display.animation),
                    "label": ws.display.label,
                    "badge": ws.display.badge,
                    "sprite": ws.display.sprite,
                    "meta": ws.meta,
                })
            path.write_text(_yaml.safe_dump(data, default_flow_style=False, allow_unicode=True, indent=2), encoding="utf-8")
        except Exception:
            log.exception("Failed to autosave pool")

    def pool_restore(self) -> None:
        """Restore pool widgets from ``_autosave-pool.yaml`` on startup."""
        from ..config import LAYOUTS_DIR
        from .types import DisplayState, WidgetState, WidgetType

        path = LAYOUTS_DIR / f"{AUTOSAVE_PREFIX}-pool.yaml"
        if not path.exists():
            return
        try:
            import yaml as _yaml
            data = _yaml.safe_load(path.read_text(encoding="utf-8"))
            if not data or "widgets" not in data:
                return
            for wd in data["widgets"]:
                ws = WidgetState(
                    id=wd["id"],
                    type=WidgetType(wd.get("type", "agent")),
                    display=DisplayState(
                        icon=wd.get("icon", ""),
                        color=wd.get("color", "#374151"),
                        animation=wd.get("animation", "none"),
                        label=wd.get("label", "Offline"),
                        badge=wd.get("badge"),
                        sprite=wd.get("sprite", "none"),
                    ),
                    meta=wd.get("meta", {}),
                )
                # Reset to offline on restore — avoid stale Running/Thinking
                ws.update_display(icon=ws.display.icon, color="#374151", animation="none", label="Offline")
                # Keep sprite from saved state (update_display doesn't touch sprite)
                ws.display.sprite = wd.get("sprite", "none")
                self._pool[ws.id] = ws
            log.info("Pool restored: %d widget(s)", len(self._pool))
        except Exception:
            log.exception("Failed to restore pool from %s", path)
