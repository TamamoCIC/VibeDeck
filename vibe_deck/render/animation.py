"""
Animation Engine — manages clip playback for hardware and sim renderers.

Architecture:
    Clip Registry  →  AnimationEngine
                        ├── get_sprite_frame(widget_id, clip_name, now) → Image | None
                        └── apply_effect(image, animation_type, now) → Image

Effect animations (pulse, blink, crawl, progress) are generated procedurally
by modifying the composed key image via PIL operations.

Sprite animations (mascot clips) are pre-authored frame sequences loaded from
asset files. The engine tracks per-widget playback state and returns the
correct frame for the current time.
"""
from __future__ import annotations

import logging
import math
import time as _time_mod
from typing import Optional

from PIL import Image, ImageEnhance

from ..core.types import AnimationType
from .animation_types import (
    AnimationCategory,
    AnimationClip,
    AnimationPlaybackState,
)

log = logging.getLogger("vibe_deck.render.animation")

# Effect timing constants (milliseconds)
_PULSE_PERIOD_MS = 1500.0
_BLINK_PERIOD_MS = 800.0
_CRAWL_PERIOD_MS = 3000.0
_PROGRESS_PERIOD_MS = 2000.0


class AnimationEngine:
    """Manages animation playback for all widgets across all terminals.

    Responsibilities:
      - Register/unregister AnimationClips by name
      - Track per-widget AnimationPlaybackState for sprite clips
      - Return the correct sprite frame for a widget at a given time
      - Apply procedural effects (pulse, blink, crawl, progress) to images
    """

    def __init__(self) -> None:
        self._clips: dict[str, AnimationClip] = {}
        self._playback: dict[str, AnimationPlaybackState] = {}
        # Frame cache: (clip_name, size) → {frame_index: Image}
        self._frame_cache: dict[tuple[str, tuple[int, int]], dict[int, Image.Image]] = {}

    # ── Clip Registry ──────────────────────────────

    def register_clip(self, clip: AnimationClip) -> None:
        """Register a named clip. Overwrites any existing clip with the same name."""
        self._clips[clip.name] = clip
        log.debug("Registered clip '%s' (%d frames, loop=%s)",
                   clip.name, len(clip.frames), clip.loop)

    def unregister_clip(self, name: str) -> None:
        """Remove a clip and clear related playback states and caches."""
        self._clips.pop(name, None)
        stale = [wid for wid, st in self._playback.items() if st.clip_name == name]
        for wid in stale:
            del self._playback[wid]
        # Clear cached frames for this clip
        stale_cache = [k for k in self._frame_cache if k[0] == name]
        for k in stale_cache:
            del self._frame_cache[k]
        if stale:
            log.debug("Cleared %d playback state(s) for clip '%s'", len(stale), name)

    @property
    def clip_names(self) -> list[str]:
        return list(self._clips.keys())

    # ── Sprite Frame Access ────────────────────────

    def get_sprite_frame(
        self,
        widget_id: str,
        clip_name: str,
        now: float,
        target_size: Optional[tuple[int, int]] = None,
    ) -> Optional[Image.Image]:
        """Return the current sprite frame for a widget, or None if no clip is active.

        Args:
            widget_id: Unique widget identifier (used for per-widget playback state).
            clip_name: Name of the registered clip to play (from SpriteClipId).
            now: time.monotonic() value for frame timing.
            target_size: Optional (w, h) to resize the frame to. Frames are cached
                         per (clip_name, size) so resize only happens once per size.

        Returns:
            The PIL Image for the current frame, or None if clip_name is "none"
            or the clip is not registered.
        """
        if not clip_name or clip_name == "none":
            return None

        clip = self._clips.get(clip_name)
        if clip is None:
            log.warning("Sprite clip '%s' not registered for widget %s", clip_name, widget_id)
            return None

        if not clip.frames:
            return None

        # Get or create playback state
        state = self._playback.get(widget_id)
        if state is None or state.clip_name != clip_name:
            state = AnimationPlaybackState(
                clip_name=clip_name,
                started_at=now,
            )
            self._playback[widget_id] = state

        # Compute elapsed time since playback started
        elapsed_ms = (now - state.started_at) * 1000.0
        frame = clip.get_frame_at(elapsed_ms)

        # Resize if needed, using cache to avoid per-frame resize cost
        if target_size is not None and frame.size != target_size:
            cache_key = (clip_name, target_size)
            if cache_key not in self._frame_cache:
                self._frame_cache[cache_key] = {}
            size_cache = self._frame_cache[cache_key]

            # Find which frame index this is (for cache key)
            frame_idx = 0
            accumulated = 0.0
            looped_elapsed = elapsed_ms % clip.total_duration_ms if (clip.loop and clip.total_duration_ms > 0) else min(elapsed_ms, float(clip.total_duration_ms))
            for idx, f in enumerate(clip.frames):
                accumulated += f.hold_ms
                if looped_elapsed < accumulated:
                    frame_idx = idx
                    break

            if frame_idx not in size_cache:
                size_cache[frame_idx] = frame.resize(target_size, Image.NEAREST)
            return size_cache[frame_idx]

        return frame

    def reset_sprite(self, widget_id: str) -> None:
        """Reset playback state for a widget (restarts its sprite clip from frame 0)."""
        self._playback.pop(widget_id, None)

    # ── Procedural Effects ─────────────────────────

    def apply_effect(
        self, image: Image.Image, animation: AnimationType, now: float
    ) -> Image.Image:
        """Apply a procedural effect animation to the given image.

        The image should already be a fully composed key render (color + icon
        + label + badge). The effect modifies it in place or returns a new image.

        Args:
            image: The composed key image to modify.
            animation: Which effect to apply.
            now: time.monotonic() value for animation phase.

        Returns:
            A new or modified PIL Image with the effect applied.
        """
        size = image.size

        if animation == AnimationType.PULSE:
            return _apply_pulse(image, now, size)
        elif animation == AnimationType.BLINK:
            return _apply_blink(image, now, size)
        elif animation == AnimationType.CRAWL:
            return _apply_crawl(image, now, size)
        elif animation == AnimationType.PROGRESS:
            return _apply_progress(image, now, size)
        else:
            return image


