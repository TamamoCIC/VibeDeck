"""
Linux platform backend.

.. TODO Checklist — implement these methods:
..
..   1. Window management (X11 / Wayland):
..      - focus_window_by_pid:  Use ``wmctrl -i -a <wid>`` or
..                              ``xdotool windowactivate <wid>``.
..      - toggle_window_by_pid: Need focus + restore tracking.
..                              Save foreground via ``xdotool getactivewindow``.
..      - find_window_title:    Parse ``wmctrl -lp`` or ``xdotool getwindowname``.
..      - register_hwnd:        Store X11 window IDs for a PID.
..      - find_and_cache_hwnd:  Enumerate via ``_NET_CLIENT_LIST``
..                              (``xprop -root``) or ``wmctrl -lp``.
..      - Cache & toggle state: Same pattern as Windows backend
..                              (``_hwnd_cache``, ``_saved_foreground``).
..
..   2. Process tools:
..      - get_parent_pid:       ``/proc/{pid}/stat`` field 4  ✅ implemented
..      - get_process_name:     ``/proc/{pid}/comm``  ✅ implemented
..      - get_console_hwnd:     Returns ``None`` (no HWND concept).
..                              Could look at ``/proc/{pid}/fd/0`` → ``/dev/pts/*``.
..      - is_shell_process:     Compare process name against SHELL_NAMES  ✅ implemented
..      - ancestor_pids:        ``/proc/{pid}/status`` PPid field  ✅ implemented
..      - find_agent_ancestor_pid: psutil walk  ✅ implemented
..
..   3. System paths:
..      - font_paths:           ``/usr/share/fonts/truetype/dejavu/``,
..                              ``/usr/share/fonts/truetype/liberation/``.
..                              Could query fontconfig: ``fc-match sans``.
..      - vibe_deck_home:       ``~/.vibe-deck``  ✅ implemented
"""

from __future__ import annotations

import logging
import os as _os
from pathlib import Path

log = logging.getLogger("vibe_deck.platform._linux")

_SHELL_NAMES: set[str] = {"bash", "sh", "zsh", "dash", "fish", "pwsh"}


class LinuxBackend:
    """Linux platform backend — window management is stubbed."""

    # ── Window management (TODO) ──────────────────────────

    def focus_window_by_pid(self, pid: int) -> bool:
        """TODO: Implement via wmctrl -i -a or xdotool windowactivate."""
        log.warning("focus_window_by_pid: not implemented on Linux")
        return False

    def toggle_window_by_pid(self, pid: int) -> dict:
        """TODO: Implement focus/unfocus toggle with saved-foreground tracking."""
        return {"action": "error", "message": "not implemented on Linux"}

    def find_window_title(self, pid: int) -> str | None:
        """TODO: Implement via wmctrl -lp parsing."""
        return None

    def register_hwnd(self, pid: int, hwnd: int) -> None:
        """TODO: Store window ID (X11/Wayland) for a PID."""
        pass

    def clear_hwnd_cache(self, pid: int | None = None) -> None:
        pass

    def clear_toggle_state(self, pid: int | None = None) -> None:
        pass

    def find_and_cache_hwnd(self, pid: int) -> int | None:
        """TODO: Enumerate via _NET_CLIENT_LIST or wmctrl."""
        return None

    # ── Process tools (/proc-based, no external deps) ─────

    def get_parent_pid(self, pid: int) -> int | None:
        """Read ``/proc/{pid}/stat`` field 4 (~10 µs per call)."""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            # Format: pid (comm) state ppid ...
            # The comm field may contain spaces, so find the last ')'.
            end_paren = stat.rfind(")")
            if end_paren == -1:
                return None
            fields = stat[end_paren + 2:].split()
            return int(fields[1]) if len(fields) >= 2 else None
        except Exception:
            return None

    def get_process_name(self, pid: int) -> str | None:
        """Read ``/proc/{pid}/comm``, stripped."""
        try:
            return Path(f"/proc/{pid}/comm").read_text().strip()
        except Exception:
            return None

    def get_console_hwnd(self) -> int | None:
        """No HWND concept on Linux.  Returns ``None``."""
        return None

    def is_shell_process(self, pid: int) -> bool:
        """Compare process name against known shell names."""
        name = self.get_process_name(pid)
        if name is None:
            return False
        return name.lower() in _SHELL_NAMES

    def ancestor_pids(self, pid: int, max_depth: int = 6) -> list[int]:
        """Walk via ``/proc/{pid}/status`` PPid field.  Stdlib only."""
        chain = [pid]
        for _ in range(max_depth):
            try:
                status = Path(f"/proc/{chain[-1]}/status").read_text()
                for line in status.splitlines():
                    if line.startswith("PPid:"):
                        ppid = int(line.split()[1])
                        if ppid <= 0 or ppid in chain:
                            return chain
                        chain.append(ppid)
                        break
            except Exception:
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
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
        ]

    @staticmethod
    def vibe_deck_home() -> str:
        return str(Path.home() / ".vibe-deck")
