"""Window focus utilities — re-exported from :mod:`vibe_deck.platform`.

Previously this module contained ~730 lines of Windows-only ctypes logic.
That logic now lives in :class:`vibe_deck.platform._windows.WindowsBackend`.

This shim preserves backward compatibility — existing imports like
``from ..core.window_focus import toggle_window_by_pid`` continue to work.
New code should import directly from :mod:`vibe_deck.platform`.
"""

from __future__ import annotations

from vibe_deck.platform import (  # noqa: F401
    clear_hwnd_cache,
    clear_toggle_state,
    find_and_cache_hwnd,
    find_window_title,
    focus_window_by_pid,
    register_hwnd,
    toggle_window_by_pid,
)
