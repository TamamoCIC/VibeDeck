"""
Tests for VibeDeck core types and protocol.
"""

import tempfile
from pathlib import Path

import pytest
from pydantic import ValidationError

from vibe_deck.core.types import (
    AnimationType,
    DisplayState,
    LayoutFrame,
    WidgetState,
    WidgetType,
)
from vibe_deck.core.message_bus import Message, MessageBus, MessageType


class TestDisplayState:
    def test_defaults(self):
        ds = DisplayState()
        assert ds.icon == ""
        assert ds.color == "#000000"
        assert ds.animation == AnimationType.NONE
        assert ds.label == ""
        assert ds.badge is None
        assert ds.sprite == "none"

    def test_custom(self):
        ds = DisplayState(
            icon="🐙", color="#22c55e", animation="crawl", label="Running", badge="3"
        )
        assert ds.icon == "🐙"
        assert ds.color == "#22c55e"
        assert ds.animation == AnimationType.CRAWL
        assert ds.label == "Running"
        assert ds.badge == "3"
        assert ds.sprite == "none"  # default

    def test_sprite_field(self):
        """Sprite field carries the clip name for pixel-art animations."""
        ds = DisplayState(
            icon="🐙", color="#22c55e", animation="crawl",
            label="Running", sprite="test_bounce",
        )
        assert ds.sprite == "test_bounce"
        # JSON roundtrip preserves sprite
        data = ds.model_dump()
        assert data["sprite"] == "test_bounce"
        ds2 = DisplayState(**data)
        assert ds2.sprite == "test_bounce"

    def test_invalid_color(self):
        """Color must be hex format."""
        with pytest.raises(ValidationError):
            DisplayState(color="green")

    def test_invalid_animation(self):
        """Animation must be a valid enum value."""
        with pytest.raises(ValidationError):
            DisplayState(animation="flying")

    def test_label_too_long(self):
        """Label max 12 chars."""
        with pytest.raises(ValidationError):
            DisplayState(label="a" * 13)

    def test_json_schema(self):
        """Pydantic exports valid JSON Schema."""
        schema = DisplayState.model_json_schema()
        assert schema["type"] == "object"
        assert "icon" in schema["properties"]
        assert "color" in schema["properties"]
        assert "animation" in schema["properties"]

    def test_json_roundtrip(self):
        """DisplayState serializes to/from JSON correctly."""
        ds = DisplayState(icon="🐙", color="#22c55e", animation="crawl", label="Run")
        data = ds.model_dump()
        ds2 = DisplayState.model_validate(data)
        assert ds2.icon == ds.icon
        assert ds2.color == ds.color
        assert ds2.animation == ds.animation


class TestWidgetState:
    def test_create(self):
        ws = WidgetState(id="agent-1", type=WidgetType.AGENT)
        assert ws.id == "agent-1"
        assert ws.type == WidgetType.AGENT
        assert ws.display.icon == ""

    def test_update_display_partial(self):
        ws = WidgetState(id="agent-1")
        ws.update_display(icon="🐙", color="#22c55e")
        assert ws.display.icon == "🐙"
        assert ws.display.color == "#22c55e"
        # unchanged
        assert ws.display.animation == AnimationType.NONE
        assert ws.display.label == ""

    def test_update_display_keeps_none(self):
        ws = WidgetState(id="agent-1", display=DisplayState(icon="🐙", color="#22c55e", label="Hi"))
        ws.update_display(animation="blink")
        assert ws.display.icon == "🐙"  # kept
        assert ws.display.animation == AnimationType.BLINK  # updated
        assert ws.display.label == "Hi"  # kept

    def test_json_roundtrip(self):
        ws = WidgetState(
            id="agent-1",
            type=WidgetType.AGENT,
            display=DisplayState(icon="🐙", color="#22c55e", animation="crawl", label="Running"),
            meta={"agent": "Claude Code", "session_id": "abc123"},
        )
        data = ws.model_dump()
        ws2 = WidgetState.model_validate(data)
        assert ws2.id == ws.id
        assert ws2.type == ws.type
        assert ws2.display.icon == ws.display.icon
        assert ws2.meta == ws.meta


