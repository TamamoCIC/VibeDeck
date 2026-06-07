"""
File Watcher — watches ~/.vibe-deck/agents/ for status file changes.

Uses watchfiles (inotify backend) to detect when agent status files
are created, modified, or deleted. Parses JSON content and publishes
WIDGET_STATE_UPDATE messages.
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

try:
    import watchfiles
    HAS_WATCHFILES = True
except ImportError:
    HAS_WATCHFILES = False


class FileWatcher(BaseConnector):
    """
    Watches the agent status directory for file changes.

    Each JSON file in ~/.vibe-deck/agents/ represents one agent's status.
    The file watcher parses changes and publishes WIDGET_STATE_UPDATE.
    """

    def __init__(self, bus: MessageBus, watch_dir: Path | None = None) -> None:
        super().__init__("file-watcher", bus)
        self._watch_dir = watch_dir or AGENTS_DIR
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start watching the agents directory."""
        await super().start()
        if not HAS_WATCHFILES:
            log.warning("watchfiles not installed; file watcher disabled. "
                        "Install: pip install watchfiles")
            return
        self._watch_dir.mkdir(parents=True, exist_ok=True)
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

    async def _handle_change(self, change_type: watchfiles.Change, path: Path) -> None:
        """Process a single file change."""
        if not path.suffix == ".json":
            return

        try:
            if change_type in (watchfiles.Change.added, watchfiles.Change.modified):
                data = json.loads(path.read_text())
                await self._publish(MessageType.WIDGET_STATE_UPDATE, {
                    "agent_name": path.stem,
                    "data": data,
                })
            elif change_type == watchfiles.Change.deleted:
                await self._publish(MessageType.WIDGET_REMOVED, {
                    "agent_name": path.stem,
                })
        except json.JSONDecodeError:
            log.warning("Invalid JSON in status file: %s", path)
        except Exception:
            log.exception("Error handling file change: %s", path)
