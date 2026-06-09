#!/usr/bin/env python3
"""
Claude Code Hook Reporter for VibeDeck.

Reads hook event JSON from stdin (Claude Code passes it to hook scripts),
appends to a JSONL event stream file that VibeDeck's FileWatcher picks up.

Usage (configured in Claude Code hooks settings):
    python /path/to/reporter.py

Design:
    - Pure Python stdlib — zero imports beyond stdlib, ~30ms cold start
    - Always exits 0 — never blocks or errors Claude Code
    - Append-only JSONL — one line per event, no read-then-write races
    - Timestamped on receipt — _vibedeck_ts records when VibeDeck saw it
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

MAX_LINES = 1000
DEFAULT_VIBEDECK_HOME = Path.home() / ".vibe-deck"

# Lone surrogates (\udc00-\udfff) can appear in Claude's JSON output but
# are not valid Unicode and cannot be encoded to UTF-8.  Replace them with
# U+FFFD (replacement character) so the JSONL write never fails.
_SURR_REPL = "\\ufffd"


def _sanitize_surrogates(s: str) -> str:
    """Replace lone surrogates in `s` with \\ufffd."""
    out: list[str] = []
    for ch in s:
        cp = ord(ch)
        if 0xDC00 <= cp <= 0xDFFF:
            out.append(_SURR_REPL)
        else:
            out.append(ch)
    return "".join(out)


def _vibedeck_home() -> Path:
    """Resolve VibeDeck data directory."""
    env = os.environ.get("VIBEDECK_HOME", "")
    if env:
        return Path(env)
    return DEFAULT_VIBEDECK_HOME


def _find_claude_code_pid() -> int | None:
    """Walk up the process tree to find the actual Claude Code PID.

    Claude Code may:
    1. Spawn python directly (no shell) → ppid = Claude Code PID
    2. Spawn via cmd.exe → ppid = transient shell PID, gpid = Claude Code PID
    3. Spawn via intermediate process → need to walk multiple levels

    Walk up max 5 levels looking for a process whose executable name
    contains "claude".  Fall back through shell detection to os.getppid().
    """
    try:
        current = os.getpid()

        for _depth in range(5):
            parent = _get_parent_pid(current)
            if parent is None or parent <= 1:
                break
            name = _get_process_name(parent)
            if name and b"claude" in name.lower():
                return parent
            current = parent

        # Fallback: if parent is a shell, use grandparent
        ppid = os.getppid()
        gpid = _get_parent_pid(ppid)
        if gpid and _is_shell_process(ppid):
            return gpid
        return ppid
    except Exception:
        return None


# Shell process names that sit between Claude Code and the hook script.
# Compared case-insensitively — Windows API may return mixed-case names.
_SHELL_NAMES = {"cmd.exe", "bash.exe", "bash", "sh", "sh.exe",
                "zsh", "zsh.exe", "dash", "pwsh.exe", "powershell.exe"}


def _is_shell_process(pid: int) -> bool:
    """Return True if *pid* belongs to a transient shell process."""
    name = _get_process_name(pid)
    if name is None:
        return False
    try:
        name_str = name.decode("utf-8", errors="replace").lower()
    except Exception:
        return False
    return name_str in _SHELL_NAMES


# Windows toolhelp API helpers — shared by _get_process_name and
# _get_parent_pid_windows to avoid code duplication.

if sys.platform == 'win32':
    import ctypes as _ctypes
    from ctypes import wintypes as _wintypes

    class _PROCESSENTRY32(_ctypes.Structure):
        _fields_ = [
            ("dwSize",              _wintypes.DWORD),
            ("cntUsage",            _wintypes.DWORD),
            ("th32ProcessID",       _wintypes.DWORD),
            ("th32DefaultHeapID",   _ctypes.POINTER(_wintypes.ULONG)),
            ("th32ModuleID",        _wintypes.DWORD),
            ("cntThreads",          _wintypes.DWORD),
            ("th32ParentProcessID", _wintypes.DWORD),
            ("pcPriClassBase",      _wintypes.LONG),
            ("dwFlags",             _wintypes.DWORD),
            ("szExeFile",           _wintypes.CHAR * 260),
        ]

    _TH32CS_SNAPPROCESS = 0x00000002

    def _kernel32():
        return _ctypes.windll.kernel32
else:
    _PROCESSENTRY32 = None
    _TH32CS_SNAPPROCESS = 0
    def _kernel32():
        return None


def _get_process_name(pid: int) -> bytes | None:
    """Get the executable name (e.g. b'cmd.exe') for a PID on Windows."""
    if sys.platform != 'win32':
        return None

    try:
        kernel32 = _kernel32()
    except (AttributeError, OSError):
        return None

    h_snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    INVALID_HANDLE_VALUE = _wintypes.HANDLE(-1).value
    if h_snap == INVALID_HANDLE_VALUE:
        return None

    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = _ctypes.sizeof(_PROCESSENTRY32)

        if kernel32.Process32First(h_snap, _ctypes.byref(entry)):
            while True:
                if entry.th32ProcessID == pid:
                    return entry.szExeFile
                if not kernel32.Process32Next(h_snap, _ctypes.byref(entry)):
                    break
    finally:
        kernel32.CloseHandle(h_snap)

    return None


def _get_parent_pid(pid: int) -> int | None:
    """Get the parent PID of a process.  Cross-platform, stdlib only."""
    if sys.platform == 'win32':
        return _get_parent_pid_windows(pid)
    else:
        return _get_parent_pid_unix(pid)


def _get_parent_pid_windows(pid: int) -> int | None:
    """Get parent PID on Windows via CreateToolhelp32Snapshot (~1-5 ms)."""
    try:
        kernel32 = _kernel32()
    except (AttributeError, OSError):
        return None

    h_snap = kernel32.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    INVALID_HANDLE_VALUE = _wintypes.HANDLE(-1).value
    if h_snap == INVALID_HANDLE_VALUE:
        return None

    try:
        entry = _PROCESSENTRY32()
        entry.dwSize = _ctypes.sizeof(_PROCESSENTRY32)

        if not kernel32.Process32First(h_snap, _ctypes.byref(entry)):
            return None

        while True:
            if entry.th32ProcessID == pid:
                ppid = entry.th32ParentProcessID
                return int(ppid) if ppid else None
            if not kernel32.Process32Next(h_snap, _ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(h_snap)

    return None


def _get_parent_pid_unix(pid: int) -> int | None:
    """Get parent PID on Unix via /proc (fast, stdlib only)."""
    try:
        stat = Path(f'/proc/{pid}/stat').read_text()
        # Format: pid (comm) state ppid ...
        parts = stat.split()
        if len(parts) >= 4:
            return int(parts[3])
    except Exception:
        pass
    return None


def _output_file(event: dict | None = None) -> Path:
    """Path to the Claude Code JSONL event stream.

    Uses the actual Claude Code PID (found by walking the process tree
    past the intermediate shell) as the stable filename key.  Falls back
    to ``session_id`` from the hook event when process-tree walking fails.

    Each Claude Code process gets its own file so VibeDeck can track
    multiple instances independently — the FileWatcher derives the
    widget_id from the filename.
    """
    agents_dir = _vibedeck_home() / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)

    # 1. Best effort: find the real Claude Code PID
    agent_pid = _find_claude_code_pid()
    if agent_pid is not None and agent_pid > 1:
        return agents_dir / f"claude-code-{agent_pid}.jsonl"

    # 2. Fallback: use session_id from the event (stable within a session)
    if event:
        session_id = event.get("session_id", "")
        if session_id:
            return agents_dir / f"claude-code-{session_id[:8]}.jsonl"

    # 3. Last resort: parent PID (may be the shell PID — imperfect on Windows)
    return agents_dir / f"claude-code-{os.getppid()}.jsonl"


def main() -> None:
    """Read hook event from stdin, append to JSONL, exit cleanly."""
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            sys.exit(0)
        event = json.loads(raw)
    except (json.JSONDecodeError, Exception):
        # Malformed input → silently ignore. Never block Claude Code.
        sys.exit(0)

    # Annotate with receipt metadata
    event["_vibedeck_ts"] = datetime.now(timezone.utc).isoformat()
    event["_vibedeck_source"] = "claude-code-hook"

    # Minimal stderr log so users can confirm hooks are firing
    hook_event = event.get("hook_event_name", "?")
    tool_name = event.get("tool_name", "")
    session_id = event.get("session_id", "")[:8]
    msg = f"[VibeDeck] {hook_event}"
    if tool_name:
        msg += f" tool={tool_name}"
    print(f"{msg} sid={session_id}", file=sys.stderr, flush=True)

    # Use compact JSON for one-line-per-event.
    # Sanitize lone surrogates that Claude's output may contain — these are
    # valid in JSON strings but cannot be encoded to UTF-8.
    line = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    line = _sanitize_surrogates(line) + "\n"

    try:
        with open(_output_file(event), "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Disk full, permissions, etc. — silently ignore.
        pass

    sys.exit(0)


if __name__ == "__main__":
    main()
