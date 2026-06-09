"""
VibeDeck Adapter Manager — manages adapter lifecycle.

Listens for AGENT_ONLINE / AGENT_OFFLINE messages and creates/starts
the appropriate adapter instance for each detected agent. Adapters run
as background asyncio tasks and publish WIDGET_STATE_UPDATE to the
MessageBus when agent state changes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .message_bus import MessageBus, MessageType

log = logging.getLogger("vibe_deck.core.adapter_manager")

# Agent name → adapter class mapping
_ADAPTER_REGISTRY: dict[str, type] = {}


def register_adapter(agent_name: str, adapter_cls: type) -> None:
    """Register an adapter class for an agent name."""
    _ADAPTER_REGISTRY[agent_name] = adapter_cls


class AdapterManager:
    """
    Watches the MessageBus for AGENT_ONLINE / AGENT_OFFLINE and manages
    adapter instances.

    Each adapter runs as a background asyncio task, keyed by PID so
    multiple instances of the same agent type can coexist.  When the
    agent process exits, the adapter is stopped and cleaned up.
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._adapters: dict[int, Any] = {}  # pid → adapter instance
        self._tasks: dict[int, asyncio.Task] = {}  # pid → task
        self._queue: asyncio.Queue | None = None
        self._running = False
        self._consumer_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Begin listening for agent lifecycle events."""
        self._running = True
        self._queue = self._bus.subscribe("adapter-manager", topics={"AGENT_ONLINE", "AGENT_OFFLINE"})
        self._consumer_task = asyncio.create_task(self._consume())
        log.info("AdapterManager started (registered: %s)", list(_ADAPTER_REGISTRY.keys()))

    async def stop(self) -> None:
        """Stop all adapters and clean up."""
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        # Stop all running adapters
        for pid, adapter in list(self._adapters.items()):
            try:
                await adapter.stop()
            except Exception:
                log.debug("Error stopping adapter pid=%d", pid, exc_info=True)
        self._adapters.clear()
        self._tasks.clear()

        if self._queue:
            self._bus.unsubscribe("adapter-manager")
        log.info("AdapterManager stopped")

    async def start_adapter(self, agent_name: str, *, pid: int = 0, **kwargs) -> Any | None:
        """Create and start an adapter for the given agent process.

        Adapters are keyed by *pid* so multiple instances of the same
        agent type (e.g. two Claude Code sessions) coexist independently.
        """
        adapter_cls = _ADAPTER_REGISTRY.get(agent_name)
        if adapter_cls is None:
            log.debug("No adapter registered for agent %r", agent_name)
            return None

        if pid and pid in self._adapters:
            log.debug("Adapter for %r (pid=%d) already running", agent_name, pid)
            return self._adapters[pid]

        try:
            adapter = adapter_cls(name=agent_name, bus=self._bus, pid=pid, **kwargs)
            self._adapters[pid] = adapter
            task = asyncio.create_task(adapter.start())
            self._tasks[pid] = task
            log.info("Adapter started: %s (pid=%d)", agent_name, pid)
            return adapter
        except Exception:
            log.exception("Failed to start adapter for %r (pid=%d)", agent_name, pid)
            return None

    async def stop_adapter(self, pid: int) -> None:
        """Stop and remove the adapter for the given PID."""
        adapter = self._adapters.pop(pid, None)
        task = self._tasks.pop(pid, None)
        if adapter:
            try:
                await adapter.stop()
            except Exception:
                pass
        if task and not task.done():
            task.cancel()

    async def _consume(self) -> None:
        """Main message consumer loop."""
        while self._running:
            try:
                msg = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                if msg.type == MessageType.AGENT_ONLINE:
                    agent_name = msg.payload.get("agent_name", "")
                    pid = msg.payload.get("pid", 0)
                    if agent_name and pid:
                        await self.start_adapter(agent_name, pid=pid)
                elif msg.type == MessageType.AGENT_OFFLINE:
                    pid = msg.payload.get("pid", 0)
                    if pid:
                        await self.stop_adapter(pid)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                log.exception("AdapterManager consumer error")
