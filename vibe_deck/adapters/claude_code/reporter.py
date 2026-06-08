#!/usr/bin/env python3
"""
Claude Code Hook Reporter for VibeDeck.

Reads hook event JSON from stdin (Claude Code passes it to hook scripts),
appends to a JSONL event stream file that VibeDeck's FileWatcher picks up.

Usage (configured in Claude Code hooks settings):
    python /path/to/reporter.py

Design:
    - Pure Python stdlib — zero imports beyond stdlib, ~30ms cold start
    - Always exits 0 — never blocks or errors Claude Code
    - Append-only JSONL — one line per event, no read-then-write races
    - Timestamped on receipt — _vibedeck_ts records when VibeDeck saw it
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_LINES = 1000
DEFAULT_VIBEDECK_HOME = Path.home() / ".vibe-deck"

# Lone surrogates (\udc00-\udfff) can appear in Claude's JSON output but
# are not valid Unicode and cannot be encoded to UTF-8.  Replace them with
# U+FFFD (replacement character) so the JSONL write never fails.
_SURR_REPL = "\\ufffd"


def _sanitize_surrogates(s: str) -> str:
    """Replace lone surrogates in `s` with \\ufffd."""
    out: list[str] = []
    for ch in s:
        cp = ord(ch)
        if 0xDC00 <= cp <= 0xDFFF:
            out.append(_SURR_REPL)
        else:
            out.append(ch)
    return "".join(out)


def _vibedeck_home() -> Path:
    """Resolve VibeDeck data directory."""
    env = os.environ.get("VIBEDECK_HOME", "")
    if env:
        return Path(env)
    return DEFAULT_VIBEDECK_HOME


def _output_file() -> Path:
    """Path to the Claude Code JSONL event stream."""
    agents_dir = _vibedeck_home() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    return agents_dir / "claude-code.jsonl"


def main() -> None:
    """Read hook event from stdin, append to JSONL, exit cleanly."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        event = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        # Malformed input → silently ignore. Never block Claude Code.
        sys.exit(0)

    # Annotate with receipt metadata
    event["_vibedeck_ts"] = datetime.now(timezone.utc).isoformat()
    event["_vibedeck_source"] = "claude-code-hook"

    # Debug logging to stderr (visible in Claude Code hook output)
    hook_event = event.get("hook_event_name", "?")
    tool_name = event.get("tool_name", "")
    session_id = event.get("session_id", "")[:8]
    msg = f"[VibeDeck] {hook_event}"
    if tool_name:
        msg += f" tool={tool_name}"
    print(f"{msg} sid={session_id}", file=sys.stderr, flush=True)

    # Use compact JSON for one-line-per-event.
    # Sanitize lone surrogates that Claude's output may contain — these are
    # valid in JSON strings but cannot be encoded to UTF-8.
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    line = _sanitize_surrogates(line) + "\n"

    try:
        with open(_output_file(), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Disk full, permissions, etc. — silently ignore.
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
