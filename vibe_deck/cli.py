"""
VibeDeck CLI — command-line entry point.

Full CLI surface for developers and AI agent users.
Every subcommand has --help. Use --json for machine-readable output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time

from . import __version__

log = logging.getLogger("vibe_deck.cli")


def main():
    # Force UTF-8 output on Windows terminals
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="vibe-deck",
        description="VibeDeck — Stream Deck toolkit for Vibe Coding & local AI orchestration",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug logging"
    )

    sub = parser.add_subparsers(dest="command", help="Available commands")

    # serve
    _serve_parser(sub)

    # status
    _status_parser(sub)

    # widget
    _widget_parser(sub)

    # layout
    _layout_parser(sub)

    # adapter
    _adapter_parser(sub)

    # config
    _config_parser(sub)

    # info
    _info_parser(sub)

    # mcp
    _mcp_parser(sub)

    # skill
    _skill_parser(sub)

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO,
                            format="%(message)s")

    if args.command is None:
        parser.print_help()
        return

    # Dispatch
    cmd_map = {
        "serve": cmd_serve,
        "status": cmd_status,
        "widget": cmd_widget,
        "layout": cmd_layout,
        "adapter": cmd_adapter,
        "config": cmd_config,
        "info": cmd_info,
        "mcp": cmd_mcp,
        "skill": cmd_skill,
    }
    handler = cmd_map.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


# ── Parser builders ──────────────────────────────


def _serve_parser(sub):
    p = sub.add_parser("serve", help="Start the VibeDeck daemon")
    p.add_argument("--port", type=int, default=9734, help="HTTP server port (default: 9734)")
    p.add_argument("--render", choices=["sim", "hardware"], default="sim",
                   help="Render target: sim (browser) or hardware (real Deck)")
    p.add_argument("--device", type=int, default=0, help="Stream Deck device index (--render hardware)")
    p.add_argument("--no-autodetect", action="store_true", help="Disable agent auto-discovery")


def _status_parser(sub):
    p = sub.add_parser("status", help="Show agent and deck status (terminal dashboard)")
    p.add_argument("--watch", action="store_true", help="Live-updating dashboard")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")


def _widget_parser(sub):
    p = sub.add_parser("widget", help="Manage Widgets")
    wp = p.add_subparsers(dest="widget_command")
    # widget list
    wl = wp.add_parser("list", help="List all active Widgets")
    wl.add_argument("--json", action="store_true")
    # widget add
    wa = wp.add_parser("add", help="Add a Widget manually")
    wa.add_argument("--type", choices=["agent", "system", "command", "approval"], required=True)
    wa.add_argument("--name", required=True, help="Widget display name")
    wa.add_argument("--key", type=int, help="Key index to place it on")


def _layout_parser(sub):
    p = sub.add_parser("layout", help="Manage deck layouts")
    lp = p.add_subparsers(dest="layout_command")
    # layout list
    lp.add_parser("list", help="List available layouts")
    # layout load
    ll = lp.add_parser("load", help="Load a layout")
    ll.add_argument("name", help="Layout name (from ~/.vibe-deck/layouts/)")
    # layout save
    ls = lp.add_parser("save", help="Save current layout")
    ls.add_argument("--name", required=True, help="Layout name")


def _adapter_parser(sub):
    p = sub.add_parser("adapter", help="Manage adapters")
    ap = p.add_subparsers(dest="adapter_command")
    # adapter list
    al = ap.add_parser("list", help="List installed adapters")
    al.add_argument("--json", action="store_true")
    al.add_argument("--builtin", action="store_true", help="Only built-in adapters")
    al.add_argument("--community", action="store_true", help="Only community adapters")
    # adapter install
    ai = ap.add_parser("install", help="Install a community adapter")
    ai.add_argument("source", help="Path or URL to adapter")


def _config_parser(sub):
    c = sub.add_parser("config", help="View configuration")
    c.add_argument("--path", action="store_true", help="Show config file path")


def _info_parser(sub):
    sub.add_parser("info", help="Show connected Stream Deck device info")


def _mcp_parser(sub):
    p = sub.add_parser("mcp", help="MCP server management")
    mp = p.add_subparsers(dest="mcp_command")
    # mcp serve
    ms = mp.add_parser("serve", help="Start MCP server (stdio transport)")
    ms.add_argument("--stdio", action="store_true", default=True, help="Use stdio transport")


def _skill_parser(sub):
    p = sub.add_parser("skill", help="VibeDeck Skill management")
    sp = p.add_subparsers(dest="skill_command")
    # skill list
    sp.add_parser("list", help="List installed skills")
    # skill install
    si = sp.add_parser("install", help="Install a skill")
    si.add_argument("source", help="Skill name, path, or URL")
    # skill remove
    sr = sp.add_parser("remove", help="Remove a skill")
    sr.add_argument("name", help="Skill name")


# ── Command handlers ─────────────────────────────


def cmd_serve(args):
    """Start the VibeDeck daemon."""
    from .config import load_config
    from .core.layout import LayoutEngine
    from .core.message_bus import MessageBus
    from .web.server import VibeDeckWebServer

    config = load_config()
    config.port = args.port
    config.render = args.render
    if args.no_autodetect:
        config.autodetect = False

    print(f"🦞 VibeDeck {__version__}")
    print(f"   Render: {config.render}")
    print(f"   Port:   {config.port}")
    print(f"   Ctrl+C to stop\n")

    bus = MessageBus()
    engine = LayoutEngine()
    server = VibeDeckWebServer(engine, port=config.port)

    async def _run():
        await server.start()

        # Keep running until SIGTERM / SIGINT
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        finally:
            await server.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        print("\n👋 VibeDeck stopped.")


def cmd_status(args):
    """Show terminal dashboard of agent and deck state."""
    if args.watch:
        _cmd_status_watch()
        return

    status_data = _get_status_data()

    if args.json:
        print(json.dumps(status_data, indent=2))
        return

    # Pretty-print
    print("🦞 VibeDeck Status")
    print(f"   Time: {time.strftime('%H:%M:%S')}")
    print()

    if not status_data.get("agents"):
        print("   No agents detected. Start a supported agent (Claude Code, OpenCode, etc.)")
        print()
        return

    print(f"{'AGENT':<16} {'STATUS':<14} {'ICON':<6} INFO")
    print("-" * 60)
    for agent in status_data["agents"]:
        print(f" {agent['name']:<15} {agent['status']:<14} {agent.get('icon',''):<6} {agent.get('info','')}")
    print()


def _cmd_status_watch():
    """Live-updating terminal dashboard (simple ANSI refresh)."""
    try:
        while True:
            # Clear screen and move cursor to top
            sys.stdout.write("\033[2J\033[H")
            cmd_status(argparse.Namespace(json=False, watch=False))
            sys.stdout.write(" [Live — Ctrl+C to exit]")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        sys.stdout.write("\n👋\n")


def _get_status_data() -> dict:
    """Collect status from the daemon (or local fallback for testing)."""
    # In production: connect to daemon's Web API.
    # For Issue #1 scaffold, return mock data.
    return {
        "version": __version__,
        "agents": [],  # populated by connectors once daemon is running
        "deck": {"connected": False, "type": "none"},
    }


def cmd_widget(args):
    """Widget CRUD."""
    if args.widget_command == "list":
        print("No active widgets. Start the daemon first (vibe-deck serve).")
    elif args.widget_command == "add":
        print(f"Adding widget: type={args.type} name={args.name}")
    else:
        print("Usage: vibe-deck widget {list|add}")


def cmd_layout(args):
    """Layout management."""
    if args.layout_command == "list":
        from .config import LAYOUTS_DIR
        layouts = sorted(LAYOUTS_DIR.glob("*.yaml")) if LAYOUTS_DIR.exists() else []
        if layouts:
            for l in layouts:
                print(f"  📄 {l.stem}")
        else:
            print("No saved layouts. Create one from the Web Editor.")
    elif args.layout_command == "load":
        print(f"Loading layout: {args.name}")
    elif args.layout_command == "save":
        print(f"Saving layout: {args.name}")
    else:
        print("Usage: vibe-deck layout {list|load|save}")


def cmd_adapter(args):
    """Adapter management."""
    if args.adapter_command == "list":
        builtin = ["claude-code", "opencode", "openclaw", "telegram"]
        if args.community:
            # Scan ~/.vibe-deck/adapters/ for community adapters
            from .config import ADAPTERS_DIR
            community = list(ADAPTERS_DIR.glob("*/adapter.yaml")) if ADAPTERS_DIR.exists() else []
            for c in community:
                print(f"  🧩 {c.parent.name} (community)")
            if not community:
                print("No community adapters installed.")
            return

        if not args.community:
            print("Built-in adapters:")
            for a in builtin:
                print(f"  🦞 {a}")
        print(f"\n  {len(builtin)} built-in | use --community for community adapters")

        if args.json:
            print(json.dumps({"builtin": builtin, "community": []}, indent=2))
    elif args.adapter_command == "install":
        print(f"Installing adapter from: {args.source}")
    else:
        print("Usage: vibe-deck adapter {list|install}")


def cmd_config(args):
    """Show configuration."""
    from .config import CONFIG_FILE, load_config
    if args.path:
        print(str(CONFIG_FILE))
    else:
        config = load_config()
        print(f"Config file: {CONFIG_FILE}")
        print(f"Port:       {config.port}")
        print(f"Render:     {config.render}")
        print(f"Autodetect: {config.autodetect}")
        print(f"Device:     #{config.device_index}")
        if config.agent_patterns:
            print(f"Agent patterns:")
            for ap in config.agent_patterns:
                print(f"  - {ap.name}: {ap.process} (args={ap.args_contains})")
        if config.mcp_servers:
            print(f"MCP servers:")
            for ms in config.mcp_servers:
                print(f"  - {ms.name}: {' '.join(ms.command)}")


def cmd_info(args):
    """Show Stream Deck device info."""
    from .render.hardware import HardwareRenderer
    decks = HardwareRenderer.discover()
    if not decks:
        print("⚠️  No Stream Deck devices detected.")
        print("   Check USB connection and udev rules.")
        print("   Install: pip install vibe-deck[deck]")
        return

    print(f"Found {len(decks)} Stream Deck device(s):\n")
    for d in decks:
        if d.get("error"):
            print(f"  [{d['index']}] <error reading device>")
        else:
            print(f"  [{d['index']}] {d['type']}")
            print(f"         Serial:   {d['serial']}")
            print(f"         Firmware: {d['firmware']}")
            print(f"         Keys:     {d['key_count']}")
            print(f"         USB:      {d['vendor_id']}:{d['product_id']}")
        print()


def cmd_mcp(args):
    """MCP server management."""
    if args.mcp_command == "serve":
        print("MCP server starting on stdio...")
        # In production: start MCP server loop. For scaffold, print stub.
    else:
        print("Usage: vibe-deck mcp serve")


def cmd_skill(args):
    """Skill management."""
    if args.skill_command == "list":
        print("Installed skills: (none yet)")
    elif args.skill_command == "install":
        print(f"Installing skill: {args.source}")
    elif args.skill_command == "remove":
        print(f"Removing skill: {args.name}")
    else:
        print("Usage: vibe-deck skill {list|install|remove}")


if __name__ == "__main__":
    main()
