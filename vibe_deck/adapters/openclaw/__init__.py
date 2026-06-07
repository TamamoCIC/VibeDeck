"""
OpenClaw adapter — maps OpenClaw Gateway WebSocket events to VibeDeck display states.

Connects to OpenClaw Gateway at ws://127.0.0.1:18789 and subscribes to
agent lifecycle events.

Default display mapping:
  - running → 🦞 running, green, crawl
  - completed → 🦞 idle, dim green, none
  - failed → 🔴 error, red, blink
  - approval_requested → 🟡 approval, yellow, blink
  - offline → ⚫ offline, dark gray, none
"""

STATUS_TO_DISPLAY = {
    "running": {"icon": "🦞", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🦞", "color": "#166534", "animation": "none", "label": "Idle"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "approval": {"icon": "🟡", "color": "#eab308", "animation": "blink", "label": "Approve"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}
