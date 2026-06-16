"""
macOS platform backend.

.. TODO Checklist — implement these methods:
..
..   1. Window management:
..      - focus_window_by_pid:  Use Accessibility API
..                              (``CGWindowListCopyWindowInfo`` +
..                              ``AXUIElementPerformAction``) or AppleScript:
..                              ``osascript -e 'tell app "Terminal" to activate'``.
..      - toggle_window_by_pid: Same with saved-foreground tracking.
..      - find_window_title:    ``CGWindowListCopyWindowInfo`` filtered by
..                              ``kCGWindowOwnerPID``.
..      - register_hwnd:        Store ``CGWindowID`` for a PID.
..      - find_and_cache_hwnd:  ``CGWindowListCopyWindowInfo`` +
..                              terminal bundle check.
..      - Cache & toggle state: Same pattern as Windows backend.
..
..   2. Process tools:
..      - get_parent_pid:       ``proc_pidinfo`` from libproc, or
..                              ``sysctl KERN_PROC_PID``.
..      - get_process_name:     ``proc_name()`` from libproc.
..      - get_console_hwnd:     Returns ``None`` (no HWND concept).
..      - is_shell_process:     Compare process name against SHELL_NAMES.
..      - ancestor_pids:        psutil chain (``.ppid()``).
..      - find_agent_ancestor_pid: psutil walk.
..
..   3. System paths:
..      - font_paths:           ``/System/Library/Fonts/Helvetica.ttc``,
..                              ``/Library/Fonts/Arial.ttf``.
..      - vibe_deck_home:       ``~/.vibe-deck``  ✅ implemented
"""

from __future__ import annotations

import logging
import os as _os
from pathlib import Path

log = logging.getLogger("vibe_deck.platform._darwin")

_SHELL_NAMES: set[str] = {"bash", "sh", "zsh", "dash", "fish"}


class DarwinBackend:
    """macOS platform backend — window management is stubbed."""

    # ── Window management (TODO) ──────────────────────────

    def focus_window_by_pid(self, pid: int) -> bool:
        """TODO: Use Accessibility API or osascript."""
        log.warning("focus_window_by_pid: not implemented on macOS")
        return False

    def toggle_window_by_pid(self, pid: int) -> dict:
        """TODO: Implement focus/unfocus toggle with saved-foreground tracking."""
        return {"action": "error", "message": "not implemented on macOS"}

    def find_window_title(self, pid: int) -> str | None:
        """TODO: CGWindowListCopyWindowInfo."""
        return None

    def register_hwnd(self, pid: int, hwnd: int) -> None:
        """TODO: Store CGWindowID for a PID."""
        pass

    def clear_hwnd_cache(self, pid: int | None = None) -> None:
        pass

    def clear_toggle_state(self, pid: int | None = None) -> None:
        pass

    def find_and_cache_hwnd(self, pid: int) -> int | None:
        """TODO: CGWindowListCopyWindowInfo + terminal bundle check."""
        return None

    # ── Keystroke injection (TODO) ─────────────────────────

    def send_keys(self, pid: int, text: str) -> dict:
        """TODO: Implement via osascript or CGEventPost."""
        return {"action": "error", "message": "send_keys not implemented on macOS"}

    # ── Process tools (/proc fallback, TODO: proc_pidinfo) ─

    def get_parent_pid(self, pid: int) -> int | None:
        """Best-effort: try ``/proc/{pid}/stat`` (optional on macOS).
        TODO: Switch to ``proc_pidinfo`` from libproc for robustness.
        """
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            end_paren = stat.rfind(")")
            if end_paren != -1:
                fields = stat[end_paren + 2:].split()
                if len(fields) >= 2:
                    return int(fields[1])
        except Exception:
            pass
        return None

    def get_process_name(self, pid: int) -> str | None:
        """Best-effort: try ``/proc/{pid}/comm``.
        TODO: Switch to ``proc_name()`` from libproc.
        """
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip()
        except Exception:
            return None

    def get_console_hwnd(self) -> int | None:
        """No HWND concept on macOS.  Returns ``None``."""
        return None

    def is_shell_process(self, pid: int) -> bool:
        """Compare process name against known shell names."""
        name = self.get_process_name(pid)
        if name is None:
            return False
        return name.lower() in _SHELL_NAMES

    def ancestor_pids(self, pid: int, max_depth: int = 6) -> list[int]:
        """Walk process tree via psutil ``.ppid()``.
        Prefer psutil on macOS because ``/proc`` may not be mounted.
        """
        import psutil
        chain = [pid]
        for _ in range(max_depth):
            try:
                ppid = psutil.Process(chain[-1]).ppid()
                if ppid is None or ppid <= 0 or ppid in chain:
                    break
                chain.append(ppid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
        return chain

    def find_agent_ancestor_pid(
        self, known_agents: set[str] | None = None,
    ) -> int:
        """Walk ancestor chain looking for known agent processes."""
        if known_agents is None:
            known_agents = {"claude", "opencode", "openclaw"}
        chain = self.ancestor_pids(_os.getpid())
        for pid in chain:
            name = (self.get_process_name(pid) or "").lower()
            if any(a.lower() in name for a in known_agents):
                return pid
        return _os.getpid()

    # ── System paths ──────────────────────────────────────

    @staticmethod
    def font_paths() -> list[str]:
        return [
            "/System/Library/Fonts/Helvetica.ttc",
            "/Library/Fonts/Arial.ttf",
        ]

    @staticmethod
    def vibe_deck_home() -> str:
        return str(Path.home() / ".vibe-deck")
