"""
Telegram adapter — file-based notification monitoring.

A Telethon daemon (monitor.py) writes status to
~/.vibe-deck/agents/telegram.json. The File Watcher picks it up
and publishes WIDGET_STATE_UPDATE to the MessageBus automatically.

This adapter module provides the display mapping and status file
reading utilities. The actual monitoring is done by monitor.py
(Telethon), which runs as a daemon-internal task if credentials
are configured.

Default display mapping:
  - unread → 💬, blue, pulse, badge=count
  - idle → 💤, dim gray, none
  - offline → ⚫, dark gray, none
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from ...config import AGENTS_DIR
from ...core.types import DisplayState, WidgetState, WidgetType

log = logging.getLogger("vibe_deck.adapters.telegram")

STATUS_TO_DISPLAY = {
    "unread": {"icon": "💬", "color": "#6366f1", "animation": "pulse", "label": "Telegram"},
    "idle": {"icon": "💤", "color": "#374151", "animation": "none", "label": "Telegram"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}

STATUS_FILE = AGENTS_DIR / "telegram.json"


class TelegramAdapter:
    """
    File-based Telegram status adapter.

    Writes status JSON to ~/.vibe-deck/agents/telegram.json when
    the Telethon monitor detects changes. The File Watcher picks
    up the file change and publishes to the MessageBus.

    Usage (by AdapterManager):
        adapter = TelegramAdapter(name="telegram", bus=message_bus)
        await adapter.start()  # starts Telethon monitor
    """

    def __init__(self, name: str = "telegram", bus=None, **kwargs) -> None:
        self.name = name
        self._bus = bus
        self._running = False
        self._status = "offline"

    @classmethod
    def is_configured(cls) -> bool:
        """Check if Telegram credentials are set in environment."""
        api_id = os.environ.get("TG_API_ID", "")
        api_hash = os.environ.get("TG_API_HASH", "")
        return bool(api_id and api_hash and api_id != "0")

    async def start(self) -> None:
        """Start the Telethon monitor and publish initial state."""
        self._running = True

        if not self.is_configured():
            log.warning("Telegram not configured — set TG_API_ID and TG_API_HASH")
            self._status = "offline"
            await self._publish()
            return

        try:
            from telethon import TelegramClient, events
        except ImportError:
            log.warning("telethon not installed. Run: pip install telethon")
            self._status = "offline"
            await self._publish()
            return

        api_id = int(os.environ["TG_API_ID"])
        api_hash = os.environ["TG_API_HASH"]
        phone = os.environ.get("TG_PHONE", "")

        STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

        state = {"total_unread": 0, "recent_chats": [], "status": "starting"}
        self._write_status(state)
        await self._publish()

        client = TelegramClient(
            str(Path.home() / ".vibe-deck" / "adapters" / "telegram" / "session"),
            api_id, api_hash,
        )

        @client.on(events.NewMessage)
        async def on_new_message(event):
            try:
                chat = await event.get_chat()
                chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown")
                state["total_unread"] += 1
                state["status"] = "unread"
                state["recent_chats"].insert(0, {
                    "chat": chat_name,
                    "preview": (event.message.message or "")[:50],
                })
                state["recent_chats"] = state["recent_chats"][:5]
                self._write_status(state)
                await self._publish()
            except Exception:
                pass

        @client.on(events.MessageRead)
        async def on_message_read(event):
            state["total_unread"] = 0
            state["status"] = "idle"
            state["recent_chats"] = []
            self._write_status(state)
            await self._publish()

        try:
            await client.start(phone=phone)
            log.info("Telegram monitor started")
            state["status"] = "idle"
            self._write_status(state)
            await self._publish()
            self._status = "idle"

            while self._running:
                await asyncio.sleep(5)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log.error("Telegram monitor error: %s", e)
            state["status"] = "no_session"
            self._write_status(state)
        finally:
            await client.disconnect()
            self._status = "offline"
            await self._publish()

    async def stop(self) -> None:
        """Stop the monitor."""
        self._running = False

    def _write_status(self, state: dict) -> None:
        """Write state to the agent status file."""
        status = {
            "agent": "Telegram",
            "total_unread": state.get("total_unread", 0),
            "recent_chats": state.get("recent_chats", []),
            "status": state.get("status", "idle"),
        }
        try:
            STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATUS_FILE.write_text(json.dumps(status, indent=2))
        except Exception:
            pass

    async def _publish(self) -> None:
        """Publish current state to MessageBus (for file watcher fallback)."""
        if self._bus is None:
            return
        from ...core.message_bus import Message, MessageType
        ws = self.as_widget_state()
        await self._bus.publish(Message(
            type=MessageType.WIDGET_STATE_UPDATE,
            source=f"adapter:{self.name}",
            payload={
                "agent_name": self.name,
                "data": {"status": self._status},
                "widget_id": ws.id,
                "display": ws.display.model_dump(),
            },
        ))

    def as_widget_state(self) -> WidgetState:
        """Produce a WidgetState with the current status."""
        # Try to read from file first for accurate unread count
        try:
            if STATUS_FILE.exists():
                data = json.loads(STATUS_FILE.read_text())
                status = data.get("status", "idle")
                unread = data.get("total_unread", 0)
                if status == "no_session":
                    cfg = dict(STATUS_TO_DISPLAY["offline"])
                elif unread > 0:
                    cfg = dict(STATUS_TO_DISPLAY["unread"])
                    cfg["badge"] = str(unread) if unread < 100 else "99+"
                else:
                    cfg = dict(STATUS_TO_DISPLAY["idle"])
                ds = DisplayState(**cfg)
                return WidgetState(
                    id=f"{self.name}-auto",
                    type=WidgetType.AGENT,
                    display=ds,
                    meta={"agent": "Telegram", "status": status, **data},
                )
        except Exception:
            pass

        display_cfg = STATUS_TO_DISPLAY.get(self._status, STATUS_TO_DISPLAY["offline"])
        ds = DisplayState(**display_cfg)
        return WidgetState(
            id=f"{self.name}-auto",
            type=WidgetType.AGENT,
            display=ds,
            meta={"agent": "Telegram", "status": self._status},
        )
