"""
Tests for VibeDeck core types.
"""

import pytest
from vibe_deck.core.layout import DisplayState, LayoutFrame, WidgetState
from vibe_deck.core.message_bus import Message, MessageBus, MessageType


class TestDisplayState:
    def test_defaults(self):
        ds = DisplayState()
        assert ds.icon == ""
        assert ds.color == "#000000"
        assert ds.animation == "none"
        assert ds.label == ""
        assert ds.badge is None

    def test_custom(self):
        ds = DisplayState(icon="🐙", color="#22c55e", animation="crawl", label="Running", badge="3")
        assert ds.icon == "🐙"
        assert ds.color == "#22c55e"
        assert ds.animation == "crawl"
        assert ds.badge == "3"


class TestWidgetState:
    def test_create(self):
        ws = WidgetState(id="agent-1", type="agent")
        assert ws.id == "agent-1"
        assert ws.type == "agent"
        assert ws.display.icon == ""

    def test_update_display_partial(self):
        ws = WidgetState(id="agent-1", type="agent")
        ws.update_display(icon="🐙", color="#22c55e")
        assert ws.display.icon == "🐙"
        assert ws.display.color == "#22c55e"
        # unchanged
        assert ws.display.animation == "none"
        assert ws.display.label == ""

    def test_update_display_keeps_none(self):
        ws = WidgetState(id="agent-1", display=DisplayState(icon="🐙", color="green", label="Hi"))
        ws.update_display(animation="blink")
        assert ws.display.icon == "🐙"  # kept
        assert ws.display.animation == "blink"  # updated
        assert ws.display.label == "Hi"  # kept


class TestLayoutFrame:
    def test_for_deck(self):
        frame = LayoutFrame.for_deck("Stream Deck XL")
        assert frame.deck_type == "Stream Deck XL"
        assert frame.rows == 4
        assert frame.cols == 8
        assert len(frame.keymap) == 32

    def test_for_deck_mini(self):
        frame = LayoutFrame.for_deck("Stream Deck Mini")
        assert frame.rows == 3
        assert frame.cols == 2
        assert len(frame.keymap) == 6

    def test_place_widget(self):
        frame = LayoutFrame.for_deck("Stream Deck")
        ws = WidgetState(id="test-1", type="agent")
        frame.place_widget(ws, 3)
        assert frame.keymap[3] == "test-1"
        assert frame.widgets["test-1"] == ws

    def test_place_widget_moves_existing(self):
        frame = LayoutFrame.for_deck("Stream Deck")
        ws = WidgetState(id="test-1")
        frame.place_widget(ws, 2)
        frame.place_widget(ws, 5)
        assert frame.keymap[2] is None
        assert frame.keymap[5] == "test-1"

    def test_remove_widget(self):
        frame = LayoutFrame.for_deck("Stream Deck")
        ws = WidgetState(id="test-1")
        frame.place_widget(ws, 3)
        frame.remove_widget("test-1")
        assert frame.keymap[3] is None
        assert "test-1" not in frame.widgets

    def test_get_widget_at(self):
        frame = LayoutFrame.for_deck("Stream Deck")
        ws = WidgetState(id="test-1")
        frame.place_widget(ws, 3)
        assert frame.get_widget_at(3) == ws
        assert frame.get_widget_at(0) is None
        assert frame.get_widget_at(999) is None


class TestMessageBus:
    @pytest.mark.asyncio
    async def test_publish_subscribe(self):
        bus = MessageBus()
        q = bus.subscribe("test-consumer")
        assert bus.consumer_count == 1

        msg = Message(type=MessageType.WIDGET_ADDED, source="test", payload={"id": "w1"})
        await bus.publish(msg)

        received = await q.get()
        assert received.type == MessageType.WIDGET_ADDED
        assert received.payload["id"] == "w1"

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = MessageBus()
        bus.subscribe("test-consumer")
        bus.unsubscribe("test-consumer")
        assert bus.consumer_count == 0

    @pytest.mark.asyncio
    async def test_topic_filtering(self):
        bus = MessageBus()
        q = bus.subscribe("filtered-consumer", topics={"WIDGET_ADDED"})

        # Should be delivered
        await bus.publish(Message(type=MessageType.WIDGET_ADDED, source="test"))
        # Should NOT be delivered (different topic)
        await bus.publish(Message(type=MessageType.KEY_PRESSED, source="test"))

        received = await q.get()
        assert received.type == MessageType.WIDGET_ADDED
        assert q.empty()  # KEY_PRESSED was filtered out
