#!/usr/bin/env bash
# VibeDeck Claude Code Hook Script
# Reads hook event from stdin, maps to status, writes to ~/.vibe-deck/agents/
#
# Install: copy hooks.json to .claude/settings.json in your project.
#
# Each hook event → status mapping:
#   SessionStart → running
#   PreToolUse / PostToolUse → running
#   UserPromptSubmit → waiting_for_user
#   Stop → idle
#   Error output → error

set -euo pipefail

VIBEDECK_DIR="${HOME}/.vibe-deck/agents"
mkdir -p "${VIBEDECK_DIR}"

# Read JSON from stdin
INPUT=$(cat)

# Extract fields
HOOK_EVENT=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('hook_event_name','unknown'))" 2>/dev/null || echo "unknown")
SESSION_ID=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id','no-session'))" 2>/dev/null || echo "no-session")
CWD=$(echo "$INPUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('cwd','.'))" 2>/dev/null || echo ".")

# Map hook event to status
case "${HOOK_EVENT}" in
    SessionStart)
        STATUS="running"
        ;;
    PreToolUse|PostToolUse)
        STATUS="running"
        ;;
    UserPromptSubmit)
        STATUS="waiting_for_user"
        ;;
    Stop)
        STATUS="idle"
        ;;
    *)
        STATUS="running"
        ;;
esac

# Build status JSON
STATUS_FILE="${VIBEDECK_DIR}/claude-code-${SESSION_ID}.json"

python3 -c "
import json, time
data = {
    'agent': 'Claude Code',
    'session_id': '${SESSION_ID}',
    'status': '${STATUS}',
    'cwd': '${CWD}',
    'hook_event': '${HOOK_EVENT}',
    'timestamp': time.time(),
}
with open('${STATUS_FILE}', 'w') as f:
    json.dump(data, f)
"

exit 0
