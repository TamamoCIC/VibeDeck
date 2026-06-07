#!/usr/bin/env python3
"""
Claude Code Status Reporter — writes agent status to ~/.vibe-deck/agents/

Detects Claude Code processes by scanning for claude/claude.exe.
Writes real-time status JSON that VibeDeck's file watcher picks up.

Usage:
    python -m vibe_deck.adapters.claude_code.reporter
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import psutil

log = logging.getLogger("vibe_deck.reporter.claude_code")

VIBEDECK_DIR = Path.home() / ".vibe-deck" / "agents"

# Process detection patterns
PROCESS_PATTERNS = [
    # Windows: Claude Code ships as claude.exe
    {"name_lower": "claude.exe", "platform": "win32"},
    # Linux/macOS: node process with claude in the command line
    {"name_lower": "node", "cmd_contains": "claude"},
    # Direct match on Linux
    {"name_lower": "claude", "platform": "linux"},
]

# Session tracking file — persists session_id across restarts
SESSION_LOG = Path.home() / ".vibe-deck" / "adapters" / "claude_code" / "sessions.json"


def find_claude_processes() -> list[dict]:
    """Find all Claude Code processes."""
    found = []
    current_platform = os.name  # "nt" for Windows, "posix" for Linux/macOS

    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time"]):
        try:
            info = proc.info
            name = (info["name"] or "").lower()
            cmdline = " ".join(info["cmdline"] or [])

            for pattern in PROCESS_PATTERNS:
                # Platform filter
                if "platform" in pattern:
                    plat = pattern["platform"]
                    if (plat == "win32" and current_platform != "nt"):
                        continue
                    if (plat == "linux" and current_platform != "posix"):
                        continue

                # Name match
                if pattern["name_lower"] != name:
                    continue

                # Command line filter
                if "cmd_contains" in pattern:
                    if pattern["cmd_contains"].lower() not in cmdline.lower():
                        continue

                found.append({
                    "pid": info["pid"],
                    "name": info["name"],
                    "cmdline": cmdline[:300],
                    "started_at": info["create_time"],
                })
                break
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    return found


def get_last_session_id() -> str | None:
    """Get the most recent Claude Code session ID from the log."""
    if not SESSION_LOG.exists():
        return None
    try:
        data = json.loads(SESSION_LOG.read_text())
        sessions = data.get("sessions", [])
        if sessions:
            return sessions[-1].get("session_id")
    except Exception:
        pass
    return None


def record_session(session_id: str, pid: int) -> None:
    """Record a session in the log."""
    SESSION_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        if SESSION_LOG.exists():
            data = json.loads(SESSION_LOG.read_text())
        else:
            data = {"sessions": []}

        # Check if this session already recorded
        sessions = data["sessions"]
        if not any(s.get("session_id") == session_id for s in sessions):
            sessions.append({
                "session_id": session_id,
                "pid": pid,
                "last_seen": time.time(),
            })

        # Keep only last 50
        data["sessions"] = sessions[-50:]
        SESSION_LOG.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def write_status(agent_name: str, status: str, info: str = "", session_id: str = "",
                 pid: int = 0, cwd: str = "") -> None:
    """Write agent status JSON for the file watcher."""
    VIBEDECK_DIR.mkdir(parents=True, exist_ok=True)

    output = {
        "agent": "Claude Code",
        "agent_name": agent_name,
        "status": status,
        "info": info,
        "session_id": session_id,
        "pid": pid,
        "cwd": cwd,
        "timestamp": time.time(),
    }

    filename = VIBEDECK_DIR / f"{agent_name}.json"
    filename.write_text(json.dumps(output, indent=2))


async def monitor_loop(interval: float = 3.0) -> None:
    """
    Main monitoring loop.

    1. Find Claude Code processes
    2. For each one found, check if it's still alive
    3. Write status files
    4. Detect process exit → write offline status
    """
    known_pids: dict[int, dict] = {}  # pid → session info

    while True:
        try:
            current = find_claude_processes()
            current_pids = {p["pid"] for p in current}

            # New processes
            for proc in current:
                pid = proc["pid"]
                if pid not in known_pids:
                    session_id = f"claude-{pid}"
                    agent_name = f"claude-code-{pid}"
                    known_pids[pid] = {
                        "agent_name": agent_name,
                        "session_id": session_id,
                        "started_at": proc["started_at"],
                    }
                    record_session(session_id, pid)
                    log.info("🟢 Claude Code detected: PID=%d", pid)
                    write_status(agent_name, "running", "Session started", session_id, pid)

            # Check each known process is still running
            for pid, info in list(known_pids.items()):
                if pid not in current_pids:
                    # Process exited — write offline status
                    write_status(info["agent_name"], "offline", "Process exited",
                                 info["session_id"], pid)
                    log.info("🔴 Claude Code exited: PID=%d", pid)
                    # Remove status file after a delay? Or keep offline status.
                    del known_pids[pid]

            # Update running processes with heartbeat
            for pid, info in known_pids.items():
                write_status(info["agent_name"], "running",
                             f"Active (PID={pid})", info["session_id"], pid)

            log.debug("Monitor tick: %d Claude Code process(es)", len(known_pids))

        except Exception:
            log.exception("Monitor error")

        await asyncio.sleep(interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    procs = find_claude_processes()
    if procs:
        print(f"Found {len(procs)} Claude Code process(es):")
        for p in procs:
            print(f"  PID={p['pid']} cmd={p['cmdline'][:120]}")
    else:
        print("No Claude Code processes found. Start Claude Code first.")

    asyncio.run(monitor_loop())
