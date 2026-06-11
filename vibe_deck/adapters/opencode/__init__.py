"""
OpenCode adapter — SSE client that maps OpenCode events to VibeDeck display.

Connects to `opencode serve` SSE endpoint at /event and listens for
session status events. Publishes WIDGET_STATE_UPDATE to MessageBus.

Default display mapping:
  - busy/running → 🦊, green, crawl
  - idle → 🦊, dim green, none
  - retry/error → 🔴, red, blink
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
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "crashed": {"icon": "⚠️", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "approval": {"icon": "🟡", "color": "#eab308", "animation": "blink", "label": "Approve"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}


class OpenCodeAdapter:
    """
    SSE client that connects to OpenCode's HTTP server and maps
    session status events to VibeDeck Widget states.

    Usage (by AdapterManager):
        adapter = OpenCodeAdapter(name="opencode", bus=message_bus)
        await adapter.start()  # runs until stopped
    """

    def __init__(self, name: str = "opencode", bus=None, url: str = "http://localhost:4096", **kwargs) -> None:
        self.name = name
        self._bus = bus
        self.url = url.rstrip("/")
        self._status = "offline"
        self._running = False

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        """Begin listening to OpenCode SSE events."""
        self._running = True
        await self._publish()
        log.info("OpenCode adapter started: %s", self.url)

        import aiohttp
        backoff = 1
        consecutive_failures = 0
        while self._running:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"{self.url}/event") as resp:
                        log.info("OpenCode SSE connected")
                        backoff = 1
                        consecutive_failures = 0  # reset on successful connection
                        async for line in resp.content:
                            if not self._running:
                                break
                            text = line.decode().strip()
                            if text.startswith("data: "):
                                await self._handle_event(text[6:])
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                consecutive_failures += 1
                # After ~5 consecutive failures (~62s cumulative), show
                # "error" to signal a persistent network problem rather
                # than a brief disconnect.
                if consecutive_failures >= 5:
                    log.warning("OpenCode SSE persistent failure (%d attempts): %s",
                                consecutive_failures, e)
                    self._status = "error"
                else:
                    self._status = "offline"
                await self._publish()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except asyncio.CancelledError:
                break
            except Exception:
                consecutive_failures += 1
                log.exception("OpenCode SSE error")
                if consecutive_failures >= 5:
                    self._status = "error"
                else:
                    self._status = "offline"
                await self._publish()
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def stop(self) -> None:
        """Stop listening."""
        self._running = False
        self._status = "offline"
        await self._publish()
        log.info("OpenCode adapter stopped")

    async def _handle_event(self, data: str) -> None:
        """Parse an SSE event and update status."""
        try:
            event = json.loads(data)
            event_type = event.get("type", "")
            old_status = self._status

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

            if self._status != old_status:
                await self._publish()
        except json.JSONDecodeError:
            pass

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
                "data": {"status": self._status, "url": self.url},
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
            meta={"agent": "OpenCode", "url": self.url, "status": self._status},
        )
