"""
File Watcher — watches ~/.vibe-deck/agents/ for status file changes.

Supports two formats:
  - .json   — single-key status file (legacy): overwritten on each update
  - .jsonl  — append-only event stream (hook reporters): last line = current state

Uses watchfiles (inotify backend) to detect when agent status files
are created, modified, or deleted. Parses content and publishes
WIDGET_STATE_UPDATE messages to the MessageBus.

JSONL files are automatically truncated to MAX_JSONL_LINES on each
change to bound disk usage.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from ..config import AGENTS_DIR
from ..core.message_bus import MessageBus, MessageType
from .base import BaseConnector

log = logging.getLogger("vibe_deck.connectors.file_watcher")

MAX_JSONL_LINES = 1000

try:
    import watchfiles

    HAS_WATCHFILES = True
except ImportError:
    HAS_WATCHFILES = False


def _parse_stem(stem: str) -> tuple[str, str, str]:
    """Parse a JSONL/JSON file stem into (agent_name, pid_str, widget_id).

    New format (multi-instance, actual Claude Code PID):
      ``claude-code-12345``  →  (``claude-code``, ``12345``, ``claude-code-12345``)

    Fallback format (session_id suffix, hex — when process-tree walk fails):
      ``claude-code-a1b2c3d4``  →  (``claude-code``, ``a1b2c3d4``, ``claude-code-a1b2c3d4``)

    Old format (single-instance, backward compat):
      ``claude-code``        →  (``claude-code``, ``""``, ``claude-code-auto``)
    """
    import re as _re
    # Matches both decimal PIDs (e.g. 12345) and 8-char hex session IDs (e.g. a1b2c3d4)
    m = _re.match(r"^(.+)-(\d+|[0-9a-fA-F]{8})$", stem)
    if m:
        agent_name = m.group(1)
        pid = m.group(2)
        widget_id = stem
    else:
        agent_name = stem
        pid = ""
        widget_id = f"{stem}-auto"
    return agent_name, pid, widget_id


class FileWatcher(BaseConnector):
    """
    Watches the agent status directory for file changes.

    Each file in ~/.vibe-deck/agents/ represents one agent's status.
    - .json  files: whole-file read → JSON parse → publish
    - .jsonl files: incremental read from last offset → publish each new line

    JSONL offset tracking ensures that brief states (e.g. UserPromptSubmit →
    Waiting) are visible even when multiple events arrive in the same watchfiles
    batch.
    """

    def __init__(self, bus: MessageBus, watch_dir: Path | None = None) -> None:
        super().__init__("file-watcher", bus)
        self._watch_dir = watch_dir or AGENTS_DIR
        self._task: asyncio.Task | None = None
        self._offsets: dict[str, int] = {}  # path stem → last read byte offset
        self._last_truncation: dict[str, float] = {}  # path name → monotonic timestamp

    async def start(self) -> None:
        """Start watching the agents directory."""
        await super().start()
        if not HAS_WATCHFILES:
            log.warning(
                "watchfiles not installed; file watcher disabled. "
                "Install: pip install watchfiles"
            )
            return
        self._watch_dir.mkdir(parents=True, exist_ok=True)

        # Scan existing status files at startup — start from current EOF
        # so we only pick up new events going forward (avoids replaying
        # historical events on daemon restart).
        for f in sorted(self._watch_dir.glob("*")):
            if f.suffix == ".json":
                await self._handle_change(watchfiles.Change.added, f)
            elif f.suffix == ".jsonl":
                # On cold start, read the last line to restore current state,
                # then set offset to EOF so we only pick up new events.
                try:
                    file_size = f.stat().st_size
                    self._offsets[f.name] = file_size
                    if file_size > 0:
                        await self._restore_last_state(f)
                except OSError:
                    self._offsets[f.name] = 0

        self._task = asyncio.create_task(self._watch_loop())
        log.info("File watcher started (dir=%s)", self._watch_dir)

    async def stop(self) -> None:
        """Stop watching."""
        await super().stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("File watcher stopped")

    async def _watch_loop(self) -> None:
        """Monitor filesystem events and parse status files."""
        async for changes in watchfiles.awatch(str(self._watch_dir)):
            if not self._running:
                break
            for change_type, path_str in changes:
                await self._handle_change(change_type, Path(path_str))

    # ── dispatch ────────────────────────────────────

    async def _handle_change(
        self, change_type: watchfiles.Change, path: Path
    ) -> None:
        """Route to the correct handler based on file extension."""
        suffix = path.suffix
        if suffix == ".json":
            await self._handle_json(change_type, path)
        elif suffix == ".jsonl":
            await self._handle_jsonl(change_type, path)

    # ── .json handler (legacy) ──────────────────────

    async def _handle_json(
        self, change_type: watchfiles.Change, path: Path
    ) -> None:
        """Process a single-key JSON status file."""
        agent_name, pid, widget_id = _parse_stem(path.stem)
        try:
            if change_type in (watchfiles.Change.added, watchfiles.Change.modified):
                data = json.loads(path.read_text(encoding="utf-8"))
                await self._publish(
                    MessageType.WIDGET_STATE_UPDATE,
                    {
                        "agent_name": agent_name,
                        "widget_id": widget_id,
                        "data": data,
                    },
                )
            elif change_type == watchfiles.Change.deleted:
                await self._publish(
                    MessageType.WIDGET_REMOVED,
                    {"agent_name": agent_name, "widget_id": widget_id},
                )
        except json.JSONDecodeError:
            log.warning("Invalid JSON in status file: %s", path)
        except Exception:
            log.exception("Error handling JSON file change: %s", path)

    # ── .jsonl handler ──────────────────────────────

    async def _handle_jsonl(
        self, change_type: watchfiles.Change, path: Path
    ) -> None:
        """Process an append-only JSONL event stream.

        Reads ALL new lines since the last known offset, and publishes
        each one in order. This preserves brief transient states (e.g.
        UserPromptSubmit → Waiting) that would otherwise be lost when
        multiple events arrive in the same watchfiles batch.

        After reading, truncates the file if it exceeds MAX_JSONL_LINES.
        """
        if change_type == watchfiles.Change.deleted:
            agent_name, pid, widget_id = _parse_stem(path.stem)
            await self._publish(
                MessageType.WIDGET_REMOVED,
                {"agent_name": agent_name, "widget_id": widget_id},
            )
            self._offsets.pop(path.name, None)
            return

        if change_type not in (watchfiles.Change.added, watchfiles.Change.modified):
            return

        try:
            file_size = path.stat().st_size
            if file_size == 0:
                return

            last_offset = self._offsets.get(path.name, 0)

            # If the file shrank (truncation by another process), reset offset
            if file_size < last_offset:
                log.debug("%s shrunk; resetting offset (was %d, now %d)",
                          path.name, last_offset, file_size)
                last_offset = 0

            # Nothing new to read
            if file_size == last_offset:
                return

            # Read only the new bytes since last offset
            new_bytes = await asyncio.to_thread(
                _read_range, path, last_offset, file_size - last_offset
            )
            new_text = new_bytes.decode("utf-8")
            new_lines = [ln for ln in new_text.split("\n") if ln]

            if not new_lines:
                self._offsets[path.name] = file_size
                return

            # Publish each new line in order so every state transition
            # (including brief ones like UserPromptSubmit → Waiting) is
            # pushed to the UI for at least one frame.
            agent_name, pid, widget_id = _parse_stem(path.stem)
            published = 0
            for line in new_lines:
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    log.debug("Unparseable JSONL line in %s: %.80s", path, line[:80])
                    continue

                hook_event = data.get("hook_event_name", "")
                tool_name = data.get("tool_name", "")
                session_id = data.get("session_id", "")[:8]
                log.info(
                    "[WATCHER] %s → hook=%s tool=%s session=%s",
                    path.name, hook_event, tool_name, session_id,
                )

                await self._publish(
                    MessageType.WIDGET_STATE_UPDATE,
                    {
                        "agent_name": agent_name,
                        "widget_id": widget_id,
                        "data": data,
                    },
                )
                published += 1

            # Update offset to current EOF
            self._offsets[path.name] = path.stat().st_size

            if published > 1:
                log.info("[WATCHER] %s: %d events published (batched)", path.name, published)

        except Exception:
            log.exception("Error handling JSONL file change: %s", path)

        # Best-effort truncation in the background
        await self._truncate_jsonl(path)

    async def _restore_last_state(self, path: Path) -> None:
        """Read the last JSONL line at startup to restore current agent state.

        Without this, a cold-started VibeDeck daemon has no way to know
        what Claude Code is doing — the FileWatcher only sees *new* events
        written after startup.  Reading the tail of the log lets us show
        the last known state (e.g. "Idle" after a Stop, "Waiting" after
        UserPromptSubmit, tool name after PreToolUse) instead of falling
        through to the adapter heartbeat and the thinking-timer dead end.
        """
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            lines = [ln for ln in text.split("\n") if ln]
            if not lines:
                return
            last_line = lines[-1]
            try:
                data = json.loads(last_line)
            except json.JSONDecodeError:
                log.debug("Last JSONL line unparseable in %s; skipping", path.name)
                return

            hook_event = data.get("hook_event_name", "")
            session_id = data.get("session_id", "")[:8]
            log.info(
                "[WATCHER] Cold start: restored last state → hook=%s session=%s",
                hook_event or "(none)", session_id,
            )

            agent_name, pid, widget_id = _parse_stem(path.stem)
            await self._publish(
                MessageType.WIDGET_STATE_UPDATE,
                {
                    "agent_name": agent_name,
                    "widget_id": widget_id,
                    "data": data,
                },
            )
        except Exception:
            log.debug("Failed to restore last state from %s", path, exc_info=True)

    async def _truncate_jsonl(
        self, path: Path, max_lines: int = MAX_JSONL_LINES
    ) -> None:
        """Truncate a JSONL file to its last `max_lines` lines.

        Runs in a thread to avoid blocking the asyncio event loop for
        large files.

        Includes a per-file cooldown (5s) so truncation doesn't fire on
        every single event when Claude Code is producing them rapidly.
        After writing, updates the offset tracker to the exact byte count
        written — this prevents the watchfiles event triggered by our own
        write from re-reading the entire file from offset 0.
        """
        import time as _time

        try:
            # ── Cooldown guard ──────────────────────────────
            now = _time.monotonic()
            last = self._last_truncation.get(path.name, 0)
            if now - last < 5.0:
                return  # too soon since last truncation
            self._last_truncation[path.name] = now

            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            lines = [ln for ln in text.split("\n") if ln]
            if len(lines) <= max_lines:
                return
            truncated = "\n".join(lines[-max_lines:]) + "\n"
            truncated_bytes = truncated.encode("utf-8")
            await asyncio.to_thread(
                path.write_text, truncated, encoding="utf-8"
            )
            # Update offset to the exact bytes we wrote so the
            # watchfiles event triggered by this write is a no-op.
            # Using len(truncated_bytes) instead of stat() avoids
            # accidentally skipping concurrent writes from Claude Code
            # that landed between our write and the stat call.
            self._offsets[path.name] = len(truncated_bytes)
            log.debug("Truncated %s: %d → %d lines", path.name, len(lines), max_lines)
        except Exception:
            log.debug("Failed to truncate %s", path, exc_info=True)


def _read_range(path: Path, offset: int, length: int) -> bytes:
    """Read `length` bytes starting at `offset` from `path`.

    Extracted as a module-level function so it can be called via
    asyncio.to_thread without capturing `self`.
    """
    with open(path, "rb") as f:
        f.seek(offset)
        return f.read(length)
