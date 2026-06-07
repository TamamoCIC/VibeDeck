"""
Claude Code adapter — maps Claude Code hooks to VibeDeck display states.

Uses Claude Code's hook system (8 lifecycle events) via a hook script
that writes agent status to ~/.vibe-deck/agents/claude-code.json.

Default display mapping:
  - SessionStart → 🐙 running, green, crawl
  - Stop → 🐙 idle, dim green, none
  - UserPromptSubmit → 🐙 waiting, yellow, blink
  - Error → 🔴 error, red, blink
  - Offline → ⚫ offline, dark gray, none
"""

STATUS_TO_DISPLAY = {
    "running": {"icon": "🐙", "color": "#22c55e", "animation": "crawl", "label": "Running"},
    "idle": {"icon": "🐙", "color": "#166534", "animation": "none", "label": "Idle"},
    "waiting_for_user": {"icon": "🐙", "color": "#eab308", "animation": "blink", "label": "Waiting"},
    "error": {"icon": "🔴", "color": "#ef4444", "animation": "blink", "label": "Error"},
    "offline": {"icon": "⚫", "color": "#374151", "animation": "none", "label": "Offline"},
}
