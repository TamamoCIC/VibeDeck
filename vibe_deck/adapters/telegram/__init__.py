"""
Telegram adapter — maps Telegram notification state to VibeDeck display.

A Telethon-based daemon monitors messages and writes status to
~/.vibe-deck/agents/telegram.json. The file watcher picks it up.

Default display mapping:
  - unread → 💬, blue, pulse, badge=count
  - idle → 💤, dim gray, none
  - offline → ⚫, dark gray, none
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ...config import AGENTS_DIR
from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.telegram")

STATUS_TO_DISPLAY = {
    "unread": {"icon": "💬", "color": "#6366f1", "animation": "pulse", "label": "Telegram"},
    "idle": {"icon": "💤", "color": "#374151", "animation": "none", "label": "Telegram"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}


class TelegramAdapter:
    """
    Reads Telegram notification state from the status file and
    produces WidgetState updates.

    Works with the Telethon monitor script (monitor.py) which
    writes status to ~/.vibe-deck/agents/telegram.json.
    """

    STATUS_FILE = AGENTS_DIR / "telegram.json"

    def __init__(self, name: str = "telegram") -> None:
        self.name = name

    def read_status(self) -> dict | None:
        """Read the current Telegram status from disk."""
        if not self.STATUS_FILE.exists():
            return None
        try:
            return json.loads(self.STATUS_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def get_display_state(self) -> tuple[str, DisplayState]:
        """Determine the current display state from the status file."""
        data = self.read_status()
        if data is None:
            return "offline", DisplayState(**STATUS_TO_DISPLAY["offline"])

        status = data.get("status", "idle")
        unread = data.get("total_unread", 0)

        if status == "no_session":
            return "offline", DisplayState(**STATUS_TO_DISPLAY["offline"])
        elif unread > 0:
            cfg = dict(STATUS_TO_DISPLAY["unread"])
            cfg["badge"] = str(unread) if unread < 100 else "99+"
            return "unread", DisplayState(**cfg)
        else:
            return "idle", DisplayState(**STATUS_TO_DISPLAY["idle"])

    def as_widget_state(self) -> WidgetState:
        """Produce a WidgetState with the current Telegram status."""
        status, ds = self.get_display_state()
        data = self.read_status() or {}
        return WidgetState(
            id=f"{self.name}-auto",
            type=WidgetType.AGENT,
            display=ds,
            meta={"agent": "Telegram", "status": status, **data},
        )
