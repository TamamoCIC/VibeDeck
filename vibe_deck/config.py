"""
VibeDeck configuration loading.

Reads from ~/.vibe-deck/config.yaml with sensible defaults.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

CONFIG_SCHEMA_VERSION = 1

VIBEDECK_HOME = Path.home() / ".vibe-deck"
CONFIG_FILE = VIBEDECK_HOME / "config.yaml"
AGENTS_DIR = VIBEDECK_HOME / "agents"
LAYOUTS_DIR = VIBEDECK_HOME / "layouts"
ADAPTERS_DIR = VIBEDECK_HOME / "adapters"
SKILLS_DIR = VIBEDECK_HOME / "skills"
TEMPLATES_DIR = VIBEDECK_HOME / "templates"

DEFAULT_PORT = 9734
DEFAULT_RENDER = "sim"


@dataclass
class AgentPattern:
    """Process-matching rule for auto-discovery."""

    name: str
    process: str
    args_contains: list[str] = field(default_factory=list)
    cmdline_regex: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "AgentPattern":
        return cls(
            name=d["name"],
            process=d["process"],
            args_contains=d.get("args_contains", []),
            cmdline_regex=d.get("cmdline_regex"),
        )


@dataclass
class MCPServerConfig:
    """External MCP server definition."""

    name: str
    command: list[str]
    args: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "MCPServerConfig":
        return cls(
            name=d["name"],
            command=d["command"] if isinstance(d["command"], list) else [d["command"]],
            args=d.get("args", []),
        )


@dataclass
class TimingConfig:
    """Timing parameters for frame scheduling and activity detection."""

    thinking_timeout_ms: int = 800
    activity_window_ms: int = 3000
    fast_frame_interval_ms: int = 33
    slow_frame_interval_ms: int = 1000

    @classmethod
    def from_dict(cls, d: dict) -> "TimingConfig":
        return cls(
            thinking_timeout_ms=d.get("thinking_timeout_ms", 800),
            activity_window_ms=d.get("activity_window_ms", 3000),
            fast_frame_interval_ms=d.get("fast_frame_interval_ms", 33),
            slow_frame_interval_ms=d.get("slow_frame_interval_ms", 1000),
        )

    def to_dict(self) -> dict:
        return {
            "thinking_timeout_ms": self.thinking_timeout_ms,
            "activity_window_ms": self.activity_window_ms,
            "fast_frame_interval_ms": self.fast_frame_interval_ms,
            "slow_frame_interval_ms": self.slow_frame_interval_ms,
        }


@dataclass
class AdapterConfig:
    """Configuration for a registered adapter (e.g. streamdeck, websocket, midi)."""

    name: str
    enabled: bool = True
    config: dict = field(default_factory=dict)

    # Per-event appearance overrides: event_name -> {icon,color,animation,label,min_display_ms}
    appearance: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "AdapterConfig":
        return cls(
            name=d["name"],
            enabled=d.get("enabled", True),
            config=d.get("config", {}),
            appearance=d.get("appearance", {}),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "config": self.config,
            "appearance": self.appearance,
        }


@dataclass
class TerminalInfo:
    """A registered Terminal (physical or virtual)."""

    id: str
    name: str
    type: str  # "physical" | "virtual"
    grid: str  # e.g. "4x8"
    layout: str  # filename
    token: str
    created_at: str = ""

    @classmethod
    def create(
        cls, name: str, terminal_type: str, grid: str, layout: str = "", terminal_id: str = ""
    ) -> "TerminalInfo":
        """Create a new TerminalInfo with auto-generated UUID token."""
        tid = terminal_id or str(uuid.uuid4())
        return cls(
            id=tid,
            name=name,
            type=terminal_type,
            grid=grid,
            layout=layout or f"{name}.yaml",
            token=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_dict(cls, d: dict) -> "TerminalInfo":
        return cls(
            id=d.get("id", str(uuid.uuid4())),
            name=d.get("name", "unknown"),
            type=d.get("type", "virtual"),
            grid=d.get("grid", "4x8"),
            layout=d.get("layout", ""),
            token=d.get("token", str(uuid.uuid4())),
            created_at=d.get("created_at", ""),
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "grid": self.grid,
            "layout": self.layout,
            "token": self.token,
            "created_at": self.created_at,
        }


@dataclass
class VibeDeckConfig:
    """Root configuration for VibeDeck."""

    port: int = DEFAULT_PORT
    render: str = DEFAULT_RENDER  # DEPRECATED: hardware detection is now automatic
    device_index: int = 0
    expose: bool = False  # bind to 0.0.0.0 instead of localhost
    autodetect: bool = True
    agent_patterns: list[AgentPattern] = field(default_factory=list)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    terminals: list[TerminalInfo] = field(default_factory=list)
    timing: TimingConfig = field(default_factory=TimingConfig)
    adapter_configs: list[AdapterConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VibeDeckConfig":
        return cls(
            port=d.get("port", DEFAULT_PORT),
            render=d.get("render", DEFAULT_RENDER),
            device_index=d.get("device_index", 0),
            expose=d.get("expose", False),
            autodetect=d.get("autodetect", True),
            agent_patterns=[
                AgentPattern.from_dict(p) for p in d.get("agent_patterns", [])
            ],
            mcp_servers=[
                MCPServerConfig.from_dict(s) for s in d.get("mcp_servers", [])
            ],
            terminals=[
                TerminalInfo.from_dict(t) for t in d.get("terminals", [])
            ],
            timing=TimingConfig.from_dict(d.get("timing", {})),
            adapter_configs=[
                AdapterConfig.from_dict(a) for a in d.get("adapter_configs", [])
            ],
        )


def ensure_dirs() -> None:
    """Create all VibeDeck directories if they don't exist."""
    for d in [VIBEDECK_HOME, AGENTS_DIR, LAYOUTS_DIR, ADAPTERS_DIR, SKILLS_DIR, TEMPLATES_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _default_config() -> VibeDeckConfig:
    """Build the default configuration with built-in agent patterns and a default terminal.

    The default terminal starts as ``"virtual"`` — it is promoted to
    ``"physical"`` only when Stream Deck hardware is detected at startup.
    """
    default_terminal = TerminalInfo.create(
        name="default",
        terminal_type="virtual",
        grid="4x8",
        layout="default-streamdeck-xl.yaml",
        terminal_id="default",
    )
    return VibeDeckConfig(
        agent_patterns=[
            AgentPattern(name="claude-code", process="claude"),
            AgentPattern(name="claude-code", process="claude.exe"),
            # npm / npx installs on Windows may run as node.exe
            AgentPattern(name="claude-code", process="node", args_contains=["claude"]),
            AgentPattern(name="claude-code", process="node.exe", args_contains=["claude"]),
            AgentPattern(name="opencode", process="opencode", args_contains=["serve"]),
            AgentPattern(name="openclaw", process="openclaw"),
        ],
        terminals=[default_terminal],
    )


def load_config(path: Path | None = None) -> VibeDeckConfig:
    """Load configuration from disk, creating defaults if absent."""
    ensure_dirs()

    config_path = path or CONFIG_FILE

    if not config_path.exists():
        default = _default_config()
        save_config(default, config_path)
        return default

    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    return VibeDeckConfig.from_dict(raw)


def save_config(config: VibeDeckConfig, path: Path | None = None) -> None:
    """Write configuration to disk."""
    config_path = path or CONFIG_FILE
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert to dict manually (dataclasses.asdict would work but is less explicit)
    d = {
        "schema_version": CONFIG_SCHEMA_VERSION,
        "port": config.port,
        "render": config.render,
        "device_index": config.device_index,
        "expose": config.expose,
        "autodetect": config.autodetect,
        "agent_patterns": [
            {
                "name": p.name,
                "process": p.process,
                "args_contains": p.args_contains,
                "cmdline_regex": p.cmdline_regex,
            }
            for p in config.agent_patterns
        ],
        "mcp_servers": [
            {"name": s.name, "command": s.command, "args": s.args}
            for s in config.mcp_servers
        ],
        "terminals": [t.to_dict() for t in config.terminals],
        "timing": config.timing.to_dict(),
        "adapter_configs": [a.to_dict() for a in config.adapter_configs],
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f, default_flow_style=False, allow_unicode=True, indent=2)
