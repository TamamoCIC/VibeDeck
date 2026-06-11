"""
Animation Clip Loader — scans asset directories and builds AnimationClips.

Directory layout::

    vibe_deck/assets/animations/
      <clip_name>/
        metadata.yaml     # {name, loop, frames: [{file, hold_ms}]}
        frame_000.png
        frame_001.png
        ...

Metadata format (metadata.yaml)::

    name: mascot_idle
    loop: true
    frames:
      - file: frame_000.png
        hold_ms: 150
      - file: frame_001.png
        hold_ms: 150
      - file: frame_002.png
        hold_ms: 200
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PIL import Image

from .animation_types import AnimationCategory, AnimationClip, AnimationFrame

log = logging.getLogger("vibe_deck.render.animation_loader")

try:
    import yaml  # type: ignore
    HAS_YAML = True
except ImportError:
    HAS_YAML = False


def load_clips(
    assets_dir: str | Path,
    key_sizes: Optional[list[tuple[int, int]]] = None,
) -> dict[str, AnimationClip]:
    """Scan ``assets_dir/animations/`` and build AnimationClips.

    Each subdirectory containing a ``metadata.yaml`` becomes one clip.
    Frames are loaded as RGB PIL Images.  If *key_sizes* is given the
    frames are pre-resized to the first listed size using NEAREST
    (pixel-art safe).  Larger sizes resize at render time.

    Args:
        assets_dir: Path to the ``assets`` directory (contains ``animations/``).
        key_sizes: Optional list of (w, h) tuples; frames are resized to
                   ``key_sizes[0]`` at load time.

    Returns:
        Dict mapping clip name → AnimationClip.  Returns an empty dict
        if the animations directory does not exist or YAML is unavailable.
    """
    if not HAS_YAML:
        log.warning("PyYAML not installed — cannot load animation clips")
        return {}

    animations_path = Path(assets_dir) / "animations"
    if not animations_path.exists():
        log.debug("Animation assets dir not found: %s", animations_path)
        return {}

    target_size = key_sizes[0] if key_sizes else None
    clips: dict[str, AnimationClip] = {}

    for clip_dir in sorted(animations_path.iterdir()):
        if not clip_dir.is_dir():
            continue

        meta_path = clip_dir / "metadata.yaml"
        if not meta_path.exists():
            log.debug("Skipping %s — no metadata.yaml", clip_dir.name)
            continue

        try:
            meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
        except Exception:
            log.exception("Failed to parse %s", meta_path)
            continue

        if not isinstance(meta, dict):
            log.warning("Invalid metadata in %s — expected dict", meta_path)
            continue

        name = meta.get("name", clip_dir.name)
        loop = meta.get("loop", True)
        frame_defs = meta.get("frames", [])

        frames: list[AnimationFrame] = []
        for fdef in frame_defs:
            file_name = fdef.get("file", "")
            hold_ms = int(fdef.get("hold_ms", 100))
            frame_path = clip_dir / file_name

            if not frame_path.exists():
                log.warning("Missing frame: %s", frame_path)
                continue

            try:
                img = Image.open(frame_path).convert("RGB")
                if target_size and img.size != target_size:
                    img = img.resize(target_size, Image.NEAREST)
                frames.append(AnimationFrame(image=img, hold_ms=hold_ms))
            except Exception:
                log.exception("Failed to load frame %s", frame_path)
                continue

        if not frames:
            log.warning("Clip '%s' has no valid frames — skipping", name)
            continue

        clip = AnimationClip(
            name=name,
            frames=frames,
            loop=loop,
            category=AnimationCategory.SPRITE,
        )
        clips[name] = clip
        log.info("Loaded clip '%s': %d frame(s), loop=%s, total=%dms",
                 name, len(frames), loop, clip.total_duration_ms)

    return clips
