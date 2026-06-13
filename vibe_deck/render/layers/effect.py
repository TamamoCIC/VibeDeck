"""Effect layer — procedural animation post-process.

Unlike other layers, effects don't produce an RGBA overlay — they
modify the composited image below them (brightness modulation,
highlight sweeps, etc.).  The Renderer handles this as a post-process
step after compositing all visual layers, by calling
``AnimationEngine.apply_effect()`` on the fully-composited RGB image.

This module exists so the layer pipeline can reference "effect" as a
named concept (for the YAML layer stack declaration) without needing
a runtime layer instance.
"""

from __future__ import annotations

from ...core.types import AnimationType, DisplayState


# Animations that are handled as post-process effects (not RGBA layers)
_EFFECT_ANIMATIONS = frozenset({
    AnimationType.PULSE,
    AnimationType.BLINK,
    AnimationType.CRAWL,
    AnimationType.PROGRESS,
})


def has_active_effect(state: DisplayState) -> bool:
    """Return True if the widget has a procedural effect that needs post-processing."""
    return state.animation in _EFFECT_ANIMATIONS
