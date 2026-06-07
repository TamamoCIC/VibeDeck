"""
VibeDeck Message Bus — internal asyncio message routing.

All Connectors push WidgetState updates into the bus. The LayoutEngine
consumes them and recomputes the LayoutFrame. The Render targets pull
frames from the frame publisher.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any


class MessageType(Enum):
    """Message types flowing through the bus."""

    WIDGET_STATE_UPDATE = auto()    # A Widget's display state changed
    WIDGET_ADDED = auto()           # New Widget registered
    WIDGET_REMOVED = auto()         # Widget unregistered
    LAYOUT_CHANGED = auto()         # Layout was loaded/switched
    AGENT_ONLINE = auto()           # Agent process detected
    AGENT_OFFLINE = auto()          # Agent process exited
    APPROVAL_REQUESTED = auto()    # Agent needs user approval
    APPROVAL_RESOLVED = auto()     # Approval answered
    KEY_PRESSED = auto()           # Physical/simulated key press
    DECK_CONNECTED = auto()        # Stream Deck hardware attached
    DECK_DISCONNECTED = auto()     # Stream Deck hardware detached
    SYSTEM_EVENT = auto()          # Generic system event


@dataclass
class Message:
    """A single message on the bus."""

    type: MessageType
    source: str  # connector id, "core", "render", "web"
    payload: dict[str, Any] = field(default_factory=dict)


class MessageBus:
    """
    Internal publish/subscribe message bus.

    Each consumer gets its own asyncio.Queue. Publishers push messages
    into all subscribed queues.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Message]] = {}
        self._subscribers: dict[str, set[str]] = {}

    def subscribe(self, consumer_id: str, topics: set[str] | None = None) -> asyncio.Queue[Message]:
        """
        Register a consumer and return its message queue.

        Args:
            consumer_id: Unique consumer name (e.g. "layout-engine", "render-sim").
            topics: Set of MessageType names to receive. None = receive all.

        Returns:
            asyncio.Queue that will receive matching messages.
        """
        q: asyncio.Queue[Message] = asyncio.Queue()
        self._queues[consumer_id] = q
        if topics:
            self._subscribers[consumer_id] = topics
        return q

    def unsubscribe(self, consumer_id: str) -> None:
        """Remove a consumer."""
        self._queues.pop(consumer_id, None)
        self._subscribers.pop(consumer_id, None)

    async def publish(self, msg: Message) -> None:
        """Send a message to all interested consumers."""
        for consumer_id, q in self._queues.items():
            topics = self._subscribers.get(consumer_id)
            if topics is None or msg.type.name in topics:
                await q.put(msg)

    @property
    def consumer_count(self) -> int:
        return len(self._queues)
