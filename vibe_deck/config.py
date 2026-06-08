"""
VibeDeck configuration loading.

Reads from ~/.vibe-deck/config.yaml with sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

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
class VibeDeckConfig:
    """Root configuration for VibeDeck."""

    port: int = DEFAULT_PORT
    render: str = DEFAULT_RENDER  # "sim" or "hardware"
    device_index: int = 0
    autodetect: bool = True
    agent_patterns: list[AgentPattern] = field(default_factory=list)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "VibeDeckConfig":
        return cls(
            port=d.get("port", DEFAULT_PORT),
            render=d.get("render", DEFAULT_RENDER),
            device_index=d.get("device_index", 0),
            autodetect=d.get("autodetect", True),
            agent_patterns=[
                AgentPattern.from_dict(p) for p in d.get("agent_patterns", [])
            ],
            mcp_servers=[
                MCPServerConfig.from_dict(s) for s in d.get("mcp_servers", [])
            ],
        )


def ensure_dirs() -> None:
    """Create all VibeDeck directories if they don't exist."""
    for d in [VIBEDECK_HOME, AGENTS_DIR, LAYOUTS_DIR, ADAPTERS_DIR, SKILLS_DIR, TEMPLATES_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def _default_config() -> VibeDeckConfig:
    """Build the default configuration with built-in agent patterns."""
    return VibeDeckConfig(
        agent_patterns=[
            AgentPattern(name="claude-code", process="claude"),
            AgentPattern(name="claude-code", process="claude.exe"),
            AgentPattern(name="opencode", process="opencode", args_contains=["serve"]),
            AgentPattern(name="openclaw", process="openclaw"),
        ]
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
        "port": config.port,
        "render": config.render,
        "device_index": config.device_index,
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
    }

    with open(config_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(d, f, default_flow_style=False, allow_unicode=True, indent=2)
