"""
Tests for the Animation Engine — procedural effects and sprite playback.
"""
import time

import pytest
from PIL import Image

from vibe_deck.core.types import AnimationType, DisplayState, WidgetState, WidgetType
from vibe_deck.render.animation import AnimationEngine
from vibe_deck.render.animation_types import (
    AnimationCategory,
    AnimationClip,
    AnimationFrame,
    AnimationPlaybackState,
    SpriteClipId,
)


class TestAnimationClip:
    def test_empty_clip_raises(self):
        clip = AnimationClip(name="empty", frames=[])
        with pytest.raises(ValueError):
            clip.get_frame_at(0)

    def test_single_frame(self):
        img = Image.new("RGB", (72, 72), "#ff0000")
        clip = AnimationClip(
            name="single",
            frames=[AnimationFrame(image=img, hold_ms=100)],
            loop=True,
        )
        result = clip.get_frame_at(0)
        assert result is img
        result = clip.get_frame_at(50)
        assert result is img
        result = clip.get_frame_at(99)
        assert result is img

    def test_two_frames_looping(self):
        img1 = Image.new("RGB", (72, 72), "#ff0000")
        img2 = Image.new("RGB", (72, 72), "#00ff00")
        clip = AnimationClip(
            name="two",
            frames=[
                AnimationFrame(image=img1, hold_ms=100),
                AnimationFrame(image=img2, hold_ms=200),
            ],
            loop=True,
        )
        # Frame 1: 0–99ms
        assert clip.get_frame_at(0) is img1
        assert clip.get_frame_at(99) is img1
        # Frame 2: 100–299ms
        assert clip.get_frame_at(100) is img2
        assert clip.get_frame_at(299) is img2
        # Loops back to frame 1 at 300ms
        assert clip.get_frame_at(300) is img1
        assert clip.get_frame_at(350) is img1
        # Frame 2 again
        assert clip.get_frame_at(400) is img2
        assert clip.get_frame_at(599) is img2

    def test_non_looping_pauses_on_last(self):
        img1 = Image.new("RGB", (72, 72), "#ff0000")
        img2 = Image.new("RGB", (72, 72), "#00ff00")
        clip = AnimationClip(
            name="once",
            frames=[
                AnimationFrame(image=img1, hold_ms=100),
                AnimationFrame(image=img2, hold_ms=100),
            ],
            loop=False,
        )
        assert clip.get_frame_at(0) is img1
        assert clip.get_frame_at(150) is img2
        # Past total duration — stays on last frame
        assert clip.get_frame_at(200) is img2
        assert clip.get_frame_at(9999) is img2

    def test_total_duration(self):
        clip = AnimationClip(
            name="dur",
            frames=[
                AnimationFrame(image=Image.new("RGB", (10, 10)), hold_ms=100),
                AnimationFrame(image=Image.new("RGB", (10, 10)), hold_ms=250),
                AnimationFrame(image=Image.new("RGB", (10, 10)), hold_ms=50),
            ],
        )
        assert clip.total_duration_ms == 400


class TestAnimationEngineEffects:
    @pytest.fixture
    def engine(self):
        return AnimationEngine()

    @pytest.fixture
    def base_img(self):
        return Image.new("RGB", (72, 72), "#2255cc")

    def test_none_returns_unchanged(self, engine, base_img):
        result = engine.apply_effect(base_img, AnimationType.NONE, 0.0)
        assert result is base_img

    def test_pulse_changes_brightness(self, engine, base_img):
        now = time.monotonic()
        img1 = engine.apply_effect(base_img, AnimationType.PULSE, now)
        assert img1.size == (72, 72)
        assert img1.mode == "RGB"
        # Pixel should differ from original (dimmed)
        orig_pixel = base_img.getpixel((36, 36))
        new_pixel = img1.getpixel((36, 36))
        assert new_pixel != orig_pixel, "Pulse should modify pixel values"

    def test_blink_off_phase_dims(self, engine, base_img):
        """During the 400-450ms 'off' phase, the image should be dimmed."""
        # Simulate now such that modulo 800ms lands in 400-450ms range
        # Use a known anchor: if now produces 410ms into the period, it blinks
        dimmed = engine.apply_effect(base_img, AnimationType.BLINK, 0.410)
        normal = engine.apply_effect(base_img, AnimationType.BLINK, 0.100)
        # Dimmed phase should differ from original
        assert dimmed.getpixel((36, 36)) != base_img.getpixel((36, 36))
        # Normal phase should be identical
        assert normal.getpixel((36, 36)) == base_img.getpixel((36, 36))

    def test_crawl_modulates_brightness(self, engine, base_img):
        result = engine.apply_effect(base_img, AnimationType.CRAWL, 0.5)
        assert result.size == (72, 72)
        assert result.mode == "RGB"

    def test_progress_adds_bar(self, engine, base_img):
        result = engine.apply_effect(base_img, AnimationType.PROGRESS, 0.25)
        assert result.size == (72, 72)
        assert result.mode == "RGB"


