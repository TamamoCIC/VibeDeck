"""End-to-end integration test for Phase 3 pipeline."""
import time
from pathlib import Path

from PIL import Image, ImageDraw

from vibe_deck.render.animation_loader import load_clips
from vibe_deck.render.animation import AnimationEngine
from vibe_deck.core.types import DisplayState, WidgetState, WidgetType


def test_e2e_phase3_pipeline():
    # 1. Load clips from assets
    assets = Path("vibe_deck/assets")
    clips = load_clips(assets, [(72, 72)])
    print(f"1. Loaded {len(clips)} clip(s): {list(clips.keys())}")
    assert len(clips) >= 1, "Should load at least test_bounce"

    # 2. Register with engine
    engine = AnimationEngine()
    for name, clip in clips.items():
        engine.register_clip(clip)

    # 3. Simulate adapter flow: DisplayState with sprite
    ds = DisplayState(
        icon="🐙", color="#22c55e", animation="crawl",
        label="Running", sprite="test_bounce",
    )
    ws = WidgetState(
        id="test-widget-1", type=WidgetType.AGENT,
        display=ds, meta={"agent": "test", "status": "running"},
    )
    print(f"2. WidgetState: id={ws.id}, sprite={ws.display.sprite}")
    assert ws.display.sprite == "test_bounce"

    # 4. model_dump includes sprite
    dumped = ws.display.model_dump()
    assert dumped.get("sprite") == "test_bounce"
    print(f"3. model_dump includes sprite: {dumped['sprite']}")

    # 5. Hardware render path: get frames at different sizes
    now = time.monotonic()
    for size_name, size in [("XL", (96, 96)), ("Standard", (72, 72)), ("Mini", (80, 80))]:
        frame = engine.get_sprite_frame(
            "test-widget-1", "test_bounce", now, target_size=size,
        )
        assert frame is not None, f"No frame for {size_name}"
        assert frame.size == size, f"Wrong size for {size_name}: {frame.size}"
        assert frame.mode == "RGB"
        # Composite label
        img = frame.copy()
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, size[1] - 14, size[0], size[1]], fill=(0, 0, 0))
        print(f"   {size_name} ({size[0]}x{size[1]}): frame OK + label overlay")

    # 6. Animation progresses over time
    frame_t0 = engine.get_sprite_frame(
        "test-widget-1", "test_bounce", now, target_size=(72, 72),
    )
    frame_t1 = engine.get_sprite_frame(
        "test-widget-1", "test_bounce", now + 0.3, target_size=(72, 72),
    )
    # Different frames should differ (frame 0 has 120ms hold, so at 300ms we're on frame 1+)
    assert frame_t0.tobytes() != frame_t1.tobytes(), "Animation should advance"
    print("4. Animation progression verified")

    # 7. sprite="none" returns None
    ws2 = WidgetState(
        id="test-widget-2", type=WidgetType.AGENT,
        display=DisplayState(icon="🔴", color="#ef4444", label="Error", sprite="none"),
        meta={},
    )
    frame_none = engine.get_sprite_frame("test-widget-2", ws2.display.sprite, now)
    assert frame_none is None
    print("5. sprite=none correctly returns None")

    # 8. Frame cache works across multiple widgets
    frame_w1 = engine.get_sprite_frame(
        "w-a", "test_bounce", now, target_size=(96, 96),
    )
    frame_w2 = engine.get_sprite_frame(
        "w-b", "test_bounce", now, target_size=(96, 96),
    )
    # Same clip + same size + same time = same frame object (cached)
    assert frame_w1 is frame_w2
    print("6. Frame cache: same frame returned for different widgets")

    print("\n=== Phase 3 e2e pipeline: ALL CHECKS PASSED ===")


if __name__ == "__main__":
    test_e2e_phase3_pipeline()
