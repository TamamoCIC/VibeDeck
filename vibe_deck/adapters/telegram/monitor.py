#!/usr/bin/env python3
"""
Telegram Monitor — Telethon-based daemon for VibeDeck.

Monitors unread messages and writes status to ~/.vibe-deck/agents/telegram.json.

Setup:
  1. pip install telethon
  2. Get API credentials from https://my.telegram.org/apps
  3. Set environment variables: TG_API_ID, TG_API_HASH, TG_PHONE
  4. Run: python -m vibe_deck.adapters.telegram.monitor

First run will prompt for phone number + verification code.
Session is cached for subsequent runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from pathlib import Path

log = logging.getLogger("vibe_deck.telegram.monitor")

VIBEDECK_HOME = Path.home() / ".vibe-deck"
AGENTS_DIR = VIBEDECK_HOME / "agents"
SESSION_FILE = VIBEDECK_HOME / "adapters" / "telegram" / "session"
STATUS_FILE = AGENTS_DIR / "telegram.json"


async def main() -> None:
    """Run the Telegram monitor."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        from telethon import TelegramClient, events
    except ImportError:
        log.error("telethon not installed. Run: pip install telethon")
        sys.exit(1)

    api_id = int(os.environ.get("TG_API_ID", "0"))
    api_hash = os.environ.get("TG_API_HASH", "")
    phone = os.environ.get("TG_PHONE", "")

    if not api_id or not api_hash:
        log.error("Set TG_API_ID and TG_API_HASH environment variables.")
        log.error("Get them from: https://my.telegram.org/apps")
        sys.exit(1)

    # Ensure directories exist
    AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)

    client = TelegramClient(str(SESSION_FILE), api_id, api_hash)

    # Track unread state
    state = {"total_unread": 0, "recent_chats": [], "status": "starting"}

    @client.on(events.NewMessage)
    async def on_new_message(event):
        """Track new incoming messages."""
        try:
            chat = await event.get_chat()
            chat_name = getattr(chat, "title", None) or getattr(chat, "first_name", "Unknown")
            state["total_unread"] += 1
            state["status"] = "unread"
            state["recent_chats"].insert(0, {
                "chat": chat_name,
                "preview": (event.message.message or "")[:50],
                "time": time.time(),
            })
            state["recent_chats"] = state["recent_chats"][:5]
            _write_status(state)
        except Exception:
            pass

    @client.on(events.MessageRead)
    async def on_message_read(event):
        """Reset unread count when messages are read elsewhere."""
        state["total_unread"] = 0
        state["status"] = "idle"
        state["recent_chats"] = []
        _write_status(state)

    try:
        await client.start(phone=phone)
        log.info("Telegram monitor started. Session: %s", SESSION_FILE)
        state["status"] = "idle"
        _write_status(state)

        # Run until disconnected
        await client.run_until_disconnected()
    except KeyboardInterrupt:
        log.info("Telegram monitor stopped")
    except Exception as e:
        log.error("Telegram monitor error: %s", e)
        state["status"] = "no_session"
        _write_status(state)
    finally:
        await client.disconnect()


def _write_status(state: dict) -> None:
    """Write the current state to the status file."""
    status = {
        "agent": "Telegram",
        "total_unread": state.get("total_unread", 0),
        "recent_chats": state.get("recent_chats", []),
        "status": state.get("status", "idle"),
        "timestamp": time.time(),
    }
    STATUS_FILE.write_text(json.dumps(status, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