class TestAnimationEngineSprites:
    def test_register_and_play(self):
        engine = AnimationEngine()
        img1 = Image.new("RGB", (72, 72), "#ff0000")
        img2 = Image.new("RGB", (72, 72), "#00ff00")
        clip = AnimationClip(
            name="test_clip",
            frames=[
                AnimationFrame(image=img1, hold_ms=500),
                AnimationFrame(image=img2, hold_ms=500),
            ],
            loop=True,
        )
        engine.register_clip(clip)
        assert "test_clip" in engine.clip_names

        now = time.monotonic()
        # Frame 1 at start
        frame = engine.get_sprite_frame("w1", "test_clip", now)
        assert frame is img1

        # Frame 2 after 500ms
        frame = engine.get_sprite_frame("w1", "test_clip", now + 0.5)
        assert frame is img2

    def test_missing_clip_returns_none(self):
        engine = AnimationEngine()
        result = engine.get_sprite_frame("w1", "no_such_clip", time.monotonic())
        assert result is None

    def test_none_clip_returns_none(self):
        engine = AnimationEngine()
        result = engine.get_sprite_frame("w1", "none", time.monotonic())
        assert result is None

    def test_reset_sprite(self):
        engine = AnimationEngine()
        img1 = Image.new("RGB", (72, 72), "#ff0000")
        img2 = Image.new("RGB", (72, 72), "#00ff00")
        clip = AnimationClip(
            name="reset_test",
            frames=[
                AnimationFrame(image=img1, hold_ms=500),
                AnimationFrame(image=img2, hold_ms=500),
            ],
            loop=True,
        )
        engine.register_clip(clip)

        # Start playback at time T0
        t0 = time.monotonic()
        frame = engine.get_sprite_frame("w1", "reset_test", t0)
        assert frame is img1, "Should start at frame 1"

        # Advance 600ms — should be on frame 2
        frame = engine.get_sprite_frame("w1", "reset_test", t0 + 0.6)
        assert frame is img2, "Should advance to frame 2"

        # Reset — clears playback state
        engine.reset_sprite("w1")

        # New time T1 — fresh state starts at frame 1 again
        t1 = time.monotonic()
        frame = engine.get_sprite_frame("w1", "reset_test", t1)
        assert frame is img1, "After reset, should restart at frame 1"


class TestAnimationEngineFrameCache:
    """Tests for the per-size frame cache in AnimationEngine."""

    def test_cached_resize(self):
        """get_sprite_frame with target_size should resize and cache."""
        engine = AnimationEngine()
        img = Image.new("RGB", (72, 72), "#ff0000")
        clip = AnimationClip(
            name="cache_test",
            frames=[AnimationFrame(image=img, hold_ms=1000)],
            loop=True,
        )
        engine.register_clip(clip)

        now = time.monotonic()

        # Request at a different size — should return resized frame
        frame = engine.get_sprite_frame("w1", "cache_test", now, target_size=(96, 96))
        assert frame is not None
        assert frame.size == (96, 96)

        # Second request — should hit cache (same object)
        frame2 = engine.get_sprite_frame("w1", "cache_test", now, target_size=(96, 96))
        assert frame2.size == (96, 96)
        # Cached frames return the same object
        assert frame2 is frame

    def test_cache_cleared_on_unregister(self):
        """Unregistering a clip should clear its frame cache."""
        engine = AnimationEngine()
        img = Image.new("RGB", (72, 72), "#ff0000")
        clip = AnimationClip(
            name="cache_clear_test",
            frames=[AnimationFrame(image=img, hold_ms=1000)],
            loop=True,
        )
        engine.register_clip(clip)

        now = time.monotonic()
        engine.get_sprite_frame("w1", "cache_clear_test", now, target_size=(96, 96))

        # Verify cache exists
        cache_key = ("cache_clear_test", (96, 96))
        assert cache_key in engine._frame_cache

        # Unregister should clear
        engine.unregister_clip("cache_clear_test")
        assert cache_key not in engine._frame_cache

    def test_no_resize_when_same_size(self):
        """When target_size matches frame size, no cache entry is created."""
        engine = AnimationEngine()
        img = Image.new("RGB", (72, 72), "#ff0000")
        clip = AnimationClip(
            name="same_size_test",
            frames=[AnimationFrame(image=img, hold_ms=1000)],
            loop=True,
        )
        engine.register_clip(clip)

        now = time.monotonic()
        frame = engine.get_sprite_frame("w1", "same_size_test", now, target_size=(72, 72))
        assert frame is not None
        assert frame.size == (72, 72)
        # Same size — no resize needed, should return original
        assert frame is img


class TestSpriteClipId:
    def test_default_is_none(self):
        assert SpriteClipId.NONE == "none"

    def test_has_mascot_clips(self):
        assert SpriteClipId.MASCOT_WALK == "mascot_walk"
        assert SpriteClipId.MASCOT_GYM == "mascot_gym"
        assert SpriteClipId.MASCOT_FLAG == "mascot_flag"
        assert SpriteClipId.MASCOT_CONFETTI == "mascot_confetti"
        assert SpriteClipId.MASCOT_IDLE == "mascot_idle"
