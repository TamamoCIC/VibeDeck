"""
Claude Code adapter — monitors Claude Code processes and maps hook events.

Two integration paths (both can coexist):
  1. Process polling  — psutil heartbeat to detect alive / dead
  2. Hook events      — Claude Code fires hooks → reporter.py writes JSONL
                         → FileWatcher picks up → MessageBus → display

The adapter's STATUS_TO_DISPLAY covers every Claude Code hook event so
that no matter which path produces the update, the display mapping works.

Default display mapping:
  SessionStart       → 🐙, green, crawl  ("Running")
  Stop               → 🐙, dim green, none ("Idle")
  UserPromptSubmit   → 🐙, yellow, blink ("Waiting")
  PreToolUse         → 🐙, green, crawl  ("Tool: <name>")
  PostToolUse        → 🐙, green, crawl  ("Running")
  PreCompact         → 🐙, indigo, pulse ("Compacting")
  SubagentStop       → 🐙, dim green, none ("Sub done")
  SessionEnd         → ⚫, dark gray, none ("Offline")
  running (process)  → 🐙, green, crawl  ("Running")
  offline (process)  → ⚫, dark gray, none ("Offline")
  error              → 🔴, red, blink    ("Error")
"""

from __future__ import annotations

import asyncio
import logging

import psutil

from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.claude_code")

# Maps internal status keys (hook event names + process-based statuses)
# to VibeDeck display primitives.  Consumers look up by either
# `hook_event_name` or `status` field.
STATUS_TO_DISPLAY: dict[str, dict[str, str]] = {
    # ── Hook event → display ─────────────────────
    "SessionStart":     {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Running"},
    "Stop":             {"icon": "🐙", "color": "#166534", "animation": "none",   "label": "Idle"},
    "UserPromptSubmit": {"icon": "🐙", "color": "#eab308", "animation": "blink",  "label": "Waiting"},
    "PreToolUse":       {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Tool"},
    "PostToolUse":      {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Running"},
    "PreCompact":       {"icon": "🐙", "color": "#6366f1", "animation": "pulse",  "label": "Compact"},
    "SubagentStop":     {"icon": "🐙", "color": "#166534", "animation": "none",   "label": "Sub done"},
    "SessionEnd":       {"icon": "⚫", "color": "#374151", "animation": "none",   "label": "Offline"},
    # ── Process-based status → display ───────────
    "running":          {"icon": "🐙", "color": "#22c55e", "animation": "crawl",  "label": "Running"},
    "idle":             {"icon": "🐙", "color": "#166534", "animation": "none",   "label": "Idle"},
    "waiting_for_user": {"icon": "🐙", "color": "#eab308", "animation": "blink",  "label": "Waiting"},
    "error":            {"icon": "🔴", "color": "#ef4444", "animation": "blink",  "label": "Error"},
    "offline":          {"icon": "⚫", "color": "#374151", "animation": "none",   "label": "Offline"},
}


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
