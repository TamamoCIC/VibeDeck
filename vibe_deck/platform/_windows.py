"""
Windows platform backend — window focus, process tools, and system paths.

All Win32 API calls (ctypes→user32/kernel32) live here.
On import this module declares ctypes structures and argtypes;
the :class:`WindowsBackend` singleton is instantiated by
:mod:`vibe_deck.platform.__init__` on Windows hosts.
"""

from __future__ import annotations

import ctypes
import logging
import os as _os
import time as _time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("vibe_deck.platform._windows")

# ── ctypes handles ────────────────────────────────────
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL,
                                   wintypes.HWND,
                                   wintypes.LPARAM)

_MAX_ANCESTOR_WALK = 6

# ── ctypes structures for SendInput (Alt-spoofing) ─────
INPUT_KEYBOARD = 1
KEYEVENTF_SCANCODE = 0x0008
KEYEVENTF_KEYUP = 0x0002
LEFT_ALT_SCANCODE = 0x0038
SW_RESTORE = 9

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_void_p),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("ki", KEYBDINPUT),
    ]


# Pre-built static INPUT structures — zero allocation per call.
_ALT_DOWN = INPUT(
    type=INPUT_KEYBOARD,
    ki=KEYBDINPUT(
        wVk=0, wScan=LEFT_ALT_SCANCODE,
        dwFlags=KEYEVENTF_SCANCODE, time=0, dwExtraInfo=0,
    ),
)
_ALT_UP = INPUT(
    type=INPUT_KEYBOARD,
    ki=KEYBDINPUT(
        wVk=0, wScan=LEFT_ALT_SCANCODE,
        dwFlags=KEYEVENTF_SCANCODE | KEYEVENTF_KEYUP,
        time=0, dwExtraInfo=0,
    ),
)

# ── argtypes / restypes ────────────────────────────────
_user32.SendInput.argtypes = [wintypes.UINT,
                               ctypes.POINTER(INPUT),
                               ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_SENDINPUT_SIZEOF = ctypes.sizeof(INPUT)

_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.GetWindowLongW.restype = ctypes.c_long

_user32.GetClassNameW.argtypes = [wintypes.HWND,
                                  ctypes.c_wchar_p,
                                  ctypes.c_int]
_user32.GetClassNameW.restype = ctypes.c_int

_user32.AttachThreadInput.argtypes = [wintypes.DWORD,
                                      wintypes.DWORD,
                                      wintypes.BOOL]
_user32.AttachThreadInput.restype = wintypes.BOOL

_user32.BringWindowToTop.argtypes = [wintypes.HWND]
_user32.BringWindowToTop.restype = wintypes.BOOL
_user32.SetFocus.argtypes = [wintypes.HWND]
_user32.SetFocus.restype = wintypes.HWND

_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                              ctypes.POINTER(wintypes.DWORD)]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
_user32.GetWindowRect.restype = wintypes.BOOL


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


FLASHW_TRAY = 0x00000002
FLASHW_TIMERNOFG = 0x0000000C

_user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
_user32.AllowSetForegroundWindow.restype = wintypes.BOOL

_user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                 ctypes.c_int, ctypes.c_int,
                                 ctypes.c_int, ctypes.c_int,
                                 wintypes.UINT]
_user32.SetWindowPos.restype = wintypes.BOOL

_user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
_user32.SwitchToThisWindow.restype = None

_user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                wintypes.DWORD, ctypes.c_void_p]
_user32.keybd_event.restype = None

_user32.SetForegroundWindow.argtypes = [wintypes.HWND]
_user32.SetForegroundWindow.restype = wintypes.BOOL

_user32.LockSetForegroundWindow.argtypes = [wintypes.UINT]
_user32.LockSetForegroundWindow.restype = wintypes.BOOL

# SetWindowPos constants
HWND_TOPMOST = -1
HWND_NOTOPMOST = -2
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_SHOWWINDOW = 0x0040

# Foreground lock constants
LSFW_LOCK = 1
LSFW_UNLOCK = 2

