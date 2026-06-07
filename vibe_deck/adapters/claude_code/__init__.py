"""
Claude Code adapter — maps Claude Code hooks to VibeDeck display states.

Uses Claude Code's hook system (8 lifecycle events) via a hook script
that writes agent status to ~/.vibe-deck/agents/claude-code.json.

Default display mapping:
  - running → 🐙, green, crawl
  - idle → 🐙, dim green, none
  - waiting_for_user → 🐙, yellow, blink
  - error → 🔴, red, blink
  - offline → ⚫, dark gray, none
"""

from __future__ import annotations

from ...core.types import DisplayState, WidgetState, WidgetType

STATUS_TO_DISPLAY = {
    "running": {"icon": "🐙", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🐙", "color": "#166534", "animation": "none", "label": "Idle"},
    "waiting_for_user": {"icon": "🐙", "color": "#eab308", "animation": "blink", "label": "Waiting"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}


class ClaudeCodeAdapter:
    """
    Translates Claude Code hook events to VibeDeck Widget states.

    Usage:
        adapter = ClaudeCodeAdapter(name="claude-code-main", session_id="abc123")
        adapter.set_running()
        widget_state = adapter.as_widget_state()
    """

    def __init__(self, name: str = "claude-code", session_id: str = "") -> None:
        self.name = name
        self.session_id = session_id
        self._status = "offline"
        self._info = ""

    @property
    def status(self) -> str:
        return self._status

    def set_running(self, info: str = "") -> None:
        self._status = "running"
        self._info = info

    def set_idle(self) -> None:
        self._status = "idle"
        self._info = ""

    def set_waiting(self) -> None:
        self._status = "waiting_for_user"
        self._info = ""

    def set_error(self, info: str = "") -> None:
        self._status = "error"
        self._info = info

    def set_offline(self) -> None:
        self._status = "offline"
        self._info = ""

    def as_widget_state(self) -> WidgetState:
        """Produce a WidgetState with the current status."""
        display_cfg = STATUS_TO_DISPLAY.get(self._status, STATUS_TO_DISPLAY["offline"])
        ds = DisplayState(**display_cfg)
        if self._info and self._status == "running":
            ds.label = self._info[:12]
        return WidgetState(
            id=f"{self.name}-auto",
            type=WidgetType.AGENT,
            display=ds,
            meta={
                "agent": "Claude Code",
                "session_id": self.session_id,
                "status": self._status,
                "info": self._info,
            },
        )

    def status_file_content(self) -> dict:
        """Return the dict to write to ~/.vibe-deck/agents/<name>.json."""
        return {
            "agent": "Claude Code",
            "session_id": self.session_id,
            "status": self._status,
            "info": self._info,
            "timestamp": __import__("time").time(),
        }
