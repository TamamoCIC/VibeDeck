"""
Animation Engine Type System.

Defines the frame-sequence data model for sprite and effect animations.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from PIL import Image


class AnimationCategory(str, Enum):
    """High-level category determining how the animation is rendered."""
    EFFECT = "effect"       # Procedural: pulse, blink, crawl, progress
    SPRITE = "sprite"       # Pre-authored frame sequences: mascot clips


class SpriteClipId(str, Enum):
    """Named sprite animation clips available for mascot/pixel-art use.

    Referenced by adapters via WidgetState.meta["sprite"].
    """
    NONE = "none"
    MASCOT_IDLE = "mascot_idle"
    MASCOT_WALK = "mascot_walk"
    MASCOT_FLAG = "mascot_flag"
    MASCOT_CONFETTI = "mascot_confetti"
    MASCOT_GYM = "mascot_gym"


@dataclass
class AnimationFrame:
    """A single frame in an animation sequence.

    Attributes:
        image: Pre-rendered PIL Image at the target key size.
        hold_ms: How long (in milliseconds) this frame is displayed.
    """
    image: Image.Image
    hold_ms: int


@dataclass
class AnimationClip:
    """A named sequence of frames that plays as an animation.

    Attributes:
        name: Unique clip identifier (matches SpriteClipId value).
        frames: Ordered list of AnimationFrame.
        loop: If True, repeats forever; if False, pauses on last frame.
        category: EFFECT (procedural) or SPRITE (pre-authored frames).
    """
    name: str
    frames: list[AnimationFrame]
    loop: bool = True
    category: AnimationCategory = AnimationCategory.SPRITE

    @property
    def total_duration_ms(self) -> int:
        """Total playback duration of one loop in milliseconds."""
        return sum(f.hold_ms for f in self.frames)

    def get_frame_at(self, elapsed_ms: float) -> Image.Image:
        """Return the frame that should be displayed at the given elapsed time.

        Args:
            elapsed_ms: Milliseconds since playback started (or modulo'd if looping).

        Returns:
            The PIL Image for the current frame.
        """
        if not self.frames:
            raise ValueError(f"Clip '{self.name}' has no frames")

        if self.loop and self.total_duration_ms > 0:
            elapsed_ms = elapsed_ms % self.total_duration_ms
        else:
            elapsed_ms = min(elapsed_ms, float(self.total_duration_ms))

        accumulated = 0.0
        for frame in self.frames:
            accumulated += frame.hold_ms
            if elapsed_ms < accumulated:
                return frame.image
        return self.frames[-1].image


@dataclass
class AnimationPlaybackState:
    """Per-widget playback state tracked by the AnimationEngine.

    Each widget with an active sprite animation has one of these.
    Effect animations are stateless and don't use this.
    """
    clip_name: str = ""
    started_at: float = 0.0  # time.monotonic() when playback began
