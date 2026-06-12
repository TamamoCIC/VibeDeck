"""Protocol classes for the VibeDeck platform abstraction layer.

Each protocol defines one capability area.  Platform backends implement
one or more of these protocols.  Callers go through the module-level
functions in :mod:`vibe_deck.platform` rather than using these types
directly.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class WindowManager(Protocol):
    """Window focus, find, toggle, and HWND cache management."""

    def focus_window_by_pid(self, pid: int) -> bool:
        """Bring the terminal window for *pid* to the foreground.

        Returns ``True`` if a window was found and focused.
        """
        ...

    def toggle_window_by_pid(self, pid: int) -> dict:
        """Focus or unfocus the agent window based on its actual
        foreground state.

        Returns a dict with ``action`` key:
        ``focused`` | ``restored`` | ``minimised`` | ``flashing`` |
        ``error`` | ``cooldown``.
        """
        ...

    def find_window_title(self, pid: int) -> str | None:
        """Walk up the process tree and return the first visible
        window title, or ``None``.
        """
        ...

    def register_hwnd(self, pid: int, hwnd: int) -> None:
        """Pre-register a known window handle for *pid*.

        Called when an agent self-reports its console HWND.
        """
        ...

    def clear_hwnd_cache(self, pid: int | None = None) -> None:
        """Clear cached HWND entries.  *pid* is ``None`` → clear all."""
        ...

    def clear_toggle_state(self, pid: int | None = None) -> None:
        """Clear saved toggle (restore) state.  *pid* is ``None`` → clear all."""
        ...

    def find_and_cache_hwnd(self, pid: int) -> int | None:
        """Find the terminal window for *pid* and cache it (early binding).

        Called by ProcessScanner at agent discovery time.
        """
        ...


@runtime_checkable
class ProcessManager(Protocol):
    """Process tree inspection tools."""

    def get_parent_pid(self, pid: int) -> int | None:
        """Return the parent PID of *pid*, or ``None`` on failure."""
        ...

    def get_process_name(self, pid: int) -> str | None:
        """Return the executable name for *pid* (e.g. ``'claude.exe'``)."""
        ...

    def get_console_hwnd(self) -> int | None:
        """Return this process's console window handle, or ``None``."""
        ...

    def is_shell_process(self, pid: int) -> bool:
        """Return ``True`` if *pid* is a transient shell (cmd.exe, bash, etc.)."""
        ...

    def ancestor_pids(self, pid: int, max_depth: int = 6) -> list[int]:
        """Return ``[pid, parent, grandparent, ...]`` up to *max_depth*."""
        ...

    def find_agent_ancestor_pid(
        self, known_agents: set[str] | None = None,
    ) -> int:
        """Walk up the process tree looking for a known agent process.

        Returns the matching PID, or ``os.getpid()`` if none found.
        """
        ...


@runtime_checkable
class SystemPaths(Protocol):
    """Platform-specific filesystem paths."""

    def font_paths(self) -> list[str]:
        """Font file paths to try in order (for PIL ``ImageFont.truetype()``)."""
        ...

    def vibe_deck_home(self) -> str:
        """The VibeDeck data directory (``~/.vibe-deck`` or equivalent)."""
        ...
