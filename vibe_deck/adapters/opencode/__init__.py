"""
OpenCode adapter — maps OpenCode SSE events to VibeDeck display states.

Connects to `opencode serve` SSE endpoint at /event and listens for
session status events.

Default display mapping:
  - busy → 🦊, green, crawl
  - idle → 🦊, dim green, none
  - retry → 🔴, red, blink
  - permission_asked → 🟡, yellow, blink (approval)
  - offline → ⚫, dark gray, none
"""

from __future__ import annotations

import asyncio
import json
import logging

from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.opencode")

STATUS_TO_DISPLAY = {
    "running": {"icon": "🦊", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🦊", "color": "#166534", "animation": "none", "label": "Idle"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Retrying"},
    "approval": {"icon": "🟡", "color": "#eab308", "animation": "blink", "label": "Approve"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}


class OpenCodeAdapter:
    """
    SSE client that connects to OpenCode's HTTP server and maps
    session status events to VibeDeck Widget states.

    Usage:
        adapter = OpenCodeAdapter(name="opencode-main", url="http://localhost:4096")
        await adapter.start()
    """

    def __init__(self, name: str = "opencode", url: str = "http://localhost:4096") -> None:
        self.name = name
        self.url = url.rstrip("/")
        self._status = "offline"
        self._task: asyncio.Task | None = None
        self._running = False

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        """Begin listening to OpenCode SSE events."""
        self._running = True
        self._task = asyncio.create_task(self._listen())
        log.info("OpenCode adapter started: %s", self.url)

    async def stop(self) -> None:
        """Stop listening."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._status = "offline"
        log.info("OpenCode adapter stopped")

    async def _listen(self) -> None:
        """SSE event loop with auto-reconnect."""
        import aiohttp

        backoff = 1
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.url}/event") as resp:
                        log.info("OpenCode SSE connected")
                        backoff = 1
                        async for line in resp.content:
                            if not self._running:
                                break
                            text = line.decode().strip()
                            if text.startswith("data: "):
                                await self._handle_event(text[6:])
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                log.warning("OpenCode SSE disconnected: %s. Reconnecting in %ds...", e, backoff)
                self._status = "offline"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("OpenCode SSE error")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _handle_event(self, data: str) -> None:
        """Parse an SSE event and update status."""
        try:
            event = json.loads(data)
            event_type = event.get("type", "")

            if event_type == "session.status":
                session_status = event.get("status", "")
                if session_status == "busy":
                    self._status = "running"
                elif session_status == "idle":
                    self._status = "idle"
                elif session_status in ("retry", "error"):
                    self._status = "error"
            elif event_type == "permission.asked":
                self._status = "approval"
            elif event_type == "question.asked":
                self._status = "approval"
        except json.JSONDecodeError:
            pass

    def as_widget_state(self) -> WidgetState:
        """Produce a WidgetState with the current status."""
        display_cfg = STATUS_TO_DISPLAY.get(self._status, STATUS_TO_DISPLAY["offline"])
        ds = DisplayState(**display_cfg)
        return WidgetState(
            id=f"{self.name}-auto",
            type=WidgetType.AGENT,
            display=ds,
            meta={"agent": "OpenCode", "url": self.url, "status": self._status},
        )
