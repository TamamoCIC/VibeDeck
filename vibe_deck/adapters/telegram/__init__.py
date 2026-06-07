"""
Telegram adapter — maps Telegram notification state to VibeDeck display.

A Telethon-based daemon monitors messages and writes status to
~/.vibe-deck/agents/telegram.json. The file watcher picks it up.

Default display mapping:
  - unread → 💬 attention, blue, pulse, badge=count
  - idle → 💤 idle, dim gray, none
  - no_session → ⚫ offline, dark gray, none
"""

STATUS_TO_DISPLAY = {
    "unread": {"icon": "💬", "color": "#6366f1", "animation": "pulse", "label": "Telegram"},
    "idle": {"icon": "💤", "color": "#374151", "animation": "none", "label": "Telegram"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}
