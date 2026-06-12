"""
Window focus utilities — find and focus an agent's window by PID.

Uses Windows API via ctypes to enumerate top-level windows, match by
process ID, and bring the target window to the foreground.

Window discovery uses a **terminal window class whitelist** to avoid
matching helper/tool windows.  Known terminal classes:
  - CASCADIA_HOSTING_WINDOW_CLASS  (Windows Terminal)
  - ConsoleWindowClass             (classic conhost)
  - mintty                         (Git Bash / MSYS2)
  - VirtualConsoleClass            (ConEmu / Cmder)

An **HWND cache** (pid → hwnd) provides instant lookups after the first
discovery.  Agents can self-report their console HWND via the hook
system for even more reliable binding.

Foreground activation cascades through multiple strategies:
  1. Alt-spoofing (SendInput + SetForegroundWindow)
  2. AttachThreadInput + BringWindowToTop + SetForegroundWindow
  3. Retry after 400 ms foreground-lock cooldown
  4. Flash taskbar (last resort)

On non-Windows platforms this is a graceful no-op.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

log = logging.getLogger("vibe_deck.core.window_focus")

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL,
                                       wintypes.HWND,
                                       wintypes.LPARAM)

_MAX_ANCESTOR_WALK = 6  # max levels to walk up the process tree

if _IS_WINDOWS:
    # ── ctypes structures for SendInput (Alt-spoofing) ──────────
    INPUT_KEYBOARD = 1
    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_KEYUP = 0x0002
    LEFT_ALT_SCANCODE = 0x0038  # documented Left Alt scan code
    SW_RESTORE = 9

    # GetWindowLong extended style constants
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

    _user32.SendInput.argtypes = [wintypes.UINT,
                                   ctypes.POINTER(INPUT),
                                   ctypes.c_int]
    _user32.SendInput.restype = wintypes.UINT
    _SENDINPUT_SIZEOF = ctypes.sizeof(INPUT)

    # GetWindowLongW — read extended window styles
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.GetWindowLongW.restype = ctypes.c_long

    # GetClassName
    _user32.GetClassNameW.argtypes = [wintypes.HWND,
                                      ctypes.c_wchar_p,
                                      ctypes.c_int]
    _user32.GetClassNameW.restype = ctypes.c_int

    # AttachThreadInput
    _user32.AttachThreadInput.argtypes = [wintypes.DWORD,
                                          wintypes.DWORD,
                                          wintypes.BOOL]
    _user32.AttachThreadInput.restype = wintypes.BOOL

    # BringWindowToTop
    _user32.BringWindowToTop.argtypes = [wintypes.HWND]
    _user32.BringWindowToTop.restype = wintypes.BOOL

    # GetWindowThreadProcessId — return thread id and write process id
    _user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                  ctypes.POINTER(wintypes.DWORD)]
    _user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    # GetWindowRect — check window size
    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
    _user32.GetWindowRect.restype = wintypes.BOOL

    # ── FLASHWINFO for taskbar flashing ─────────────────
    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]
    FLASHW_TRAY = 0x00000002
    FLASHW_TIMERNOFG = 0x0000000C  # flash until foreground

    # ── Missing argtypes for foreground strategies ─────
    _user32.AllowSetForegroundWindow.argtypes = [wintypes.DWORD]
    _user32.AllowSetForegroundWindow.restype = wintypes.BOOL

    _user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND,
                                     ctypes.c_int, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int,
                                     wintypes.UINT]
    _user32.SetWindowPos.restype = wintypes.BOOL

    _user32.SwitchToThisWindow.argtypes = [wintypes.HWND, wintypes.BOOL]
    _user32.SwitchToThisWindow.restype = None  # void

    _user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE,
                                    wintypes.DWORD, ctypes.c_void_p]
    _user32.keybd_event.restype = None  # void

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

    # Virtual key code
    VK_MENU = 0x12  # Alt key


# ── Terminal window class whitelist ──────────────────
# Only windows with these class names are considered real terminal
# windows.  This prevents matching helper/tool windows like
# "✳ alt-spoof-focus" that happen to belong to an ancestor PID.
_TERMINAL_CLASSES: set[str] = {
    "CASCADIA_HOSTING_WINDOW_CLASS",  # Windows Terminal
    "ConsoleWindowClass",             # classic conhost
    "mintty",                         # Git Bash / MSYS2
    "VirtualConsoleClass",            # ConEmu / Cmder
}

# Allow users to extend via environment variable (semicolon-separated)
import os as _os
_extra = _os.environ.get("VIBEDECK_TERMINAL_CLASSES", "")
if _extra:
    _TERMINAL_CLASSES.update(c.strip() for c in _extra.split(";") if c.strip())


# ── HWND cache (pid → hwnd) ─────────────────────────
# Populated by:
#   1. register_hwnd()  — called when agent self-reports via hook data
#   2. _find_window()   — called on first discovery
# Entries are validated with IsWindow() before use.
_hwnd_cache: dict[int, int] = {}


def register_hwnd(pid: int, hwnd: int) -> None:
    """Register a known window handle for a PID.

    Called when an agent self-reports its console HWND through the
    hook system (e.g. ``_console_hwnd`` field in hook data).

    On ConPTY-based terminals (Windows Terminal), ``GetConsoleWindow()``
    returns the hidden conhost window.  We resolve it to the visible
    Windows Terminal window by walking the process tree from conhost's
    PID to its parent (WindowsTerminal.exe).
    """
    if not (pid > 0 and hwnd > 0 and _IS_WINDOWS):
        return

    # Check if the reported HWND is a visible terminal window
    if _user32.IsWindowVisible(hwnd):
        cls = _get_window_class(hwnd)
        if cls in _TERMINAL_CLASSES:
            _hwnd_cache[pid] = hwnd
            log.info("register_hwnd: pid=%d → hwnd=%d (class=%r, direct)", pid, hwnd, cls)
            return

    # The HWND is hidden (ConPTY conhost).  Find the visible WT window
    # by getting the conhost PID and looking for its parent's window.
    conhost_pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(conhost_pid))
    if conhost_pid.value > 0:
        import psutil
        try:
            parent_pid = psutil.Process(conhost_pid.value).ppid()
            if parent_pid and parent_pid > 0:
                # Search for the parent's window (likely Windows Terminal)
                parent_chain = _ancestor_pids(parent_pid)
                visible_hwnd = _find_window(parent_chain)
                if visible_hwnd:
                    _hwnd_cache[pid] = visible_hwnd
                    log.info("register_hwnd: pid=%d → hwnd=%d (resolved from conhost pid=%d)",
                             pid, visible_hwnd, conhost_pid.value)
                    return
        except Exception:
            pass

    # Fallback: cache the original HWND anyway — better than nothing
    _hwnd_cache[pid] = hwnd
    log.info("register_hwnd: pid=%d → hwnd=%d (fallback, may be hidden)", pid, hwnd)


def clear_hwnd_cache(pid: int | None = None) -> None:
    """Clear cached HWND entries.  If *pid* is None, clear all."""
    if pid is None:
        _hwnd_cache.clear()
    else:
        _hwnd_cache.pop(pid, None)


def _get_cached_hwnd(pid: int) -> int | None:
    """Return cached HWND for *pid* if it still exists, else None."""
    hwnd = _hwnd_cache.get(pid)
    if hwnd and _IS_WINDOWS and _user32.IsWindow(hwnd):
        return hwnd
    if hwnd:
        _hwnd_cache.pop(pid, None)
    return None


def _ancestor_pids(pid: int) -> list[int]:
    """Return [*pid*, parent_pid, grandparent_pid, ...] up to
    ``_MAX_ANCESTOR_WALK`` levels.  Stops early if a process cannot
    be accessed or has no parent."""
    import psutil
    chain = [pid]
    for _ in range(_MAX_ANCESTOR_WALK):
        try:
            ppid = psutil.Process(chain[-1]).ppid()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            break
        if ppid is None or ppid <= 0 or ppid in chain:
            break  # root or loop guard
        chain.append(ppid)
    return chain


def _get_window_class(hwnd: int) -> str:
    """Return the window class name of *hwnd*."""
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetClassNameW(hwnd, buf, 255)
    return buf.value


def _is_tool_window(hwnd: int) -> bool:
    """Return True if *hwnd* has WS_EX_TOOLWINDOW or WS_EX_NOACTIVATE."""
    ex_style = _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    return bool(ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE))


def _has_meaningful_size(hwnd: int) -> bool:
    """Return True if *hwnd* has a non-zero client area."""
    rect = RECT()
    if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return False
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    return width > 10 and height > 10


def _find_window(pids: list[int]) -> int | None:
    """Return the best terminal window handle belonging to any PID in
    *pids* (searched in order — deepest descendant first).

    Only windows matching the terminal class whitelist are considered.
    Tool windows, zero-size windows, and non-activatable windows are
    excluded.
    """
    pids_set = set(pids)
    found: list[tuple[int, int, str]] = []  # (pid_index, hwnd, class_name)

    def _enum_proc(hwnd: int, _lparam: int) -> bool:
        process_id = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        pid = process_id.value
        if pid not in pids_set:
            return True
        if not _user32.IsWindowVisible(hwnd):
            return True

        # ── Window class filter ──────────────────────
        cls_name = _get_window_class(hwnd)
        if cls_name not in _TERMINAL_CLASSES:
            return True  # not a terminal window — skip

        # ── Extended style filter ────────────────────
        if _is_tool_window(hwnd):
            return True  # tool/helper window — skip

        # ── Size filter ──────────────────────────────
        if not _has_meaningful_size(hwnd):
            return True  # zero-size placeholder — skip

        # ── Title check (skip VibeDeck's own terminal) ─
        title_len = _user32.GetWindowTextLengthW(hwnd)
        if title_len > 0:
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetWindowTextW(hwnd, buf, 255)
            title = buf.value
            if title.strip().lower() == "vibedeck":
                return True  # VibeDeck's own terminal

        idx = pids.index(pid) if pid in pids else 999
        found.append((idx, hwnd, cls_name))
        return True

    _user32.EnumWindows(_WNDENUMPROC(_enum_proc), 0)

    # Prefer the deepest descendant (lowest index in chain)
    found.sort(key=lambda x: x[0])
    if found:
        _, hwnd, cls = found[0]
        log.info("_find_window: matched hwnd=%d class=%r (candidates=%d)",
                 hwnd, cls, len(found))
        return hwnd
    return None


def _is_window_minimized(hwnd: int) -> bool:
    """Return True if *hwnd* is currently minimized (iconic)."""
    return bool(_user32.IsIconic(hwnd))


def _ensure_message_queue() -> None:
    """Ensure the calling thread has a Windows message queue.

    SendInput and other window-management APIs require one.
    PeekMessageW is the cheapest way to create it.
    """
    _user32.PeekMessageW(None, 0, 0, 0, 0x0001)  # PM_NOREMOVE


def _try_alt_spoof(hwnd: int) -> bool:
    """Alt-spoofing via SendInput (scan codes)."""
    _ensure_message_queue()
    sent_down = _user32.SendInput(1, ctypes.byref(_ALT_DOWN), _SENDINPUT_SIZEOF)
    _kernel32.Sleep(1)
    try:
        result = _user32.SetForegroundWindow(hwnd)
    finally:
        sent_up = _user32.SendInput(1, ctypes.byref(_ALT_UP), _SENDINPUT_SIZEOF)
    log.info("_try_alt_spoof(sc) hwnd=%d send_down=%d sfw=%d send_up=%d",
             hwnd, sent_down, result, sent_up)
    return bool(result)


def _try_alt_spoof_vk(hwnd: int) -> bool:
    """Alt-spoofing via keybd_event (virtual key codes).

    ``keybd_event`` is an older API that may bypass UIPI restrictions
    that block ``SendInput`` on very recent Windows 11 builds.
    """
    _ensure_message_queue()
    _user32.keybd_event(VK_MENU, 0, 0, 0)               # Alt down
    _kernel32.Sleep(1)
    try:
        result = _user32.SetForegroundWindow(hwnd)
    finally:
        _user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)  # Alt up
    log.info("_try_alt_spoof(vk) hwnd=%d sfw=%d", hwnd, result)
    return bool(result)


def _try_topmost_trick(hwnd: int) -> bool:
    """Briefly set TOPMOST then remove it — can jolt the window into
    foreground on some Windows 11 builds."""
    _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
    _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                         SWP_NOMOVE | SWP_NOSIZE)
    # Follow up with SetForegroundWindow — topmost jolt may have
    # weakened the foreground lock.
    result = _user32.SetForegroundWindow(hwnd)
    log.info("_try_topmost_trick hwnd=%d sfw=%d", hwnd, result)
    return bool(result)


def _try_attach_thread(hwnd: int) -> bool:
    """Try to activate *hwnd* via AttachThreadInput.

    Attaches the calling thread to the foreground window's thread,
    which grants temporary foreground-activation rights.  Also uses
    ``AllowSetForegroundWindow(ASFW_ANY)`` and the TOPMOST trick
    to maximise the chance of success on Windows 11.
    """
    fg_hwnd = _user32.GetForegroundWindow()
    if not fg_hwnd or fg_hwnd == hwnd:
        return False  # no foreground window or already focused

    fg_tid = _user32.GetWindowThreadProcessId(fg_hwnd, None)
    my_tid = _kernel32.GetCurrentThreadId()
    if fg_tid == my_tid:
        # Same thread — just call SetForegroundWindow directly
        return bool(_user32.SetForegroundWindow(hwnd))

    attached = _user32.AttachThreadInput(my_tid, fg_tid, True)
    if not attached:
        log.info("_try_attach_thread: AttachThreadInput failed (tid=%d→%d)",
                 my_tid, fg_tid)
        return False
    try:
        # ASFW_ANY = 0xFFFFFFFF: allow any process to set foreground
        _user32.AllowSetForegroundWindow(0xFFFFFFFF)
        # Unlock foreground lock (no-op if not locked, but harmless)
        _user32.LockSetForegroundWindow(LSFW_UNLOCK)
        # Restore and bring to top
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.BringWindowToTop(hwnd)
        # TOPMOST jolt while attached to foreground thread
        _user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        _user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0,
                             SWP_NOMOVE | SWP_NOSIZE)
        result = _user32.SetForegroundWindow(hwnd)
        return bool(result)
    finally:
        _user32.AttachThreadInput(my_tid, fg_tid, False)


def _try_switch_direct(hwnd: int) -> bool:
    """Use ``SwitchToThisWindow`` as a standalone strategy.

    This simulates Alt+Tab and can work even when ``SetForegroundWindow``
    is blocked by UIPI or foreground-lock policies.
    """
    _user32.ShowWindow(hwnd, SW_RESTORE)
    _user32.SwitchToThisWindow(hwnd, True)
    _kernel32.Sleep(50)  # allow the switch to settle
    result = (_user32.GetForegroundWindow() == hwnd)
    log.info("_try_switch_direct hwnd=%d ok=%d", hwnd, result)
    return result


def _bring_to_foreground(hwnd: int) -> bool:
    """Bring *hwnd* to the foreground with retry and graceful degradation.

    Strategy cascade (ordered by success rate on Windows 11):
      1. SwitchToThisWindow (simulates Alt+Tab — most reliable on Win11).
      2. Alt-spoof via keybd_event (bypasses SendInput restrictions).
      3. AttachThreadInput + TOPMOST jolt + AllowSetForegroundWindow.
      4. Alt-spoof via SendInput (traditional approach).
      5. TOPMOST jolt standalone.
      6. Wait 400 ms, retry after foreground-lock cooldown.
      7. Flash taskbar (last resort).
    """
    title = _window_title(hwnd)

    # Restore minimized windows first
    if _is_window_minimized(hwnd):
        _user32.ShowWindow(hwnd, SW_RESTORE)

    # ── Strategy 1: SwitchToThisWindow (Alt+Tab sim) ──
    if _try_switch_direct(hwnd):
        log.info("_bring_to_foreground [switch-direct] hwnd=%d title=%r ✓", hwnd, title)
        return True
    log.info("_bring_to_foreground [switch-direct] hwnd=%d ✗", hwnd)

    # ── Strategy 2: Alt-spoof via keybd_event ──────────
    if _try_alt_spoof_vk(hwnd):
        log.info("_bring_to_foreground [alt-spoof-vk] hwnd=%d title=%r ✓", hwnd, title)
        return True
    log.info("_bring_to_foreground [alt-spoof-vk] hwnd=%d ✗", hwnd)

    # ── Strategy 3: AttachThreadInput + TOPMOST ──────
    if _try_attach_thread(hwnd):
        log.info("_bring_to_foreground [attach-thread] hwnd=%d ✓", hwnd)
        return True
    log.info("_bring_to_foreground [attach-thread] hwnd=%d ✗", hwnd)

    # ── Strategy 4: Alt-spoof via SendInput ──────────
    if _try_alt_spoof(hwnd):
        log.info("_bring_to_foreground [alt-spoof-sc] hwnd=%d title=%r ✓", hwnd, title)
        return True
    log.info("_bring_to_foreground [alt-spoof-sc] hwnd=%d ✗", hwnd)

    # ── Strategy 5: TOPMOST jolt standalone ──────────
    if _try_topmost_trick(hwnd):
        log.info("_bring_to_foreground [topmost-jolt] hwnd=%d ✓", hwnd)
        return True
    log.info("_bring_to_foreground [topmost-jolt] hwnd=%d ✗", hwnd)

    # ── Strategy 6: Retry after foreground-lock reset ─
    _kernel32.Sleep(400)
    if _try_switch_direct(hwnd):
        log.info("_bring_to_foreground [switch-direct-2] hwnd=%d ✓", hwnd)
        return True
    if _try_alt_spoof_vk(hwnd):
        log.info("_bring_to_foreground [alt-spoof-vk-2] hwnd=%d ✓", hwnd)
        return True
    if _user32.SetForegroundWindow(hwnd):
        log.info("_bring_to_foreground [bare-sfw] hwnd=%d ✓", hwnd)
        return True
    log.info("_bring_to_foreground [post-cooldown] hwnd=%d ✗", hwnd)

    # ── Strategy 7: Flash taskbar (last resort) ─────
    fw = FLASHWINFO()
    fw.cbSize = ctypes.sizeof(FLASHWINFO)
    fw.hwnd = hwnd
    fw.dwFlags = FLASHW_TRAY | FLASHW_TIMERNOFG
    fw.uCount = 0
    fw.dwTimeout = 0
    _user32.FlashWindowEx(ctypes.byref(fw))

    # Try one last SwitchToThisWindow after flashing
    try:
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SwitchToThisWindow(hwnd, True)
        if _user32.GetForegroundWindow() == hwnd:
            log.info("_bring_to_foreground [switch-flash] hwnd=%d ✓", hwnd)
            return True
    except Exception:
        pass

    log.warning("_bring_to_foreground: all strategies failed — taskbar flashing for %r", title)
    return False


def _window_title(hwnd: int) -> str:
    """Read the title text of *hwnd*."""
    buf = ctypes.create_unicode_buffer(256)
    _user32.GetWindowTextW(hwnd, buf, 255)
    return buf.value


# ── Toggle state (module-level, lives for daemon lifetime) ──
# pid → hwnd that was foreground before we focused the agent window
_saved_foreground: dict[int, int] = {}
# pid → monotonic timestamp of last toggle (cooldown to avoid rate-limiting)
_last_toggle_at: dict[int, float] = {}
_TOGGLE_COOLDOWN_S = 1.0  # minimum seconds between toggles for the same PID


# ── Public API ────────────────────────────────────


def focus_window_by_pid(pid: int) -> bool:
    """Walk from *pid* up the process tree, find the first visible
    window, and bring it to the foreground.

    Returns ``True`` if a window was found and focused.
    """
    if not _IS_WINDOWS or pid <= 0:
        return False

    chain = _ancestor_pids(pid)
    log.debug("focus: pid chain for %d → %s", pid, chain)

    # Try cached HWND first
    hwnd = _get_cached_hwnd(pid)
    if hwnd:
        log.info("focus: using cached hwnd=%d for pid=%d", hwnd, pid)
    else:
        hwnd = _find_window(chain)
        if hwnd:
            _hwnd_cache[pid] = hwnd

    if hwnd is None:
        return False

    _bring_to_foreground(hwnd)
    log.info("Focused window for pid=%d via ancestor chain %s (hwnd=%s, title=%r)",
             pid, chain, hwnd, _window_title(hwnd))
    return True


def toggle_window_by_pid(pid: int) -> dict:
    """Focus or unfocus the agent window based on its actual foreground state.

    - If the agent window is **not** the foreground window → save the
      current foreground, then bring the agent window to front.
    - If the agent window **is** already foreground → restore the
      previously-saved window.  If there is no saved window (or it
      no longer exists), minimise the agent window instead.

    Returns a status dict (suitable for JSON response).
    """
    if not _IS_WINDOWS or pid <= 0:
        return {"action": "error", "message": "unsupported platform"}

    # ── Cooldown guard ──────────────────────────────
    import time as _time
    now = _time.monotonic()
    last = _last_toggle_at.get(pid, 0)
    if now - last < _TOGGLE_COOLDOWN_S:
        return {"action": "cooldown", "message": "please wait before toggling again"}
    _last_toggle_at[pid] = now

    chain = _ancestor_pids(pid)
    log.info("Toggle: pid=%d full_chain=%s", pid, chain)

    # ── Build search chain (strip VibeDeck's own ancestry) ──
    import os as _os
    _own_pid = _os.getpid()
    _cut = len(chain)
    for _i, _p in enumerate(chain):
        if _p == _own_pid:
            _cut = _i  # stop before VibeDeck's own PID
            break
    search_chain = chain[:_cut] if _cut < len(chain) else chain
    if len(search_chain) < len(chain):
        log.info("Toggle: stripped VibeDeck ancestry — search_chain=%s", search_chain)

    # ── Try cached HWND first ─────────────────────
    agent_hwnd = _get_cached_hwnd(pid)
    used_cache = False
    if agent_hwnd:
        used_cache = True
        log.info("Toggle: using cached hwnd=%d for pid=%d", agent_hwnd, pid)

    if agent_hwnd is None:
        agent_hwnd = _find_window(search_chain)
        if agent_hwnd:
            _hwnd_cache[pid] = agent_hwnd
            log.info("Toggle: cached new hwnd=%d for pid=%d", agent_hwnd, pid)

    if agent_hwnd is None:
        return {"action": "error", "message": f"PID {pid} has no visible terminal windows"}

    agent_title = _window_title(agent_hwnd)

    current_fg = _user32.GetForegroundWindow()
    is_foreground = (agent_hwnd == current_fg)

    if not is_foreground:
        # ── Agent not in front → save current, focus agent ──
        _saved_foreground[pid] = current_fg
        ok = _bring_to_foreground(agent_hwnd)
        if not ok and used_cache:
            # Cached hwnd may be stale (e.g. early binding found a
            # transient window).  Clear the cache, re-find, and retry.
            log.warning("Toggle: focus failed with cached hwnd=%d — retrying with fresh find",
                        agent_hwnd)
            clear_hwnd_cache(pid)
            agent_hwnd = _find_window(search_chain)
            if agent_hwnd:
                _hwnd_cache[pid] = agent_hwnd
                agent_title = _window_title(agent_hwnd)
                log.info("Toggle: re-found hwnd=%d title=%r — retrying focus",
                         agent_hwnd, agent_title)
                ok = _bring_to_foreground(agent_hwnd)
        if not ok:
            # Focus failed — clear state, taskbar is already flashing
            _saved_foreground.pop(pid, None)
            return {"action": "flashing",
                    "title": agent_title,
                    "message": f"'{agent_title}' taskbar is flashing. Click it or Alt+Tab."}
        log.info("Toggle: focused agent pid=%d (hwnd=%s, title=%r, saved_fg=%s)",
                 pid, agent_hwnd, agent_title, current_fg)
        return {"action": "focused", "pid": pid, "title": agent_title, "restorable": True}

    # ── Agent already foreground → restore or minimise ──
    prev_hwnd = _saved_foreground.pop(pid, 0)
    if prev_hwnd and _user32.IsWindow(prev_hwnd) and prev_hwnd != agent_hwnd:
        _bring_to_foreground(prev_hwnd)
        log.info("Toggle: restored previous window for pid=%d (hwnd=%s, title=%r)",
                 pid, prev_hwnd, _window_title(prev_hwnd))
        return {"action": "restored", "pid": pid, "title": _window_title(prev_hwnd)}

    # No saved window (or self-loop / dead) → minimise
    _user32.ShowWindow(agent_hwnd, 6)  # SW_MINIMIZE
    log.info("Toggle: minimised agent pid=%d (no saved window or self-loop)", pid)
    return {"action": "minimised", "pid": pid}


def clear_toggle_state(pid: int | None = None) -> None:
    """Clear saved toggle state.  If *pid* is ``None``, clear all."""
    if pid is None:
        _saved_foreground.clear()
    else:
        _saved_foreground.pop(pid, None)


def find_window_title(pid: int) -> str | None:
    """Walk from *pid* up the process tree and return the title of the
    first visible window found, or ``None``."""
    if not _IS_WINDOWS or pid <= 0:
        return None

    chain = _ancestor_pids(pid)
    hwnd = _get_cached_hwnd(pid) or _find_window(chain)
    return _window_title(hwnd) if hwnd is not None else None


def find_and_cache_hwnd(pid: int) -> int | None:
    """Find the terminal window for *pid* and cache the result.

    Called by ProcessScanner at agent discovery time (early binding).
    Returns the HWND if found, else None.
    """
    if not _IS_WINDOWS or pid <= 0:
        return None

    # Check cache first
    cached = _get_cached_hwnd(pid)
    if cached:
        return cached

    chain = _ancestor_pids(pid)
    hwnd = _find_window(chain)
    if hwnd:
        _hwnd_cache[pid] = hwnd
        log.info("find_and_cache_hwnd: pid=%d → hwnd=%d (title=%r, class=%r)",
                 pid, hwnd, _window_title(hwnd), _get_window_class(hwnd))
    return hwnd
