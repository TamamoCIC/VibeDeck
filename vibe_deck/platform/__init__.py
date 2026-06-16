"""Platform abstraction layer — cross-platform window management,
process tools, and system paths.

Backend selection happens at import time based on :data:`sys.platform`:

* ``win32`` → :class:`~._windows.WindowsBackend`
* ``darwin`` → :class:`~._darwin.DarwinBackend`
* everything else → :class:`~._linux.LinuxBackend`

Usage::

    from vibe_deck.platform import focus_window_by_pid, font_paths
    focus_window_by_pid(1234)
"""

from __future__ import annotations

import logging
import sys
from typing import TYPE_CHECKING

log = logging.getLogger("vibe_deck.platform")

# ── Backend selection at import time ──────────────────────
if sys.platform == "win32":
    from ._windows import WindowsBackend as _BackendCls
elif sys.platform == "darwin":
    from ._darwin import DarwinBackend as _BackendCls
else:
    from ._linux import LinuxBackend as _BackendCls

_backend = _BackendCls()

log.debug("Platform backend: %s (sys.platform=%r)", type(_backend).__name__, sys.platform)


# ── Window management ─────────────────────────────────────

def focus_window_by_pid(pid: int) -> bool:
    """Bring the terminal window for *pid* to the foreground."""
    return _backend.focus_window_by_pid(pid)


def toggle_window_by_pid(pid: int) -> dict:
    """Focus or unfocus the agent window based on its actual foreground state.

    Returns a dict with ``action`` key:
    ``focused`` | ``restored`` | ``minimised`` | ``flashing`` | ``error`` | ``cooldown``.
    """
    return _backend.toggle_window_by_pid(pid)


def find_window_title(pid: int) -> str | None:
    """Walk up the process tree and return the first visible window title."""
    return _backend.find_window_title(pid)


def register_hwnd(pid: int, hwnd: int) -> None:
    """Pre-register a known window handle for *pid* (from self-reporting)."""
    _backend.register_hwnd(pid, hwnd)


def clear_hwnd_cache(pid: int | None = None) -> None:
    """Clear cached HWND entries.  *pid* is ``None`` → clear all."""
    _backend.clear_hwnd_cache(pid)


def clear_toggle_state(pid: int | None = None) -> None:
    """Clear saved toggle (restore) state.  *pid* is ``None`` → clear all."""
    _backend.clear_toggle_state(pid)


def find_and_cache_hwnd(pid: int) -> int | None:
    """Find the terminal window for *pid* and cache it (early binding)."""
    return _backend.find_and_cache_hwnd(pid)


def send_keys(pid: int, text: str) -> dict:
    """Type *text* into the terminal window for *pid*.

    Returns ``{"action": "sent", "text": text}`` on success, or an
    error dict on failure.
    """
    return _backend.send_keys(pid, text)


# ── Process tools ─────────────────────────────────────────

def get_parent_pid(pid: int) -> int | None:
    """Return the parent PID of *pid*, or ``None`` on failure."""
    return _backend.get_parent_pid(pid)


def get_process_name(pid: int) -> str | None:
    """Return the executable name for *pid* (e.g. ``'claude.exe'``)."""
    return _backend.get_process_name(pid)


def get_console_hwnd() -> int | None:
    """Return this process's console window handle, or ``None``."""
    return _backend.get_console_hwnd()


def is_shell_process(pid: int) -> bool:
    """Return ``True`` if *pid* is a transient shell (cmd.exe, bash, etc.)."""
    return _backend.is_shell_process(pid)


def ancestor_pids(pid: int, max_depth: int = 6) -> list[int]:
    """Return ``[pid, parent, grandparent, ...]`` up to *max_depth*."""
    return _backend.ancestor_pids(pid, max_depth=max_depth)


def find_agent_ancestor_pid(
    known_agents: set[str] | None = None,
) -> int:
    """Walk up the process tree looking for a known agent process."""
    return _backend.find_agent_ancestor_pid(known_agents)


# ── System paths ──────────────────────────────────────────

def font_paths() -> list[str]:
    """Font file paths to try in order (for ``PIL.ImageFont.truetype()``)."""
    return _backend.font_paths()


def vibe_deck_home() -> str:
    """The VibeDeck data directory (``~/.vibe-deck`` or equivalent)."""
    return _backend.vibe_deck_home()


# ── Re-export protocol types (for type-checking) ─────────
if TYPE_CHECKING:
    from ._protocols import (
        ProcessManager,
        SystemPaths,
        WindowManager,
    )

__all__ = [
    # Window management
    "focus_window_by_pid",
    "toggle_window_by_pid",
    "find_window_title",
    "register_hwnd",
    "clear_hwnd_cache",
    "clear_toggle_state",
    "find_and_cache_hwnd",
    "send_keys",
    # Process tools
    "get_parent_pid",
    "get_process_name",
    "get_console_hwnd",
    "is_shell_process",
    "ancestor_pids",
    "find_agent_ancestor_pid",
    # System paths
    "font_paths",
    "vibe_deck_home",
]
