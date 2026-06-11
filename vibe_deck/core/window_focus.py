"""
Window focus utilities — find and focus an agent's window by PID.

Uses Windows API via ctypes to enumerate top-level windows, match by
process ID, and bring the target window to the foreground.

Foreground activation is achieved via the "Alt-spoofing" technique:
inject a synthetic Left-Alt key-down event with ``SendInput`` (scan
code, not virtual-key code), which temporarily grants the calling
thread foreground-activation rights so ``SetForegroundWindow`` can
succeed from a background daemon process.

Because AI agents (Claude Code, etc.) run inside a terminal, we walk
the process tree upward to find the ancestor that actually owns the
visible window.

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
    # Ensure this thread has a Windows message queue.  Some window-
    # management APIs (including SendInput) require it.
    _user32.PeekMessageW(None, 0, 0, 0, 0x0001)  # PM_NOREMOVE

    # ── ctypes structures for SendInput (Alt-spoofing) ──────────
    INPUT_KEYBOARD = 1
    KEYEVENTF_SCANCODE = 0x0008
    KEYEVENTF_KEYUP = 0x0002
    LEFT_ALT_SCANCODE = 0x0038  # documented Left Alt scan code
    SW_RESTORE = 9

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


def _find_window(pids: list[int]) -> int | None:
    """Return the first visible, titled window handle belonging to any
    PID in *pids* (searched in order — deepest descendant first so the
    most specific match wins).

    Windows whose title is exactly ``"VibeDeck"`` (the daemon's own
    terminal) are excluded — VibeDeck and the agent are sibling
    processes under the same terminal, so the terminal window title
    can be inherited from VibeDeck's tab.
    """
    pids_set = set(pids)
    found: list[tuple[int, int]] = []  # (pid_index_in_chain, hwnd)

    def _enum_proc(hwnd: int, _lparam: int) -> bool:
        process_id = wintypes.DWORD()
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
        pid = process_id.value
        if pid in pids_set and _user32.IsWindowVisible(hwnd):
            title_len = _user32.GetWindowTextLengthW(hwnd)
            if title_len > 0:
                buf = ctypes.create_unicode_buffer(256)
                _user32.GetWindowTextW(hwnd, buf, 255)
                title = buf.value
                # Skip VibeDeck's own terminal window
                if title.strip().lower() == "vibedeck":
                    return True  # keep enumerating
                idx = pids.index(pid) if pid in pids else 999
                found.append((idx, hwnd))
        return True

    _user32.EnumWindows(_WNDENUMPROC(_enum_proc), 0)

    # Prefer the deepest descendant (lowest index in chain → agent PID
    # itself if it has a window, then parent, etc.)
    found.sort(key=lambda x: x[0])
    return found[0][1] if found else None


def _is_window_minimized(hwnd: int) -> bool:
    """Return True if *hwnd* is currently minimized (iconic)."""
    return bool(_user32.IsIconic(hwnd))


def _try_alt_spoof(hwnd: int) -> bool:
    """Single shot of Alt-spoofing.  Returns True if focus succeeded."""
    sent_down = _user32.SendInput(1, ctypes.byref(_ALT_DOWN), _SENDINPUT_SIZEOF)
    _kernel32.Sleep(1)
    try:
        result = _user32.SetForegroundWindow(hwnd)
    finally:
        sent_up = _user32.SendInput(1, ctypes.byref(_ALT_UP), _SENDINPUT_SIZEOF)
    log.debug("_try_alt_spoof hwnd=%s send_down=%s sfw=%s send_up=%s",
              hwnd, sent_down, result, sent_up)
    return bool(result)


def _bring_to_foreground(hwnd: int) -> bool:
    """Bring *hwnd* to the foreground with retry and graceful degradation.

    Strategy cascade:
      1. Alt-spoofing (instant — normal operation).
      2. Wait 400 ms, retry Alt-spoofing (post-unlock — foreground lock
         is briefly absent after a session unlock).
      3. Flash taskbar + SwitchToThisWindow (persistent UIPI block).
    """
    title = _window_title(hwnd)

    # ── Strategy 1: Alt-spoofing ─────────────────────
    if _try_alt_spoof(hwnd):
        log.info("_bring_to_foreground [alt-spoof-1] hwnd=%s title=%r ✓", hwnd, title)
        if _is_window_minimized(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        return True

    log.info("_bring_to_foreground [alt-spoof-1] hwnd=%s title=%r ✗ — will retry", hwnd, title)

    # ── Strategy 2: Retry after foreground-lock reset ─
    # After a session unlock, the foreground lock timer restarts.
    # Waiting ~400 ms gives it time to expire, after which even a
    # plain SetForegroundWindow (without Alt-spoofing) can succeed.
    _kernel32.Sleep(400)
    if _try_alt_spoof(hwnd):
        log.info("_bring_to_foreground [alt-spoof-2] hwnd=%s ✓", hwnd)
        if _is_window_minimized(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        return True
    # Also try bare SetForegroundWindow — post-unlock it might just work
    if _user32.SetForegroundWindow(hwnd):
        log.info("_bring_to_foreground [bare-sfw] hwnd=%s ✓", hwnd)
        if _is_window_minimized(hwnd):
            _user32.ShowWindow(hwnd, SW_RESTORE)
        return True

    # ── Strategy 3: Flash + SwitchToThisWindow ────────
    # Can't steal focus (persistent UIPI block).  Flash the taskbar
    # so the user sees which window to switch to.
    fw = FLASHWINFO()
    fw.cbSize = ctypes.sizeof(FLASHWINFO)
    fw.hwnd = hwnd
    fw.dwFlags = FLASHW_TRAY | FLASHW_TIMERNOFG
    fw.uCount = 0
    fw.dwTimeout = 0
    _user32.FlashWindowEx(ctypes.byref(fw))

    try:
        _user32.ShowWindow(hwnd, SW_RESTORE)
        _user32.SwitchToThisWindow(hwnd, True)
        if _user32.GetForegroundWindow() == hwnd:
            log.info("_bring_to_foreground [switch] hwnd=%s ✓", hwnd)
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

    hwnd = _find_window(chain)
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

    # ── Strip VibeDeck's own ancestry ──────────────────
    # If the agent was launched from the same terminal as VibeDeck,
    # the ancestor chain will include VibeDeck's PID and its parents.
    # Searching those would match VibeDeck's own terminal window.
    # We only care about PIDs *below* VibeDeck in the tree.
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

    agent_hwnd = _find_window(search_chain)

    if agent_hwnd is None:
        # If the stripped chain is empty, the agent IS VibeDeck (or a child
        # with no terminal window of its own).
        if not search_chain:
            return {"action": "error", "message": f"PID {pid} is VibeDeck or a direct child with no window"}
        return {"action": "error", "message": f"PID {pid} has no visible windows"}

    agent_title = _window_title(agent_hwnd)

    current_fg = _user32.GetForegroundWindow()
    is_foreground = (agent_hwnd == current_fg)

    if not is_foreground:
        # ── Agent not in front → save current, focus agent ──
        _saved_foreground[pid] = current_fg
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
    hwnd = _find_window(chain)
    return _window_title(hwnd) if hwnd is not None else None