# Virtual key codes
VK_MENU = 0x12
VK_RETURN = 0x0D
VK_SHIFT = 0x10
VK_Y = 0x59
VK_N = 0x4E

# ── Terminal window class whitelist ────────────────────
_TERMINAL_CLASSES: set[str] = {
    "CASCADIA_HOSTING_WINDOW_CLASS",
    "ConsoleWindowClass",
    "mintty",
    "VirtualConsoleClass",
}
_extra = _os.environ.get("VIBEDECK_TERMINAL_CLASSES", "")
if _extra:
    _TERMINAL_CLASSES.update(c.strip() for c in _extra.split(";") if c.strip())

_SHELL_NAMES: set[str] = {
    "cmd.exe", "powershell.exe", "pwsh.exe",
    "bash.exe", "wsl.exe", "sh.exe",
}

# Toolhelp constants
_TH32CS_SNAPPROCESS = 0x00000002


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize",              wintypes.DWORD),
        ("cntUsage",            wintypes.DWORD),
        ("th32ProcessID",       wintypes.DWORD),
        ("th32DefaultHeapID",   ctypes.POINTER(wintypes.ULONG)),
        ("th32ModuleID",        wintypes.DWORD),
        ("cntThreads",          wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase",      wintypes.LONG),
        ("dwFlags",             wintypes.DWORD),
        ("szExeFile",           wintypes.CHAR * 260),
    ]


# ═══════════════════════════════════════════════════════════
# WindowsBackend
# ═══════════════════════════════════════════════════════════

