"""
VibeDeck Connector base class.

All connectors extend this and push WidgetState updates to the MessageBus.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from ..core.message_bus import Message, MessageBus, MessageType


class BaseConnector(ABC):
    """
    Abstract base for all Connectors.

    Subclasses implement `start()` and `stop()` for their specific
    data source (file watch, SSE client, process scanner, etc.).
    """

    def __init__(self, connector_id: str, bus: MessageBus) -> None:
        self.id = connector_id
        self._bus = bus
        self._running = False

    @property
    def running(self) -> bool:
        return self._running

    @abstractmethod
    async def start(self) -> None:
        """Begin watching / connecting. Called by the Core supervisor."""
        self._running = True

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect. Called on SIGTERM."""
        self._running = False

    async def _publish(self, msg_type: MessageType, payload: dict | None = None) -> None:
        """Push a message to the bus."""
        await self._bus.publish(
            Message(type=msg_type, source=self.id, payload=payload or {})
        )
