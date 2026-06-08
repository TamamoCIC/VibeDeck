# Claude Code ↔ VibeDeck Integration Guide

Connecting Claude Code to VibeDeck for real-time agent status monitoring
on a Stream Deck (or virtual phone terminal).

## Quick Start

### 1. Install

```bash
pip install -e .
```

### 2. One-time setup

```bash
vibe-deck setup claude-code
```

This copies the hook reporter to `~/.vibe-deck/reporters/claude-code/` and
generates `hooks.json` with all 8 hook event handlers.

### 3. Wire hooks into Claude Code

Merge the generated hooks.json into `~/.claude/settings.json`:

```jsonc
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "python C:/Users/YOU/.vibe-deck/reporters/claude-code/reporter.py"
      }]
    }],
    "UserPromptSubmit": [ /* same command */ ],
    "PreToolUse":       [ /* same command */ ],
    "PostToolUse":      [ /* same command */ ],
    "Stop":             [ /* same command */ ],
    "SubagentStop":     [ /* same command */ ],
    "PreCompact":       [ /* same command */ ],
    "SessionEnd":       [ /* same command */ ]
  }
}
```

> **Windows note**: Use forward slashes (`C:/Users/...`) — JSON double-backslash
> escaping breaks command execution.

### 4. Start the daemon

```bash
vibe-deck serve --demo --no-physical --port 9734
```

### 5. Connect

Open `http://localhost:9734` in a browser. First visit shows a setup wizard —
pick your grid, give it a name, click Connect. The token is saved to
localStorage.

## Hook Events → Visual States

```
UserPromptSubmit   → 🟡 Waiting    yellow  blink  "Waiting"
  +0.8s silence    → 🟣 Thinking   purple  pulse  "Thinking"
PreToolUse         → 🟢 tool-name  green   crawl  (tool name)
PostToolUse        → 🟢 tool-name  green   crawl  (tool name)
  +0.8s silence    → 🟣 Thinking   purple  pulse  "Thinking"
Stop               → 🔵 Idle       slate   none   "Idle"
SessionEnd         → ⚫ Offline    gray    none   "Offline"
```

States between tool calls are inferred by a 0.8-second inactivity timeout
(since Claude Code has no hook for "model is thinking/writing").

## Web UI

### Toolbar

| Button | Function |
|--------|----------|
| Grid dropdown | Switch terminal grid size |
| 🔍 Live / ✏️ Inspect | Toggle between live monitoring and key inspector |
| 🎨 Visual | Edit per-event widget appearance (icon, color, animation) |
| 💾 Save | Save current widget layout to `~/.vibe-deck/layouts/` |
| 📂 Load | Load a saved layout |
| 💅 Theme | Edit CSS theme variables |

### Visual Timeline

Click `📋 Visual Timeline` at the bottom to see a millisecond-precision log
of every state change.

### Key Inspector

Switch to ✏️ Inspect mode, click any key to see its current widget details.

## Configuration Files

| File | Purpose |
|------|---------|
| `~/.vibe-deck/config.yaml` | Daemon config (port, terminals, agent patterns) |
| `~/.vibe-deck/adapters/claude-code.yaml` | Widget appearance per event |
| `~/.vibe-deck/layouts/*.yaml` | Saved widget layouts |
| `~/.vibe-deck/themes/default.css` | Web UI theme |
| `~/.claude/settings.json` | Claude Code hooks (global) |
| `.claude/settings.json` | Claude Code hooks (per-project) |

## Architecture

```
Claude Code
  │  hook fires (UserPromptSubmit, PreToolUse, PostToolUse, Stop, ...)
  ▼
reporter.py (stdin JSON → JSONL append)
  │
  ▼
~/.vibe-deck/agents/claude-code.jsonl
  │  watchfiles detects change
  ▼
FileWatcher → MessageBus → Supervisor → LayoutEngine → SSE → Web UI
                 │                         │
                 ▼                         ▼
          Adapter heartbeat         SimRenderer (Pillow → PNG)
          (psutil, 3s interval)     Hardware (Stream Deck)
```

## Frame Rate

- **30 fps** while agents are active (hook events within last 3 seconds)
- **1 fps** when all agents idle

## Troubleshooting

### Hook events not appearing

Check JSONL directly:
```bash
tail -5 ~/.vibe-deck/agents/claude-code.jsonl
```

If the file has events but the daemon doesn't show them, restart the daemon.

### Missing Stop / UserPromptSubmit events

These events contain Claude's output which may include lone surrogate
characters (`\udc00`-`\udfff`). The reporter sanitizes these to `�`
before writing. If you see `REPORTER_ERR` in the experiment log, check
that the reporter is up to date:

```bash
diff ~/.vibe-deck/reporters/claude-code/reporter.py \
     vibe_deck/adapters/claude_code/reporter.py
```

### Widget shows on wrong terminal

This is a known issue (#10). The current workaround broadcasts all
updates to all terminals. If you switch grid size in the web UI,
re-register via the wizard.

### Daemon debug logging

```bash
vibe-deck serve --demo --no-physical --debug
```

Logs show `[WATCHER]`, `[HOOK→UI]`, and `[THINKING]` messages for tracing
the full pipeline.

## Known Issues

- **Terminal vs Layout confusion** (#10): grid sizes treated as separate terminals
- **UserPromptSubmit fires inconsistently**: in long interactive sessions may
  only fire on first message — use Stop for reliable Idle detection
- **Adapter heartbeat suppression**: hook events authoritative over heartbeat;
  if you restart the daemon mid-session, the widget shows "Running" until
  next hook event arrives
