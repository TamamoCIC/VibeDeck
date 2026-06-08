"""
Claude Code adapter — monitors Claude Code processes and maps hook events.

Two integration paths (both can coexist):
  1. Process polling  — psutil heartbeat to detect alive / dead
  2. Hook events      — Claude Code fires hooks → reporter.py writes JSONL
                         → FileWatcher picks up → MessageBus → display

The adapter's appearance config is loaded from
~/.vibe-deck/adapters/claude-code.yaml (fallback to built-in defaults).
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import psutil
import yaml

from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.claude_code")

APPEARANCE_CONFIG_PATH = Path.home() / ".vibe-deck" / "adapters" / "claude-code.yaml"

# ── Built-in fallback defaults ──────────────────────

_BUILTIN_STATUS_TO_DISPLAY: dict[str, dict[str, str]] = {
    "SessionStart":     {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Running"},
    "Stop":             {"icon": "🐙", "color": "#166534", "animation": "none",   "label": "Idle"},
    "UserPromptSubmit": {"icon": "🐙", "color": "#eab308", "animation": "blink",  "label": "Waiting"},
    "PreToolUse":       {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Tool"},
    "PostToolUse":      {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Running"},
    "PreCompact":       {"icon": "🐙", "color": "#6366f1", "animation": "pulse",  "label": "Compact"},
    "SubagentStop":     {"icon": "🐙", "color": "#166534", "animation": "none",   "label": "Sub done"},
    "SessionEnd":       {"icon": "⚫", "color": "#374151", "animation": "none",   "label": "Offline"},
    "running":          {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Running"},
    "idle":             {"icon": "🐙", "color": "#166534", "animation": "none",   "label": "Idle"},
    "waiting_for_user": {"icon": "🐙", "color": "#eab308", "animation": "blink",  "label": "Waiting"},
    "thinking":         {"icon": "🐙", "color": "#7c3aed", "animation": "pulse",  "label": "Thinking"},
    "writing":          {"icon": "🐙", "color": "#3b82f6", "animation": "pulse",  "label": "Writing"},
    "error":            {"icon": "🔴", "color": "#ef4444", "animation": "blink",  "label": "Error"},
    "offline":          {"icon": "⚫", "color": "#374151", "animation": "none",   "label": "Offline"},
}


def _load_appearance_config() -> dict[str, dict[str, str]]:
    """Load appearance config from YAML, falling back to built-in defaults."""
    try:
        if APPEARANCE_CONFIG_PATH.exists():
            raw = yaml.safe_load(APPEARANCE_CONFIG_PATH.read_text(encoding="utf-8"))
            if raw and "events" in raw:
                loaded = dict(raw["events"])
                # Merge with built-in so missing keys are filled
                merged = dict(_BUILTIN_STATUS_TO_DISPLAY)
                merged.update(loaded)
                return merged
    except Exception:
        log.warning("Failed to load appearance config, using built-in defaults",
                    exc_info=True)
    return dict(_BUILTIN_STATUS_TO_DISPLAY)


def _save_appearance_config(events: dict[str, dict[str, str]]) -> None:
    """Persist appearance config to YAML."""
    APPEARANCE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"events": events}
    with open(APPEARANCE_CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True, indent=2)


# Active config — loaded at import time, reloadable
STATUS_TO_DISPLAY: dict[str, dict[str, str]] = _load_appearance_config()


class ClaudeCodeAdapter:
    """
    Monitors a Claude Code process and publishes WidgetState updates.

    Two modes (automatic fallback):
      - Hook-driven  — display updates arrive via FileWatcher from hook events
      - Process-poll — psutil heartbeat; detects exit even without hooks

    The adapter writes WIDGET_STATE_UPDATE to the MessageBus on every
    status change. The supervisor routes these to the LayoutEngine.

    Usage (by AdapterManager):
        adapter = ClaudeCodeAdapter(name="claude-code", bus=message_bus, pid=12345)
        await adapter.start()
    """

    def __init__(
        self, name: str = "claude-code", bus=None, pid: int = 0, **kwargs
    ) -> None:
        self.name = name
        self._bus = bus
        self._pid = pid
        self._status = "offline"
        self._running = False
        self._session_id = f"claude-{pid}"

    @property
    def status(self) -> str:
        return self._status

    def _is_process_alive(self) -> bool:
        """Check if the tracked PID is still running."""
        if not self._pid:
            return False
        try:
            proc = psutil.Process(self._pid)
            return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    async def start(self) -> None:
        """Begin monitoring. Runs until stop() is called or process exits."""
        self._running = True
        self._status = "running"
        await self._publish()
        log.info("ClaudeCode adapter started (pid=%d)", self._pid)

        try:
            while self._running:
                await asyncio.sleep(3.0)
                if not self._is_process_alive():
                    log.info("Claude Code process exited (pid=%d)", self._pid)
                    self._status = "offline"
                    await self._publish()
                    break
        except asyncio.CancelledError:
            pass
        finally:
            self._running = False

    async def stop(self) -> None:
        """Stop monitoring."""
        self._running = False
        self._status = "offline"
        await self._publish()

    async def _publish(self) -> None:
        """Publish current state to MessageBus."""
        if self._bus is None:
            return
        from ...core.message_bus import Message, MessageType

        ws = self.as_widget_state()
        await self._bus.publish(
            Message(
                type=MessageType.WIDGET_STATE_UPDATE,
                source=f"adapter:{self.name}",
                payload={
                    "agent_name": self.name,
                    "data": {
                        "status": self._status,
                        "session_id": self._session_id,
                        "pid": self._pid,
                    },
                    "widget_id": ws.id,
                    "display": ws.display.model_dump(),
                },
            )
        )

    def as_widget_state(self) -> WidgetState:
        """Produce a WidgetState with the current status."""
        display_cfg = STATUS_TO_DISPLAY.get(
            self._status, STATUS_TO_DISPLAY["offline"]
        )
        ds = DisplayState(**display_cfg)
        return WidgetState(
            id=f"{self.name}-auto",
            type=WidgetType.AGENT,
            display=ds,
            meta={
                "agent": "Claude Code",
                "session_id": self._session_id,
                "status": self._status,
                "pid": self._pid,
            },
        )