# ── Procedural Effect Implementations ──────────────

def _apply_pulse(img: Image.Image, now: float, size: tuple[int, int]) -> Image.Image:
    """Brightness oscillation: 1.5s period, sine wave between 40%-100% brightness."""
    t_ms = (now * 1000.0) % _PULSE_PERIOD_MS
    phase = (t_ms / _PULSE_PERIOD_MS) * 2.0 * math.pi
    factor = 0.4 + 0.6 * (math.sin(phase) + 1.0) / 2.0  # 0.4 .. 1.0
    dark = Image.new("RGB", size, (0, 0, 0))
    return Image.blend(img, dark, 1.0 - factor)


def _apply_blink(img: Image.Image, now: float, size: tuple[int, int]) -> Image.Image:
    """Quick flash: 0.8s period, 0.05s dip to near-black."""
    t_ms = (now * 1000.0) % _BLINK_PERIOD_MS
    if 400.0 < t_ms < 450.0:
        dark = Image.new("RGB", size, (10, 10, 10))
        return Image.blend(img, dark, 0.9)
    return img


def _apply_crawl(img: Image.Image, now: float, size: tuple[int, int]) -> Image.Image:
    """Brightness sweep across the key: 3s period, horizontal gradient pulse."""
    t_ms = (now * 1000.0) % _CRAWL_PERIOD_MS
    phase = (t_ms / _CRAWL_PERIOD_MS) * 2.0 * math.pi
    enhancer = ImageEnhance.Brightness(img)
    factor = 0.85 + 0.25 * math.sin(phase)
    return enhancer.enhance(factor)


def _apply_progress(img: Image.Image, now: float, size: tuple[int, int]) -> Image.Image:
    """Animated highlight bar sweeping left to right: 2s period.

    Uses Image.blend with a white strip overlay — fast, no per-pixel loops.
    """
    w, h = size
    t = (now * 1000.0) % _PROGRESS_PERIOD_MS / _PROGRESS_PERIOD_MS  # 0..1
    bar_x = int(t * w)
    bar_half = max(2, w // 10)

    # Build a white strip overlay at the bar position
    overlay = Image.new("RGB", size, (0, 0, 0))
    x_start = max(0, bar_x - bar_half)
    x_end = min(w, bar_x + bar_half + 1)
    if x_start < x_end:
        strip = Image.new("RGB", (x_end - x_start, h), (80, 80, 80))
        overlay.paste(strip, (x_start, 0))

    # Blend: 30% overlay at peak (center), tapering to 0% at edges
    # The strip has uniform brightness; the visible "fade" comes from
    # the narrow strip width blended against the original image.
    return Image.blend(img, overlay, 0.25)
