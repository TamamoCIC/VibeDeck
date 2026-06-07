"""
OpenClaw adapter — maps OpenClaw Gateway WebSocket events to VibeDeck display.

Connects to OpenClaw Gateway at ws://127.0.0.1:18789 and subscribes to
agent lifecycle events.

Default display mapping:
  - running → 🦞, green, crawl
  - completed/idle → 🦞, dim green, none
  - failed → 🔴, red, blink
  - approval_requested → 🟡, yellow, blink
  - offline → ⚫, dark gray, none
"""

from __future__ import annotations

import asyncio
import json
import logging

from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.openclaw")

STATUS_TO_DISPLAY = {
    "running": {"icon": "🦞", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🦞", "color": "#166534", "animation": "none", "label": "Idle"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "approval": {"icon": "🟡", "color": "#eab308", "animation": "blink", "label": "Approve"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}


class OpenClawAdapter:
    """
    WebSocket client that connects to OpenClaw Gateway and maps
    agent lifecycle events to VibeDeck Widget states.

    Usage:
        adapter = OpenClawAdapter(name="openclaw-main")
        await adapter.start()
    """

    def __init__(
        self,
        name: str = "openclaw",
        url: str = "ws://127.0.0.1:18789",
        token: str | None = None,
    ) -> None:
        self.name = name
        self.url = url
        self._token = token
        self._status = "offline"
        self._task: asyncio.Task | None = None
        self._running = False
        self._req_id = 0

    @property
    def status(self) -> str:
        return self._status

    async def start(self) -> None:
        """Connect to Gateway and subscribe to events."""
        self._running = True
        self._task = asyncio.create_task(self._listen())
        log.info("OpenClaw adapter started: %s", self.url)

    async def stop(self) -> None:
        """Disconnect."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._status = "offline"
        log.info("OpenClaw adapter stopped")

    async def _listen(self) -> None:
        """WebSocket event loop with auto-reconnect."""
        import websockets

        backoff = 1
        while self._running:
            try:
                async with websockets.connect(self.url) as ws:
                    log.info("OpenClaw Gateway connected")
                    # Connect handshake
                    await self._send(ws, "connect", {"auth": {"token": self._token} if self._token else {}})
                    backoff = 1

                    async for raw in ws:
                        if not self._running:
                            break
                        await self._handle_message(raw)
            except Exception as e:
                log.warning("OpenClaw Gateway disconnected: %s. Reconnecting in %ds...", e, backoff)
                self._status = "offline"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)
            except asyncio.CancelledError:
                break

    async def _send(self, ws, method: str, params: dict) -> None:
        """Send a JSON-RPC request frame."""
        self._req_id += 1
        frame = {
            "type": "req",
            "id": self._req_id,
            "method": method,
            "params": params,
        }
        await ws.send(json.dumps(frame))

    async def _handle_message(self, raw: str) -> None:
        """Parse a Gateway frame and update status."""
        try:
            frame = json.loads(raw)
            frame_type = frame.get("type", "")

            if frame_type == "event":
                event_name = frame.get("event", "")
                payload = frame.get("payload", {})

                if event_name == "agent":
                    # Agent lifecycle event
                    phase = payload.get("phase", "")
                    if phase == "start":
                        self._status = "running"
                    elif phase == "end":
                        self._status = "idle"
                    elif phase == "error":
                        self._status = "error"
                elif event_name == "approval":
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
            meta={"agent": "OpenClaw", "url": self.url, "status": self._status},
        )
