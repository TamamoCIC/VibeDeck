"""
Process Scanner — auto-detects agent processes by matching patterns.

Uses /proc scanning and pattern matching to detect when AI agent
processes start and stop. Published as AGENT_ONLINE / AGENT_OFFLINE
messages to the bus.
"""

from __future__ import annotations

import asyncio
import logging

import psutil

from ..config import AgentPattern
from ..core.message_bus import MessageBus, MessageType
from .base import BaseConnector

log = logging.getLogger("vibe_deck.connectors.process_scanner")


class ProcessScanner(BaseConnector):
    """
    Periodically scans running processes and matches against configured patterns.

    On first detection → AGENT_ONLINE. On disappearance → AGENT_OFFLINE.
    """

    INTERVAL = 3.0  # seconds between scans

    def __init__(
        self,
        bus: MessageBus,
        patterns: list[AgentPattern],
        interval: float = INTERVAL,
    ) -> None:
        super().__init__("process-scanner", bus)
        self._patterns = patterns
        self._interval = interval
        self._known: dict[str, psutil.Process] = {}  # agent_name → process
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin periodic scanning."""
        await super().start()
        self._task = asyncio.create_task(self._scan_loop())
        log.info("Process scanner started (interval=%ss, patterns=%d)",
                 self._interval, len(self._patterns))

    async def stop(self) -> None:
        """Stop scanning."""
        await super().stop()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Process scanner stopped")

    async def _scan_loop(self) -> None:
        """Main scan loop. Runs until cancelled."""
        while self._running:
            try:
                await self._scan_once()
            except Exception:
                log.exception("Process scan failed")
            await asyncio.sleep(self._interval)

    async def _scan_once(self) -> None:
        """Single scan iteration."""
        current: dict[str, psutil.Process] = {}

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                info = proc.info
                name = info["name"]
                cmdline = info["cmdline"] or []
                cmdline_str = " ".join(cmdline)

                for pattern in self._patterns:
                    if name and pattern.process.lower() in name.lower():
                        # Check args_contains if specified
                        if pattern.args_contains:
                            if not all(
                                a.lower() in cmdline_str.lower()
                                for a in pattern.args_contains
                            ):
                                continue
                        # Check cmdline_regex if specified
                        if pattern.cmdline_regex:
                            import re
                            if not re.search(pattern.cmdline_regex, cmdline_str):
                                continue
                        current[pattern.name] = proc
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        # Detect new agents
        for name, proc in current.items():
            if name not in self._known:
                log.info("Agent detected: %s (pid=%s)", name, proc.pid)
                await self._publish(MessageType.AGENT_ONLINE, {
                    "agent_name": name,
                    "pid": proc.pid,
                })

        # Detect gone agents
        for name in list(self._known):
            if name not in current:
                log.info("Agent gone: %s", name)
                await self._publish(MessageType.AGENT_OFFLINE, {
                    "agent_name": name,
                })

        self._known = current