class WindowsBackend:
    """Full Windows implementation of window management, process tools,
    and system paths."""

    def __init__(self) -> None:
        # ── HWND cache (pid → hwnd) ─────────────────────
        self._hwnd_cache: dict[int, int] = {}
        # ── Toggle state ─────────────────────────────────
        self._saved_foreground: dict[int, int] = {}   # pid → prev fg hwnd
        self._last_toggle_at: dict[int, float] = {}   # pid → monotonic timestamp
        self._TOGGLE_COOLDOWN_S = 1.0

    # ══════════════════════════════════════════════════════
    # Window management — public API
    # ══════════════════════════════════════════════════════

    def focus_window_by_pid(self, pid: int) -> bool:
        """Bring the terminal window for *pid* to the foreground."""
        if pid <= 0:
            return False

        chain = self.ancestor_pids(pid)
        log.debug("focus: pid chain for %d → %s", pid, chain)

        hwnd = self._get_cached_hwnd(pid)
        if hwnd:
            log.info("focus: using cached hwnd=%d for pid=%d", hwnd, pid)
        else:
            hwnd = self._find_window(chain)
            if hwnd:
                self._hwnd_cache[pid] = hwnd

        if hwnd is None:
            return False

        self._bring_to_foreground(hwnd)
        log.info("Focused window for pid=%d via ancestor chain %s (hwnd=%d, title=%r)",
                 pid, chain, hwnd, self._window_title(hwnd))
        return True

    def toggle_window_by_pid(self, pid: int) -> dict:
        """Focus or unfocus the agent window based on its actual foreground state."""
        if pid <= 0:
            return {"action": "error", "message": "invalid pid"}

        # ── Cooldown guard ──────────────────────────────
        now = _time.monotonic()
        last = self._last_toggle_at.get(pid, 0)
        if now - last < self._TOGGLE_COOLDOWN_S:
            return {"action": "cooldown", "message": "please wait before toggling again"}
        self._last_toggle_at[pid] = now

        chain = self.ancestor_pids(pid)
        log.info("Toggle: pid=%d full_chain=%s", pid, chain)

        # ── Build search chain (strip VibeDeck's own ancestry) ──
        _own_pid = _os.getpid()
        _cut = len(chain)
        for _i, _p in enumerate(chain):
            if _p == _own_pid:
                _cut = _i
                break
        search_chain = chain[:_cut] if _cut < len(chain) else chain
        if len(search_chain) < len(chain):
            log.info("Toggle: stripped VibeDeck ancestry — search_chain=%s", search_chain)

        # ── Try cached HWND first ─────────────────────
        agent_hwnd = self._get_cached_hwnd(pid)
        used_cache = False
        if agent_hwnd:
            used_cache = True
            log.info("Toggle: using cached hwnd=%d for pid=%d", agent_hwnd, pid)

        if agent_hwnd is None:
            agent_hwnd = self._find_window(search_chain)
            if agent_hwnd:
                self._hwnd_cache[pid] = agent_hwnd
                log.info("Toggle: cached new hwnd=%d for pid=%d", agent_hwnd, pid)

        if agent_hwnd is None:
            return {"action": "error",
                    "message": f"PID {pid} has no visible terminal windows"}

        agent_title = self._window_title(agent_hwnd)

        current_fg = _user32.GetForegroundWindow()
        is_foreground = (agent_hwnd == current_fg)

        if not is_foreground:
            # ── Agent not in front → save current, focus agent ──
            # Skip saving when current_fg is a terminal window —
            # terminal-to-terminal saves cause cross-agent contamination
            # where _saved_foreground[pid2] ends up pointing to agent-1's
            # HWND, making button-2 restore window-1 on the next toggle.
            current_fg_cls = self._get_window_class(current_fg) if current_fg else ""
            if current_fg_cls not in _TERMINAL_CLASSES:
                self._saved_foreground[pid] = current_fg
            ok = self._bring_to_foreground(agent_hwnd)
            if not ok and used_cache:
                log.warning("Toggle: focus failed with cached hwnd=%d — retrying with fresh find",
                            agent_hwnd)
                self.clear_hwnd_cache(pid)
                agent_hwnd = self._find_window(search_chain)
                if agent_hwnd:
                    self._hwnd_cache[pid] = agent_hwnd
                    agent_title = self._window_title(agent_hwnd)
                    log.info("Toggle: re-found hwnd=%d title=%r — retrying focus",
                             agent_hwnd, agent_title)
                    ok = self._bring_to_foreground(agent_hwnd)
            if not ok:
                self._saved_foreground.pop(pid, None)
                return {"action": "flashing",
                        "title": agent_title,
                        "message": f"'{agent_title}' taskbar is flashing. Click it or Alt+Tab."}
            log.info("Toggle: focused agent pid=%d (hwnd=%d, title=%r, saved_fg=%d)",
                     pid, agent_hwnd, agent_title, current_fg)
            return {"action": "focused", "pid": pid, "title": agent_title,
                    "restorable": True}

        # ── Agent already foreground → restore or minimise ──
        prev_hwnd = self._saved_foreground.pop(pid, 0)
        if prev_hwnd and _user32.IsWindow(prev_hwnd) and prev_hwnd != agent_hwnd:
            self._bring_to_foreground(prev_hwnd)
            log.info("Toggle: restored previous window for pid=%d (hwnd=%d, title=%r)",
                     pid, prev_hwnd, self._window_title(prev_hwnd))
            return {"action": "restored", "pid": pid,
                    "title": self._window_title(prev_hwnd)}

        # No saved window (or self-loop / dead) → minimise
        _user32.ShowWindow(agent_hwnd, 6)  # SW_MINIMIZE
        log.info("Toggle: minimised agent pid=%d (no saved window or self-loop)", pid)
        return {"action": "minimised", "pid": pid}

    def send_keys(self, pid: int, text: str) -> dict:
        """Type *text* into the terminal window for *pid*.

        Finds the window for *pid*, brings it to the foreground, then
        sends each character via ``keybd_event``.  Only handles ASCII
        printable characters plus ``\\n`` (Enter).  The window stays in
        the foreground after sending — the user can see the agent's response.

        Returns ``{"action": "sent", "text": text}`` on success, or an
        error dict on failure.
        """
        if pid <= 0 or not text:
            return {"action": "error", "message": "invalid pid or empty text"}

        chain = self.ancestor_pids(pid)
        hwnd = self._get_cached_hwnd(pid) or self._find_window(chain)
        if hwnd is None:
            return {"action": "error",
                    "message": f"no window found for PID {pid}"}

        # Bring window to foreground so keystrokes land in the right place.
        if not self._bring_to_foreground(hwnd):
            return {"action": "error",
                    "message": f"could not focus window for PID {pid}"}

        # Brief delay — let the window thread accept input
        _kernel32.Sleep(60)

        for char in text:
            if char == '\n':
                vk = VK_RETURN
            elif char == '\r':
                continue
            elif char == '\t':
                vk = 0x09  # VK_TAB
            elif 'A' <= char <= 'Z':
                # Uppercase letter: hold Shift
                _user32.keybd_event(VK_SHIFT, 0, 0, 0)
                _kernel32.Sleep(1)
                vk = ord(char)
            elif 'a' <= char <= 'z':
                # Lowercase letter: just the VK code (no Shift)
                vk = ord(char.upper())
            elif char in "0123456789":
                vk = ord(char)
            elif char == ' ':
                vk = 0x20  # VK_SPACE
            elif char == '.':
                vk = 0xBE
            elif char == '-':
                vk = 0xBD
            elif char == '/':
                vk = 0xBF
            elif char == ':':
                _user32.keybd_event(VK_SHIFT, 0, 0, 0)
                _kernel32.Sleep(1)
                vk = 0xBA
            elif char == '\\':
                vk = 0xDC
            elif char == '?':
                _user32.keybd_event(VK_SHIFT, 0, 0, 0)
                _kernel32.Sleep(1)
                vk = 0xBF
            else:
                log.debug("send_keys: unsupported char %r — skipping", char)
                continue

            # Key down
            _user32.keybd_event(vk, 0, 0, 0)
            _kernel32.Sleep(5)
            # Key up
            _user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)
            _kernel32.Sleep(5)

            # Release Shift if it was held
            if char in 'AZ:?':
                _user32.keybd_event(VK_SHIFT, 0, KEYEVENTF_KEYUP, 0)
                _kernel32.Sleep(1)

        log.info("send_keys: sent %r to pid=%d (hwnd=%d)", text, pid, hwnd)
        return {"action": "sent", "text": text}

    def find_window_title(self, pid: int) -> str | None:
        """Walk from *pid* up the process tree and return the first
        visible window title, or ``None``."""
        if pid <= 0:
            return None
        chain = self.ancestor_pids(pid)
        hwnd = self._get_cached_hwnd(pid) or self._find_window(chain)
        return self._window_title(hwnd) if hwnd is not None else None

    def register_hwnd(self, pid: int, hwnd: int) -> None:
        """Register a known window handle for a PID.

        Called when an agent self-reports its console HWND through the
        hook system.  On ConPTY-based terminals (Windows Terminal),
        resolves the hidden conhost window to the visible WT window.
        """
        if not (pid > 0 and hwnd > 0):
            return

        # Check if the reported HWND is a visible terminal window
        if _user32.IsWindowVisible(hwnd):
            cls = self._get_window_class(hwnd)
            if cls in _TERMINAL_CLASSES:
                self._hwnd_cache[pid] = hwnd
                log.info("register_hwnd: pid=%d → hwnd=%d (class=%r, direct)",
                         pid, hwnd, cls)
                return

        # Hidden (ConPTY conhost) — resolve to visible WT window
        conhost_pid = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(conhost_pid))
        if conhost_pid.value > 0:
            import psutil
            try:
                parent_pid = psutil.Process(conhost_pid.value).ppid()
                if parent_pid and parent_pid > 0:
                    parent_chain = self.ancestor_pids(parent_pid)
                    visible_hwnd = self._find_window(parent_chain)
                    if visible_hwnd:
                        self._hwnd_cache[pid] = visible_hwnd
                        log.info("register_hwnd: pid=%d → hwnd=%d "
                                 "(resolved from conhost pid=%d)",
                                 pid, visible_hwnd, conhost_pid.value)
                        return
            except Exception:
                pass

        # Fallback: cache the original HWND anyway — better than nothing
        self._hwnd_cache[pid] = hwnd
        log.info("register_hwnd: pid=%d → hwnd=%d (fallback, may be hidden)",
                 pid, hwnd)

    def clear_hwnd_cache(self, pid: int | None = None) -> None:
        """Clear cached HWND entries.  *pid* is ``None`` → clear all."""
        if pid is None:
            self._hwnd_cache.clear()
        else:
            self._hwnd_cache.pop(pid, None)

    def clear_toggle_state(self, pid: int | None = None) -> None:
        """Clear saved toggle state.  *pid* is ``None`` → clear all."""
        if pid is None:
            self._saved_foreground.clear()
        else:
            self._saved_foreground.pop(pid, None)

    def find_and_cache_hwnd(self, pid: int) -> int | None:
        """Find the terminal window for *pid* and cache it (early binding)."""
        if pid <= 0:
            return None

        cached = self._get_cached_hwnd(pid)
        if cached:
            return cached

        chain = self.ancestor_pids(pid)
        hwnd = self._find_window(chain)
        if hwnd:
            self._hwnd_cache[pid] = hwnd
            log.info("find_and_cache_hwnd: pid=%d → hwnd=%d (title=%r, class=%r)",
                     pid, hwnd, self._window_title(hwnd),
                     self._get_window_class(hwnd))
        return hwnd

    # ══════════════════════════════════════════════════════
    # HWND cache helpers
    # ══════════════════════════════════════════════════════

    def _get_cached_hwnd(self, pid: int) -> int | None:
        """Return cached HWND for *pid* if it still exists, else None."""
        hwnd = self._hwnd_cache.get(pid)
        if hwnd and _user32.IsWindow(hwnd):
            return hwnd
        if hwnd:
            self._hwnd_cache.pop(pid, None)
        return None

    # ══════════════════════════════════════════════════════
    # Window discovery
    # ══════════════════════════════════════════════════════

    def _get_window_class(self, hwnd: int) -> str:
        """Return the window class name of *hwnd*."""
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(hwnd, buf, 255)
        return buf.value

    def _is_tool_window(self, hwnd: int) -> bool:
        """Return True if *hwnd* has WS_EX_TOOLWINDOW or WS_EX_NOACTIVATE."""
        ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        return bool(ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE))

    def _has_meaningful_size(self, hwnd: int) -> bool:
        """Return True if *hwnd* has a non-zero client area."""
        rect = RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        width = rect.right - rect.left
        height = rect.bottom - rect.top
        return width > 10 and height > 10

    def _find_window(self, pids: list[int]) -> int | None:
        """Return the best terminal window handle belonging to any PID in
        *pids* (searched in order — deepest descendant first)."""
        pids_set = set(pids)
        found: list[tuple[int, int, str]] = []

        def _enum_proc(hwnd: int, _lparam: int) -> bool:
            process_id = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
            pid = process_id.value
            if pid not in pids_set:
                return True
            if not _user32.IsWindowVisible(hwnd):
                return True

            cls_name = self._get_window_class(hwnd)
            if cls_name not in _TERMINAL_CLASSES:
                return True

            if self._is_tool_window(hwnd):
                return True

            if not self._has_meaningful_size(hwnd):
                return True

            # Skip VibeDeck's own terminal
            title_len = _user32.GetWindowTextLengthW(hwnd)
            if title_len > 0:
                buf = ctypes.create_unicode_buffer(256)
                _user32.GetWindowTextW(hwnd, buf, 255)
                if buf.value.strip().lower() == "vibedeck":
                    return True

            idx = pids.index(pid) if pid in pids else 999
            found.append((idx, hwnd, cls_name))
            return True

        _user32.EnumWindows(_WNDENUMPROC(_enum_proc), 0)

        found.sort(key=lambda x: x[0])
        if found:
            _, hwnd, cls = found[0]
            log.info("_find_window: matched hwnd=%d class=%r (candidates=%d)",
                     hwnd, cls, len(found))
            return hwnd
        return None

    def _window_title(self, hwnd: int) -> str:
        """Read the title text of *hwnd*."""
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetWindowTextW(hwnd, buf, 255)
        return buf.value

    def _is_window_minimized(self, hwnd: int) -> bool:
        """Return True if *hwnd* is currently minimized (iconic)."""
        return bool(_user32.IsIconic(hwnd))

    # ══════════════════════════════════════════════════════
    # Foreground activation strategies
    # ══════════════════════════════════════════════════════

    def _ensure_message_queue(self) -> None:
        """Ensure the calling thread has a Windows message queue."""
        _user32.PeekMessageW(None, 0, 0, 0, 0x0001)  # PM_NOREMOVE

    def _try_alt_spoof(self, hwnd: int) -> bool:
        """Alt-spoofing via SendInput (scan codes)."""
        self._ensure_message_queue()
        sent_down = _user32.SendInput(1, ctypes.byref(_ALT_DOWN), _SENDINPUT_SIZEOF)
        _kernel32.Sleep(1)
        try:
            result = _user32.SetForegroundWindow(hwnd)
        finally:
            sent_up = _user32.SendInput(1, ctypes.byref(_ALT_UP), _SENDINPUT_SIZEOF)
        log.info("_try_alt_spoof(sc) hwnd=%d send_down=%d sfw=%d send_up=%d",
                 hwnd, sent_down, result, sent_up)
        return bool(result)

    def _try_alt_spoof_vk(self, hwnd: int) -> bool:
        """Alt-spoofing via keybd_event (virtual key codes).

        ``keybd_event`` is an older API that may bypass UIPI restrictions
        that block ``SendInput`` on very recent Windows 11 builds.
        """
        self._ensure_message_queue()
        _user32.keybd_event(VK_MENU, 0, 0, 0)
        _kernel32.Sleep(1)
        try:
            result = _user32.SetForegroundWindow(hwnd)
        finally:
            _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        log.info("_try_alt_spoof(vk) hwnd=%d sfw=%d", hwnd, result)
        return bool(result)

    def _try_topmost_trick(self, hwnd: int) -> bool:
        """Briefly set TOPMOST then remove it — can jolt the window into
        foreground on some Windows 11 builds."""
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE)
        result = _user32.SetForegroundWindow(hwnd)
        log.info("_try_topmost_trick hwnd=%d sfw=%d", hwnd, result)
        return bool(result)

    def _try_attach_thread(self, hwnd: int) -> bool:
        """Try to activate *hwnd* via AttachThreadInput + TOPMOST jolt +
        AllowSetForegroundWindow."""
        fg_hwnd = _user32.GetForegroundWindow()
        if not fg_hwnd or fg_hwnd == hwnd:
            return False

        fg_tid = _user32.GetWindowThreadProcessId(fg_hwnd, None)
        my_tid = _kernel32.GetCurrentThreadId()
        if fg_tid == my_tid:
            return bool(_user32.SetForegroundWindow(hwnd))

        attached = _user32.AttachThreadInput(my_tid, fg_tid, True)
        if not attached:
            log.info("_try_attach_thread: AttachThreadInput failed (tid=%d→%d)",
                     my_tid, fg_tid)
            return False
        try:
            _user32.AllowSetForegroundWindow(0xFFFFFFFF)
            _user32.LockSetForegroundWindow(LSFW_UNLOCK)
            _user32.ShowWindow(hwnd, SW_RESTORE)
            _user32.BringWindowToTop(hwnd)
            _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
            _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                                 SWP_NOMOVE | SWP_NOSIZE)
            result = _user32.SetForegroundWindow(hwnd)
            return bool(result)
        finally:
            _user32.AttachThreadInput(my_tid, fg_tid, False)

    def _try_switch_direct(self, hwnd: int, alt_tab: bool = False) -> bool:
        """Use ``SwitchToThisWindow`` as a standalone strategy.

        *alt_tab*=False is the safe default — it switches without
        simulating Alt+Tab, so other maximized windows are unaffected.
        Only pass ``True`` as a desperate last resort (known to
        un-maximize unrelated windows on Windows 11).
        """
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SwitchToThisWindow(hwnd, alt_tab)
        _kernel32.Sleep(50)
        result = (_user32.GetForegroundWindow() == hwnd)
        log.info("_try_switch_direct hwnd=%d alt_tab=%s ok=%d", hwnd, alt_tab, result)
        return result

    def _try_gentle_focus(self, hwnd: int) -> bool:
        """Gentle focus: SetForegroundWindow + BringWindowToTop + SetFocus.

        No AltTab simulation, no thread attachment tricks — just the
        standard Win32 focus APIs.  Safe to call without side effects.
        """
        try:
            _user32.BringWindowToTop(hwnd)
            _user32.SetForegroundWindow(hwnd)
            _user32.SetFocus(hwnd)
            _kernel32.Sleep(30)
            return _user32.GetForegroundWindow() == hwnd
        except Exception:
            return False

    def _bring_to_foreground(self, hwnd: int) -> bool:
        """Bring *hwnd* to the foreground with a multi-strategy cascade.

        Strategies are ordered from gentlest (no side effects) to most
        aggressive (may affect other windows).  Each strategy is only
        tried if the previous one failed.
        """
        title = self._window_title(hwnd)

        if self._is_window_minimized(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)

        # Strategy 1: Gentle focus — no side effects
        if self._try_gentle_focus(hwnd):
            log.info("_bring_to_foreground [gentle] hwnd=%d title=%r ✓", hwnd, title)
            return True
        log.info("_bring_to_foreground [gentle] hwnd=%d ✗", hwnd)

        # Strategy 2: SwitchToThisWindow without AltTab — safe
        if self._try_switch_direct(hwnd, alt_tab=False):
            log.info("_bring_to_foreground [switch-safe] hwnd=%d title=%r ✓", hwnd, title)
            return True
        log.info("_bring_to_foreground [switch-safe] hwnd=%d ✗", hwnd)

        # Strategy 3: Alt-spoof via keybd_event
        if self._try_alt_spoof_vk(hwnd):
            log.info("_bring_to_foreground [alt-spoof-vk] hwnd=%d title=%r ✓", hwnd, title)
            return True
        log.info("_bring_to_foreground [alt-spoof-vk] hwnd=%d ✗", hwnd)

        # Strategy 4: AttachThreadInput + TOPMOST
        if self._try_attach_thread(hwnd):
            log.info("_bring_to_foreground [attach-thread] hwnd=%d ✓", hwnd)
            return True
        log.info("_bring_to_foreground [attach-thread] hwnd=%d ✗", hwnd)

        # Strategy 5: Alt-spoof via SendInput
        if self._try_alt_spoof(hwnd):
            log.info("_bring_to_foreground [alt-spoof-sc] hwnd=%d title=%r ✓", hwnd, title)
            return True
        log.info("_bring_to_foreground [alt-spoof-sc] hwnd=%d ✗", hwnd)

        # Strategy 6: TOPMOST jolt standalone
        if self._try_topmost_trick(hwnd):
            log.info("_bring_to_foreground [topmost-jolt] hwnd=%d ✓", hwnd)
            return True
        log.info("_bring_to_foreground [topmost-jolt] hwnd=%d ✗", hwnd)

        # Strategy 7: Retry after foreground-lock cooldown
        _kernel32.Sleep(400)
        if self._try_switch_direct(hwnd, alt_tab=False):
            log.info("_bring_to_foreground [switch-safe-2] hwnd=%d ✓", hwnd)
            return True
        if self._try_alt_spoof_vk(hwnd):
            log.info("_bring_to_foreground [alt-spoof-vk-2] hwnd=%d ✓", hwnd)
            return True
        if _user32.SetForegroundWindow(hwnd):
            log.info("_bring_to_foreground [bare-sfw] hwnd=%d ✓", hwnd)
            return True
        log.info("_bring_to_foreground [post-cooldown] hwnd=%d ✗", hwnd)

        # Strategy 8: SwitchToThisWindow WITH AltTab — DESPERATE LAST RESORT.
        # This CAN un-maximize other windows on Windows 11, but it's the
        # most reliable way to force a window to foreground when all else
        # fails.  Only reached after every gentle strategy has been tried.
        log.warning("_bring_to_foreground: trying AltTab switch as last resort "
                    "(may un-maximize other windows)")
        try:
            _user32.ShowWindow(hwnd, SW_RESTORE)
            _user32.SwitchToThisWindow(hwnd, True)
            if _user32.GetForegroundWindow() == hwnd:
                log.info("_bring_to_foreground [switch-altab] hwnd=%d ✓", hwnd)
                return True
        except Exception:
            pass

        # Strategy 9: Flash taskbar — absolute final fallback
        fw = FLASHWINFO()
        fw.cbSize = ctypes.sizeof(FLASHWINFO)
        fw.hwnd = hwnd
        fw.dwFlags = FLASHW_TRAY | FLASHW_TIMERNOFG
        fw.uCount = 0
        fw.dwTimeout = 0
        _user32.FlashWindowEx(ctypes.byref(fw))

        log.warning("_bring_to_foreground: all strategies failed — "
                    "taskbar flashing for %r", title)
        return False

    # ══════════════════════════════════════════════════════
    # Process tools — public API
    # ══════════════════════════════════════════════════════

    def get_parent_pid(self, pid: int) -> int | None:
        """Get parent PID on Windows via CreateToolhelp32Snapshot (~1-5 ms)."""
        h_snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
        if h_snap == INVALID_HANDLE_VALUE:
            return None

        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)

            if not _kernel32.Process32First(h_snap, ctypes.byref(entry)):
                return None

            while True:
                if entry.th32ProcessID == pid:
                    ppid = entry.th32ParentProcessID
                    return int(ppid) if ppid else None
                if not _kernel32.Process32Next(h_snap, ctypes.byref(entry)):
                    break
        finally:
            _kernel32.CloseHandle(h_snap)

        return None

    def get_process_name(self, pid: int) -> str | None:
        """Get the executable name (e.g. ``'claude.exe'``) for a PID."""
        h_snap = _kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
        INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value
        if h_snap == INVALID_HANDLE_VALUE:
            return None

        try:
            entry = _PROCESSENTRY32()
            entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)

            if _kernel32.Process32First(h_snap, ctypes.byref(entry)):
                while True:
                    if entry.th32ProcessID == pid:
                        return entry.szExeFile.decode("utf-8", errors="replace")
                    if not _kernel32.Process32Next(h_snap, ctypes.byref(entry)):
                        break
        finally:
            _kernel32.CloseHandle(h_snap)

        return None

    def get_console_hwnd(self) -> int | None:
        """Return this process's console window handle, or ``None``."""
        hwnd = _kernel32.GetConsoleWindow()
        return hwnd if hwnd else None

    def is_shell_process(self, pid: int) -> bool:
        """Return ``True`` if *pid* is a transient shell process."""
        name = self.get_process_name(pid)
        if name is None:
            return False
        return name.lower() in _SHELL_NAMES

    def ancestor_pids(self, pid: int, max_depth: int = 6) -> list[int]:
        """Return ``[pid, parent, grandparent, ...]`` up to *max_depth*."""
        import psutil
        chain = [pid]
        for _ in range(max_depth):
            try:
                ppid = psutil.Process(chain[-1]).ppid()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                break
            if ppid is None or ppid <= 0 or ppid in chain:
                break
            chain.append(ppid)
        return chain

    def find_agent_ancestor_pid(
        self, known_agents: set[str] | None = None,
    ) -> int:
        """Walk up the process tree looking for a known agent process."""
        if known_agents is None:
            known_agents = {"claude", "claude.exe", "opencode", "openclaw"}

        import psutil
        try:
            proc = psutil.Process(_os.getpid())
            for _ in range(10):
                name = proc.name() or ""
                if any(agent in name.lower() for agent in known_agents):
                    return proc.pid
                proc = proc.parent()
                if proc is None:
                    break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

        return _os.getpid()

    # ══════════════════════════════════════════════════════
    # System paths
    # ══════════════════════════════════════════════════════

    @staticmethod
    def font_paths() -> list[str]:
        return [
            "C:\\Windows\\Fonts\\seguiemj.ttf",   # emoji (🐙⚫ etc.)
            "C:\\Windows\\Fonts\\consola.ttf",
            "C:\\Windows\\Fonts\\segoeui.ttf",
        ]

    @staticmethod
    def vibe_deck_home() -> str:
        return str(Path.home() / ".vibe-deck")
