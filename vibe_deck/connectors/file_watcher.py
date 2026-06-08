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
JSONL_TAIL_BYTES = 8192  # Read last 8KB to find the last complete line

try:
    import watchfiles

    HAS_WATCHFILES = True
except ImportError:
    HAS_WATCHFILES = False


class FileWatcher(BaseConnector):
    """
    Watches the agent status directory for file changes.

    Each file in ~/.vibe-deck/agents/ represents one agent's status.
    - .json  files: whole-file read → JSON parse → publish
    - .jsonl files: tail-read last line → JSON parse → publish, then truncate
    """

    def __init__(self, bus: MessageBus, watch_dir: Path | None = None) -> None:
        super().__init__("file-watcher", bus)
        self._watch_dir = watch_dir or AGENTS_DIR
        self._task: asyncio.Task | None = None

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

        # Scan existing status files at startup
        for f in sorted(self._watch_dir.glob("*")):
            if f.suffix in (".json", ".jsonl"):
                await self._handle_change(watchfiles.Change.added, f)

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
        try:
            if change_type in (watchfiles.Change.added, watchfiles.Change.modified):
                data = json.loads(path.read_text(encoding="utf-8"))
                await self._publish(
                    MessageType.WIDGET_STATE_UPDATE,
                    {
                        "agent_name": path.stem,
                        "data": data,
                    },
                )
            elif change_type == watchfiles.Change.deleted:
                await self._publish(
                    MessageType.WIDGET_REMOVED,
                    {"agent_name": path.stem},
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

        Reads only the last line (most recent event) to minimise I/O.
        After reading, truncates the file if it exceeds MAX_JSONL_LINES.
        """
        if change_type == watchfiles.Change.deleted:
            await self._publish(
                MessageType.WIDGET_REMOVED,
                {"agent_name": path.stem},
            )
            return

        if change_type not in (watchfiles.Change.added, watchfiles.Change.modified):
            return

        try:
            file_size = path.stat().st_size
            if file_size == 0:
                return

            # Read only the tail of the file to find the last complete line
            read_start = max(0, file_size - JSONL_TAIL_BYTES)
            tail_bytes = path.read_bytes()[read_start:]
            tail_text = tail_bytes.decode("utf-8")
            lines = [ln for ln in tail_text.strip().split("\n") if ln]

            if not lines:
                return

            last_line = lines[-1]
            try:
                data = json.loads(last_line)
            except json.JSONDecodeError:
                log.debug("Unparseable JSONL tail line in %s", path)
                return

            await self._publish(
                MessageType.WIDGET_STATE_UPDATE,
                {
                    "agent_name": path.stem,
                    "data": data,
                },
            )
        except Exception:
            log.exception("Error handling JSONL file change: %s", path)

        # Best-effort truncation in the background
        await self._truncate_jsonl(path)

    async def _truncate_jsonl(
        self, path: Path, max_lines: int = MAX_JSONL_LINES
    ) -> None:
        """Truncate a JSONL file to its last `max_lines` lines.

        Runs in a thread to avoid blocking the asyncio event loop for
        large files.
        """
        try:
            text = await asyncio.to_thread(path.read_text, encoding="utf-8")
            lines = [ln for ln in text.split("\n") if ln]
            if len(lines) <= max_lines:
                return
            truncated = "\n".join(lines[-max_lines:]) + "\n"
            await asyncio.to_thread(
                path.write_text, truncated, encoding="utf-8"
            )
            log.debug("Truncated %s: %d → %d lines", path.name, len(lines), max_lines)
        except Exception:
            log.debug("Failed to truncate %s", path, exc_info=True)
