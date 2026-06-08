"""
Claude Code adapter — polls Claude Code processes and publishes status.

Uses psutil to detect Claude Code processes by PID and monitors their
state. Publishes WIDGET_STATE_UPDATE to the MessageBus on status changes.

Default display mapping:
  - running → 🐙, green, crawl
  - idle → 🐙, dim green, none
  - waiting_for_user → 🐙, yellow, blink
  - error → 🔴, red, blink
  - offline → ⚫, dark gray, none
"""

from __future__ import annotations

import asyncio
import logging

import psutil

from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.claude_code")

STATUS_TO_DISPLAY = {
    "running": {"icon": "🐙", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🐙", "color": "#166534", "animation": "none", "label": "Idle"},
    "waiting_for_user": {"icon": "🐙", "color": "#eab308", "animation": "blink", "label": "Waiting"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}


class ClaudeCodeAdapter:
    """
    Polls a Claude Code process and publishes WidgetState updates.

    The adapter monitors a specific PID. If the process dies, it
    publishes an offline status and exits.

    Usage (by AdapterManager):
        adapter = ClaudeCodeAdapter(name="claude-code", bus=message_bus, pid=12345)
        await adapter.start()  # runs until process exits or stopped
    """

    def __init__(self, name: str = "claude-code", bus=None, pid: int = 0) -> None:
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
        """Begin polling. Runs until stop() is called or process exits."""
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
        """Stop polling."""
        self._running = False
        self._status = "offline"
        await self._publish()

    async def _publish(self) -> None:
        """Publish current state to MessageBus."""
        if self._bus is None:
            return
        from ...core.message_bus import Message, MessageType
        ws = self.as_widget_state()
        await self._bus.publish(Message(
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
        ))

    def as_widget_state(self) -> WidgetState:
        """Produce a WidgetState with the current status."""
        display_cfg = STATUS_TO_DISPLAY.get(self._status, STATUS_TO_DISPLAY["offline"])
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