class TestLayoutFrame:
    def test_for_grid(self):
        frame = LayoutFrame.for_grid(4, 8, "4x8")
        assert frame.display_name == "4x8"
        assert frame.rows == 4
        assert frame.cols == 8
        assert len(frame.keymap) == 32

    def test_for_grid_mini(self):
        frame = LayoutFrame.for_grid(3, 2, "3x2")
        assert frame.rows == 3
        assert frame.cols == 2
        assert len(frame.keymap) == 6

    def test_for_deck_legacy(self):
        """Legacy for_deck still works via for_grid."""
        frame = LayoutFrame.for_deck("Stream Deck XL")
        assert frame.display_name == "Stream Deck XL"
        assert frame.rows == 4
        assert frame.cols == 8

    def test_for_grid_default_name(self):
        """for_grid auto-generates display_name if omitted."""
        frame = LayoutFrame.for_grid(3, 5)
        assert frame.display_name == "3x5"

    def test_place_widget(self):
        frame = LayoutFrame.for_grid(3, 5, "3x5")
        ws = WidgetState(id="test-1", type=WidgetType.AGENT)
        frame.place_widget(ws, 3)
        assert frame.keymap[3] == "test-1"
        assert frame.widgets["test-1"] == ws

    def test_place_widget_moves_existing(self):
        frame = LayoutFrame.for_grid(3, 5, "3x5")
        ws = WidgetState(id="test-1")
        frame.place_widget(ws, 2)
        frame.place_widget(ws, 5)
        assert frame.keymap[2] is None
        assert frame.keymap[5] == "test-1"

    def test_remove_widget(self):
        frame = LayoutFrame.for_grid(3, 5, "3x5")
        ws = WidgetState(id="test-1")
        frame.place_widget(ws, 3)
        frame.remove_widget("test-1")
        assert frame.keymap[3] is None
        assert "test-1" not in frame.widgets

    def test_get_widget_at(self):
        frame = LayoutFrame.for_grid(3, 5, "3x5")
        ws = WidgetState(id="test-1")
        frame.place_widget(ws, 3)
        assert frame.get_widget_at(3) == ws
        assert frame.get_widget_at(0) is None
        assert frame.get_widget_at(999) is None

    def test_yaml_roundtrip(self):
        """LayoutFrame survives YAML round-trip."""
        frame = LayoutFrame.for_grid(4, 8, "4x8")
        ws = WidgetState(
            id="agent-1",
            type=WidgetType.AGENT,
            display=DisplayState(icon="🐙", color="#22c55e", animation="crawl", label="Running"),
            meta={"pid": 1234},
        )
        frame.place_widget(ws, 0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            frame.to_yaml(f.name)

        loaded = LayoutFrame.from_yaml(f.name)
        assert loaded.display_name == frame.display_name
        assert loaded.rows == frame.rows
        assert loaded.cols == frame.cols
        assert loaded.keymap[0] == "agent-1"
        assert loaded.widgets["agent-1"].display.icon == "🐙"

        Path(f.name).unlink()

    def test_yaml_roundtrip_legacy(self):
        """Legacy YAML with deck_type still loads correctly."""
        import yaml
        legacy = {
            "name": "legacy-layout",
            "deck_type": "Stream Deck XL",
            "rows": 4,
            "cols": 8,
            "widgets": [],
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            yaml.safe_dump(legacy, f)
            f.flush()
            loaded = LayoutFrame.from_yaml(f.name)
            assert loaded.display_name == "Stream Deck XL"  # migrated from deck_type
            assert loaded.rows == 4
            assert loaded.cols == 8
        Path(f.name).unlink()

    def test_json_roundtrip(self):
        """LayoutFrame serializes to/from JSON correctly."""
        frame = LayoutFrame.for_grid(3, 5, "3x5")
        ws = WidgetState(id="test-1", display=DisplayState(icon="🦊"))
        frame.place_widget(ws, 0)

        data = frame.model_dump()
        frame2 = LayoutFrame.model_validate(data)
        assert frame2.display_name == frame.display_name
        assert frame2.keymap[0] == "test-1"
        assert frame2.widgets["test-1"].display.icon == "🦊"

    def test_key_count_property(self):
        frame = LayoutFrame.for_grid(4, 8, "4x8")
        assert frame.key_count == 32

        frame2 = LayoutFrame.for_grid(3, 2, "3x2")
        assert frame2.key_count == 6


class TestLayoutEngine:
    """Tests for the multi-terminal LayoutEngine."""

    def test_default_terminal_exists(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        assert "default" in engine.list_terminals()
        assert engine.frame is not None
        assert engine.frame.rows == 4
        assert engine.frame.cols == 8

    def test_register_terminal(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        frame = engine.register_terminal("phone-01", 3, 5, "phone-3x5")
        assert "phone-01" in engine.list_terminals()
        assert frame.rows == 3
        assert frame.cols == 5
        assert frame.display_name == "phone-3x5"

    def test_register_terminal_idempotent(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        f1 = engine.register_terminal("t1", 3, 5, "test")
        f2 = engine.register_terminal("t1", 8, 8, "ignored")
        assert f1 is f2  # same frame returned
        assert f1.rows == 3  # original dimensions preserved

    def test_unregister_terminal(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        engine.register_terminal("phone-01", 3, 5, "phone")
        engine.unregister_terminal("phone-01")
        assert "phone-01" not in engine.list_terminals()
        assert engine.get_frame("phone-01") is None

    def test_unregister_default_is_noop(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        engine.unregister_terminal("default")
        assert "default" in engine.list_terminals()  # default is preserved

    def test_get_frame_unknown_returns_none(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        assert engine.get_frame("nonexistent") is None

    def test_update_widget_routes_to_terminal(self):
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import WidgetState, WidgetType
        engine = LayoutEngine()
        engine.register_terminal("t1", 3, 5, "test1")
        engine.register_terminal("t2", 4, 8, "test2")

        ws = WidgetState(id="w1", type=WidgetType.AGENT)
        engine.update_widget(ws, "t1")

        f1 = engine.get_frame("t1")
        f2 = engine.get_frame("t2")
        assert "w1" in f1.widgets
        assert "w1" not in f2.widgets  # isolated

    def test_update_widget_unknown_terminal(self):
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import WidgetState
        engine = LayoutEngine()
        result = engine.update_widget(WidgetState(id="x"), "ghost")
        assert result is None

    def test_list_terminals(self):
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        engine.register_terminal("a", 3, 2, "a")
        engine.register_terminal("b", 3, 5, "b")
        terminals = engine.list_terminals()
        assert "default" in terminals
        assert "a" in terminals
        assert "b" in terminals
        assert len(terminals) == 3

    def test_remove_widget(self):
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import WidgetState, WidgetType
        engine = LayoutEngine()
        engine.register_terminal("t1", 3, 5, "test")
        ws = WidgetState(id="w1", type=WidgetType.AGENT)
        engine.update_widget(ws, "t1")
        engine.remove_widget("w1", "t1")
        f1 = engine.get_frame("t1")
        assert "w1" not in f1.widgets

    # ── New: update_widget_across_terminals ────────

    def test_update_widget_across_terminals_updates_existing_only(self):
        """update_widget_across_terminals updates existing widgets, never creates."""
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import DisplayState, WidgetState, WidgetType
        engine = LayoutEngine()
        engine.register_terminal("t1", 3, 5, "test1")
        engine.register_terminal("t2", 4, 8, "test2")

        # Place widget on t1 only
        ws = WidgetState(id="agent-1", type=WidgetType.AGENT,
                         display=DisplayState(icon="🐙", label="Running"))
        engine.upsert_widget(ws, "t1")

        # Now update across all terminals — should only touch t1
        updated_ws = WidgetState(id="agent-1", type=WidgetType.AGENT,
                                 display=DisplayState(icon="🦊", label="Idle"))
        updated = engine.update_widget_across_terminals(updated_ws)

        assert updated == ["t1"]  # only t1 had the widget
        f1 = engine.get_frame("t1")
        f2 = engine.get_frame("t2")
        assert f1.widgets["agent-1"].display.icon == "🦊"
        assert "agent-1" not in f2.widgets  # NOT auto-created on t2

    def test_update_widget_across_terminals_no_existing(self):
        """Returns empty list when widget doesn't exist on any terminal."""
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import DisplayState, WidgetState
        engine = LayoutEngine()
        ws = WidgetState(id="ghost", display=DisplayState(icon="👻"))
        updated = engine.update_widget_across_terminals(ws)
        assert updated == []

    # ── New: named layouts ────────────────────────

    def test_save_and_list_layouts(self):
        """save_layout_as creates a named layout, list_layouts returns it."""
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import WidgetState, WidgetType
        engine = LayoutEngine()
        engine.register_terminal("t1", 3, 5, "test")
        engine.update_widget(WidgetState(id="w1", type=WidgetType.AGENT), "t1")

        result = engine.save_layout_as("my-layout", "t1", to_disk=False)
        assert result is None  # to_disk=False means no path
        layouts = engine.list_layouts("t1")
        assert "my-layout" in layouts

    def test_switch_layout(self):
        """switch_layout changes the active frame and preserves the old one."""
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import WidgetState, WidgetType
        engine = LayoutEngine()
        engine.register_terminal("t1", 3, 5, "test")

        # Set up widget on active layout, then save it
        engine.update_widget(WidgetState(id="w1", type=WidgetType.AGENT), "t1")
        engine.save_layout_as("layout-a", "t1", to_disk=False)

        # Clear widget from active, save as layout-b
        engine.remove_widget("w1", "t1")
        engine.save_layout_as("layout-b", "t1", to_disk=False)

        # Switch back to layout-a — widget should reappear
        frame = engine.switch_layout("t1", "layout-a")
        assert frame is not None
        assert "w1" in frame.widgets

        # Switch to layout-b — widget should be gone
        frame = engine.switch_layout("t1", "layout-b")
        assert frame is not None
        assert "w1" not in frame.widgets

    def test_switch_layout_unknown(self):
        """switch_layout returns None for unknown terminal or layout."""
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        assert engine.switch_layout("ghost", "x") is None
        assert engine.switch_layout("default", "nonexistent") is None

    def test_load_layout_with_name(self):
        """load_layout sets active and can store as named layout."""
        import tempfile
        from pathlib import Path
        from vibe_deck.core.layout import LayoutEngine
        from vibe_deck.core.types import DisplayState, LayoutFrame, WidgetState, WidgetType

        engine = LayoutEngine()
        engine.register_terminal("t1", 3, 5, "test")

        frame = LayoutFrame.for_grid(3, 5, "test")
        frame.place_widget(WidgetState(
            id="w1", type=WidgetType.AGENT,
            display=DisplayState(icon="🐙")), 0)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            frame.to_yaml(f.name)

        result = engine.load_layout(f.name, "t1", as_name="loaded")
        assert result is not None
        assert "w1" in result.widgets
        assert "loaded" in engine.list_layouts("t1")

        Path(f.name).unlink()

    def test_save_layout_as_unknown_terminal(self):
        """save_layout_as returns None for unknown terminal."""
        from vibe_deck.core.layout import LayoutEngine
        engine = LayoutEngine()
        assert engine.save_layout_as("x", "ghost") is None


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

    @pytest.mark.asyncio
    async def test_multiple_consumers(self):
        bus = MessageBus()
        q1 = bus.subscribe("c1")
        q2 = bus.subscribe("c2")

        await bus.publish(Message(type=MessageType.WIDGET_ADDED, source="test"))

        assert await q1.get()
        assert await q2.get()
        assert q1.empty()
        assert q2.empty()
