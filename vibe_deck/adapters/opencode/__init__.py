"""
OpenCode adapter — maps OpenCode SSE events to VibeDeck display states.

Connects to `opencode serve` SSE endpoint at /event and listens for
session status events.

Default display mapping:
  - busy → 🦊 running, green, crawl
  - idle → 🦊 idle, dim green, none
  - retry → 🔴 error, red, blink
  - permission_asked → 🟡 approval, yellow, blink
  - offline → ⚫ offline, dark gray, none
"""

STATUS_TO_DISPLAY = {
    "running": {"icon": "🦊", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🦊", "color": "#166534", "animation": "none", "label": "Idle"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Retrying"},
    "approval": {"icon": "🟡", "color": "#eab308", "animation": "blink", "label": "Approve"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}
