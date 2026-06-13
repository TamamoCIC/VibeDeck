"""
VibeDeck Terminal Registry — manages connected Terminals and their tokens.

Provides CRUD operations for TerminalInfo records backed by the
persistent config file (~/.vibe-deck/config.yaml).
"""

from __future__ import annotations

import logging
from typing import Optional

from ..config import TerminalInfo, VibeDeckConfig, load_config, save_config

log = logging.getLogger("vibe_deck.core.registry")


class TerminalRegistry:
    """
    Registry of all Terminals (physical and virtual).

    Backed by the persistent config file. Tokens are per-device UUIDs
    that identify Virtual Terminals across reconnections.

    Usage:
        registry = TerminalRegistry()
        registry.load()
        t = registry.get_by_token("abc123")
        if t is None:
            t = registry.register("phone-01", "virtual", "4x8")
    """

    def __init__(self) -> None:
        self._config: VibeDeckConfig | None = None

    # ── Load / Save ───────────────────────────────

    def load(self) -> VibeDeckConfig:
        """Load config from disk, creating defaults if absent."""
        self._config = load_config()
        self._ensure_default_terminal()
        return self._config

    def save(self) -> None:
        """Persist current config to disk."""
        if self._config is None:
            return
        save_config(self._config)

    def _ensure_default_terminal(self) -> None:
        """Create a default virtual terminal if none exist.

        The type starts as ``"virtual"`` — it is only promoted to
        ``"physical"`` when hardware detection succeeds during startup.
        """
        if self._config is None:
            return
        if not self._config.terminals:
            default = TerminalInfo.create(
                name="default",
                terminal_type="virtual",
                grid="4x8",
                layout="default-streamdeck-xl.yaml",
                terminal_id="default",
            )
            self._config.terminals.append(default)
            self.save()
            log.info("Created default terminal with token %s", default.token[:8])

    # ── Query ─────────────────────────────────────

    def get_by_token(self, token: str) -> TerminalInfo | None:
        """Find a terminal by its auth token."""
        if self._config is None:
            return None
        for t in self._config.terminals:
            if t.token == token:
                return t
        return None

    def get_by_id(self, terminal_id: str) -> TerminalInfo | None:
        """Find a terminal by its id."""
        if self._config is None:
            return None
        for t in self._config.terminals:
            if t.id == terminal_id:
                return t
        return None

    def get_by_name(self, name: str) -> TerminalInfo | None:
        """Find a terminal by its display name."""
        if self._config is None:
            return None
        for t in self._config.terminals:
            if t.name == name:
                return t
        return None

    def list_all(self) -> list[TerminalInfo]:
        """Return all registered terminals."""
        if self._config is None:
            return []
        return list(self._config.terminals)

    # ── Mutations ─────────────────────────────────

    def register(
        self,
        name: str,
        terminal_type: str,
        grid: str,
        layout: str = "",
    ) -> TerminalInfo:
        """Create and persist a new terminal. Returns the new TerminalInfo."""
        if self._config is None:
            self.load()

        t = TerminalInfo.create(
            name=name,
            terminal_type=terminal_type,
            grid=grid,
            layout=layout,
        )
        self._config.terminals.append(t)
        self.save()
        log.info("Registered %s terminal %r (grid=%s, token=%s...)", terminal_type, name, grid, t.token[:8])
        return t

    def remove(self, terminal_id: str) -> bool:
        """Remove a terminal by id. Returns True if removed."""
        if self._config is None:
            return False
        for i, t in enumerate(self._config.terminals):
            if t.id == terminal_id:
                self._config.terminals.pop(i)
                self.save()
                log.info("Removed terminal %r", t.name)
                return True
        return False

    def remove_by_token(self, token: str) -> bool:
        """Remove a terminal by auth token. Returns True if removed."""
        t = self.get_by_token(token)
        if t is None:
            return False
        return self.remove(t.id)

    def rename(self, terminal_id: str, new_name: str) -> bool:
        """Rename a terminal. Updates config and saves. Returns True on success."""
        t = self.get_by_id(terminal_id)
        if t is None:
            return False
        t.name = new_name
        self.save()
        log.info("Renamed terminal %r to %r", terminal_id, new_name)
        return True

    def update_grid(self, terminal_id: str, new_grid: str) -> bool:
        """Change terminal grid. Validates format (NxM). Updates and saves."""
        t = self.get_by_id(terminal_id)
        if t is None:
            return False
        parts = new_grid.split("x")
        if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
            log.warning("Invalid grid format %r — expected NxM", new_grid)
            return False
        t.grid = new_grid
        self.save()
        log.info("Updated grid for terminal %r to %r", terminal_id, new_grid)
        return True

    def count(self) -> int:
        """Return the number of registered terminals."""
        if self._config is None:
            return 0
        return len(self._config.terminals)
