# 🦞 VibeDeck

> **A Stream Deck control surface for AI-assisted coding & local AI orchestration.**
> No keyboard, just vibes.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-brightgreen)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20Linux-blue)]()

---

## What is this?

VibeDeck turns your Elgato Stream Deck into a physical control surface for
local AI workflows. Monitor Claude Code sessions at a glance, toggle agent
windows with a single key press, and keep tabs on multiple AI processes —
all without alt-tabbing away from your editor.

It also provides a **browser-based virtual viewer** (phone, tablet, second
monitor) that mirrors the exact same display as the hardware — same layout,
same icons, same status.

## Architecture

VibeDeck is built around three clean concepts:

### Endpoint → Renderer → Binding

```
┌─────────────────────────────────────────────────┐
│  Endpoint "default" (4×8 grid, widget pool)     │
│  ┌───────────────────────────────────────────┐  │
│  │         StandardFrame (every tick)         │  │
│  └──────────────┬────────────────────────────┘  │
│                 │                                │
│     ┌───────────┴──────────┐                    │
│     ▼                      ▼                    │
│  Physical Renderer    Virtual Renderer          │
│  (USB HID → Deck)    (SSE → browser/phone)     │
└─────────────────────────────────────────────────┘
```

- **Endpoint** — A logical workspace with its own grid, widgets, layout, and auth token. Not a device — a service that devices attach to.
- **Renderer** — A stateless byte-pusher that delivers rendered frames to a specific output (HID for hardware, SSE for browsers).
- **Binding** — The exclusive relationship between one physical Stream Deck and one Endpoint. Auto-detected, user-confirmed, persistent across restarts.

### Standard Frame Pipeline

Every key image is built from a stack of composable **Layers**, rendered into a
device-independent **StandardFrame**, then pushed by thin **Transports**:

```
Backdrop → Sprite → Icon → Label → Badge → Effect
                         │
                         ▼
                  StandardFrame
              (JPEG + PNG per key)
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         HIDTransport         WebTransport
        (USB, flip+diff)    (SSE, base64 PNG)
```

See [ADR 0002](docs/adr/0002-standard-frame-pipeline.md) and
[ADR 0003](docs/adr/0003-endpoint-renderer-split.md) for the full design
rationale.

## Quick Start

```bash
# Install with Stream Deck hardware support
pip install "vibe-deck[deck]"

# List connected Stream Deck devices
vibe-deck info

# Start the daemon (auto-detects hardware; virtual viewer always available)
vibe-deck serve

# Open the virtual viewer in your browser
# → http://localhost:9734

# Allow connections from phone/tablet on LAN
vibe-deck serve --expose

# Terminal dashboard — no browser needed
vibe-deck status --watch
```

## CLI

```
vibe-deck serve              # Start the daemon
vibe-deck status             # Live terminal dashboard
vibe-deck whoami             # Identify the Claude Code session in your terminal
vibe-deck info               # List connected Stream Deck devices
vibe-deck widget list        # List active widgets
vibe-deck widget add         # Manually add a widget
vibe-deck layout list        # List saved layouts
vibe-deck layout load <name> # Switch layout
vibe-deck adapter list       # List installed adapters
vibe-deck config show        # View configuration
vibe-deck endpoint list      # List endpoints
vibe-deck endpoint rename    # Rename an endpoint
vibe-deck mcp serve          # Start MCP server (stdio transport)
vibe-deck setup claude-code  # Install Claude Code hooks integration
```

Every subcommand includes `--help` with examples.

## Built-in Adapters

VibeDeck auto-detects AI agent processes and shows their status on the Deck:

| Adapter | Detection | Data Source |
|---------|-----------|-------------|
| **Claude Code** | Process scanner (`claude`, `claude.exe`) | Process polling + file watch (`~/.vibe-deck/agents/`) |
| **OpenCode** | Process scanner (`opencode serve`) | SSE endpoint at `localhost:4096/event` |
| **OpenClaw** | Process scanner (`openclaw`) | WebSocket at `ws://127.0.0.1:18789` (JSON-RPC) |
| **Telegram** | Config-driven (env vars) | Telethon client (user session) |

All adapters use **passive monitoring** — no agent software modifications required.

## Project Status

🚧 **Pre-alpha (v0.1.0)** — Solid foundation, actively developed.

### What's working

- [x] Stream Deck hardware abstraction (auto-detect, HID transport)
- [x] Browser-based virtual viewer (SSE, real-time mirror of hardware)
- [x] 4 AI agent adapters (Claude Code, OpenCode, OpenClaw, Telegram)
- [x] Standard Frame rendering pipeline (PIL layers → JPEG+PNG per key)
- [x] Composable layer system (backdrop, icon, label, badge, sprite, effect)
- [x] Window focus toggle (press key → bring agent window to front)
- [x] Claude Code hooks integration (`vibe-deck setup claude-code`)
- [x] MCP server (expose agent/widget/deck state to external tools)
- [x] Animation engine (pulse, blink, crawl, sprite clips)
- [x] Settings UI + widget pool management
- [x] Layout save/load + auto-recovery
- [x] Network resilience (reconnect on disconnect, hot-plug support)
- [x] Platform abstraction (Windows primary; Linux support)

### Roadmap

#### Next: Approval System (Level B)
- [ ] Approval Panel Widget Container (on-demand Y/N layout on Deck)
- [ ] Widget Container infrastructure (rect bounds, layout strategies, presets)
- [ ] Agent Selector (bubble-sort multi-Agent approval queue)
- [ ] Keystroke injection (SendInput to Agent terminal on press)

#### Later
- [ ] Dynamic approval options + small-device layouts
- [ ] System metric widgets (GPU, CPU, memory)
- [ ] MCP client (ingest external MCP server data as widgets)
- [ ] systemd daemon integration
- [ ] Linux parity (platform backend feature-complete on par with Windows)
- [ ] Community adapter marketplace + skill distribution

## Acknowledgments

VibeDeck stands on the shoulders of giants. The Elgato Stream Deck protocol was
reverse-engineered entirely by the open source community — Elgato does not
provide an official Linux SDK.

**Core dependency:** [python-elgato-streamdeck](https://github.com/abcminiuser/python-elgato-streamdeck)
by Dean Camera (abcminiuser) and [many contributors](docs/ACKNOWLEDGMENTS.md).

Full credits in [docs/ACKNOWLEDGMENTS.md](docs/ACKNOWLEDGMENTS.md).

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

*Made with 🦞 by the VibeDeck team.*
