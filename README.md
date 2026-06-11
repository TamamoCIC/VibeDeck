# 🦞 VibeDeck

> **A Stream Deck toolkit for Vibe Coding & local AI orchestration on Linux.**
> No keyboard, just vibes.

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10+-brightgreen)](pyproject.toml)

---

## What is this?

VibeDeck turns your Elgato Stream Deck into a physical control surface for
local AI workflows. Think: hardware buttons to launch models, toggle services,
monitor GPU usage, or trigger prompts — all without touching a keyboard.

## Quick Start

```bash
# Install with Stream Deck support
pip install "vibe-deck[deck]"

# List connected devices
vibe-deck info

# Start the daemon (virtual-only, no hardware required)
vibe-deck serve --no-physical

# Or just listen for key events
vibe-deck listen
```

## Project Status

🚧 **Pre-alpha** — Building the foundation. Expect rapid iteration.

### Roadmap

- [x] Stream Deck hardware abstraction layer
- [x] CLI for device discovery and key listening
- [ ] AI service monitor (local LLM dashboard on deck)
- [ ] MaidLLM integration (start/stop services from deck)
- [ ] Custom profile system (pageable key layouts)
- [ ] Animated GIF support on keys
- [ ] Vibe Coding hotkey launcher

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
