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

    _serve_parser(sub)
    _status_parser(sub)
    _demo_parser(sub)
    _widget_parser(sub)
    _layout_parser(sub)
    _adapter_parser(sub)
    _config_parser(sub)
    _info_parser(sub)
    _mcp_parser(sub)
    _skill_parser(sub)

    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command is None:
        parser.print_help()
        return

    cmd_map = {
        "serve": cmd_serve,
        "status": cmd_status,
        "demo": cmd_demo,
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
    p = sub.add_parser("serve", help="Start the VibeDeck daemon",
        description="Start the VibeDeck daemon with Web UI. Use --demo for sample widgets.",
        epilog="Example: vibe-deck serve --demo --port 9734")
    p.add_argument("--port", type=int, default=9734, help="HTTP server port (default: 9734)")
    p.add_argument("--render", choices=["sim", "hardware"], default="sim",
                   help="[DEPRECATED] Render target. Use --no-physical instead")
    p.add_argument("--device", type=int, default=0, help="Stream Deck device index")
    p.add_argument("--no-autodetect", action="store_true", help="Disable agent auto-discovery")
    p.add_argument("--demo", action="store_true", help="Start with sample widgets for development")
    p.add_argument("--expose", action="store_true", help="Bind to 0.0.0.0 (allow LAN connections)")
    p.add_argument("--no-physical", action="store_true", help="Skip Stream Deck hardware detection (virtual-only mode)")


def _status_parser(sub):
    p = sub.add_parser("status", help="Show agent and deck status (terminal dashboard)",
        epilog="Example: vibe-deck status --watch")
    p.add_argument("--watch", action="store_true", help="Live-updating dashboard")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")


def _demo_parser(sub):
    sub.add_parser("demo", help="Start daemon with demo widgets (alias for serve --demo)",
        description="Start VibeDeck in demo mode with sample widgets. Equivalent to: vibe-deck serve --demo")


def _widget_parser(sub):
    p = sub.add_parser("widget", help="Manage Widgets")
    wp = p.add_subparsers(dest="widget_command")
    wl = wp.add_parser("list", help="List all active Widgets",
        epilog="Example: vibe-deck widget list --json")
    wl.add_argument("--json", action="store_true")
    wa = wp.add_parser("add", help="Add a Widget manually",
        epilog="Example: vibe-deck widget add --type command --name 'Open Terminal' --key 15")
    wa.add_argument("--type", choices=["agent", "system", "command", "approval"], required=True)
    wa.add_argument("--name", required=True, help="Widget display name")
    wa.add_argument("--key", type=int, help="Key index to place it on")


def _layout_parser(sub):
    p = sub.add_parser("layout", help="Manage deck layouts")
    lp = p.add_subparsers(dest="layout_command")
    lp.add_parser("list", help="List available layouts")
    ll = lp.add_parser("load", help="Load a layout",
        epilog="Example: vibe-deck layout load my-layout")
    ll.add_argument("name", help="Layout name (from ~/.vibe-deck/layouts/)")
    ls = lp.add_parser("save", help="Save current layout",
        epilog="Example: vibe-deck layout save --name my-layout")
    ls.add_argument("--name", required=True, help="Layout name")


def _adapter_parser(sub):
    p = sub.add_parser("adapter", help="Manage adapters")
    ap = p.add_subparsers(dest="adapter_command")
    al = ap.add_parser("list", help="List installed adapters",
        epilog="Example: vibe-deck adapter list --json")
    al.add_argument("--json", action="store_true")
    al.add_argument("--builtin", action="store_true", help="Only built-in adapters")
    al.add_argument("--community", action="store_true", help="Only community adapters")
    ai = ap.add_parser("install", help="Install a community adapter",
        epilog="Example: vibe-deck adapter install ./my-adapter")
    ai.add_argument("source", help="Path or URL to adapter")


def _config_parser(sub):
    c = sub.add_parser("config", help="View configuration",
        epilog="Example: vibe-deck config --path")
    c.add_argument("--path", action="store_true", help="Show config file path")


def _info_parser(sub):
    sub.add_parser("info", help="Show connected Terminal device info")


def _mcp_parser(sub):
    p = sub.add_parser("mcp", help="MCP server management")
    mp = p.add_subparsers(dest="mcp_command")
    ms = mp.add_parser("serve", help="Start MCP server (stdio transport for AI agents)",
        epilog="Example: vibe-deck mcp serve")


def _skill_parser(sub):
    p = sub.add_parser("skill", help="VibeDeck Skill management")
    sp = p.add_subparsers(dest="skill_command")
    sp.add_parser("list", help="List installed skills")
    si = sp.add_parser("install", help="Install a skill",
        epilog="Example: vibe-deck skill install claude-code")
    si.add_argument("source", help="Skill name, path, or URL")
    sr = sp.add_parser("remove", help="Remove a skill",
        epilog="Example: vibe-deck skill remove claude-code")
    sr.add_argument("name", help="Skill name")


# ── Command handlers ─────────────────────────────


def cmd_serve(args):
    """Start the VibeDeck daemon."""
    host = "0.0.0.0" if getattr(args, 'expose', False) else "localhost"
    print(f"\n🦞  VibeDeck {__version__}")
    print(f"   Render:   {'virtual-only' if getattr(args, 'no_physical', False) else args.render}")
    print(f"   Web UI:   http://{host}:{args.port}")
    print(f"   Demo:     {'yes' if args.demo else 'no'}")
    print(f"   LAN:      {'yes' if getattr(args, 'expose', False) else 'no (--expose to enable)'}")
    print(f"   Ctrl+C to stop\n")

    from .core.event_loop import run_supervisor

    try:
        asyncio.run(run_supervisor(
            port=args.port,
            render=args.render,
            device_index=args.device,
            autodetect=not args.no_autodetect,
            demo=args.demo,
            expose=getattr(args, 'expose', False),
            no_physical=getattr(args, 'no_physical', False),
        ))
    except KeyboardInterrupt:
        print("\n👋 VibeDeck stopped.")


def cmd_demo(args):
    """Start daemon in demo mode."""
    ns = argparse.Namespace(
        port=9734, render="sim", device=0,
        no_autodetect=True, demo=True,
        expose=False, no_physical=True,
    )
    cmd_serve(ns)


def cmd_status(args):
    """Show terminal dashboard of agent and deck state."""
    if args.watch:
        _cmd_status_watch(args)
        return

    # Try to get live data from daemon, fall back to mock
    status_data = _get_status_data()

    if getattr(args, 'json', False):
        print(json.dumps(status_data, indent=2))
        return

    print("\n🦞  VibeDeck Status")
    print(f"   Time: {time.strftime('%H:%M:%S')}")
    print()

    agents = status_data.get("agents", [])
    if not agents:
        print("   No agents detected.")
        print("   Start a supported agent or run: vibe-deck demo")
        print()
        return

    # Table
    print(f" {'AGENT':<16} {'STATUS':<14} {'ICON':<6} {'INFO':<20}")
    print("-" * 60)
    for a in agents:
        icon = a.get('icon', '')
        print(f" {a['name']:<15} {a['status']:<14} {icon:<6} {a.get('info', ''):<20}")
    print()


def _cmd_status_watch(args):
    """Live-updating terminal dashboard."""
    try:
        while True:
            sys.stdout.write("\033[2J\033[H")  # Clear screen
            # Use a non-json namespace for display
            ns = argparse.Namespace(json=False, watch=False)
            cmd_status(ns)
            sys.stdout.write(" [Live — updating every 2s — Ctrl+C to exit]\n")
            sys.stdout.flush()
            time.sleep(2)
    except KeyboardInterrupt:
        sys.stdout.write("\n👋\n")


def _get_status_data() -> dict:
    """Collect status data."""
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:9734/api/frame")
        data = json.loads(resp.read())
        agents = []
        for k in data.get("keys", []):
            if k.get("widget_id"):
                agents.append({
                    "name": k["widget_id"],
                    "status": k.get("label", "unknown"),
                    "icon": k.get("icon", ""),
                    "info": k.get("type", ""),
                })
        return {"version": __version__, "agents": agents}
    except Exception:
        return {"version": __version__, "agents": []}


def cmd_widget(args):
    """Widget CRUD."""
    if args.widget_command == "list":
        status = _get_status_data()
        agents = status.get("agents", [])
        if args.json:
            print(json.dumps(agents, indent=2))
        elif agents:
            for a in agents:
                print(f"  {a['icon']} {a['name']} [{a['status']}] ({a['info']})")
        else:
            print("No active widgets. Start the daemon first (vibe-deck serve).")
    elif args.widget_command == "add":
        print(f"Adding widget: type={args.type} name={args.name} key={args.key}")
    else:
        print("Usage: vibe-deck widget {list|add}")


def cmd_layout(args):
    """Layout management."""
    from .config import LAYOUTS_DIR

    if args.layout_command == "list":
        layouts = sorted(LAYOUTS_DIR.glob("*.yaml")) if LAYOUTS_DIR.exists() else []
        if layouts:
            print(f"Layouts in {LAYOUTS_DIR}:")
            for l in layouts:
                print(f"  📄 {l.stem}")
        else:
            print(f"No saved layouts in {LAYOUTS_DIR}")
            print("Create one from the Web Editor or vibe-deck layout save.")
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
        from .config import ADAPTERS_DIR

        if args.community:
            community = sorted(ADAPTERS_DIR.glob("*/adapter.yaml")) if ADAPTERS_DIR.exists() else []
            if community:
                for c in community:
                    print(f"  🧩 {c.parent.name} (community)")
            else:
                print("No community adapters installed.")
                print(f"Install to: {ADAPTERS_DIR}/<name>/adapter.yaml")
            return

        print("Built-in adapters:")
        for a in builtin:
            print(f"  🦞 {a}")

        if args.json:
            print(json.dumps({"builtin": builtin, "community": []}, indent=2))
    elif args.adapter_command == "install":
        print(f"Installing adapter from: {args.source}")
        from .config import ADAPTERS_DIR
        print(f"Target: {ADAPTERS_DIR}/")
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
        print(f"Port:        {config.port}")
        print(f"Render:      {config.render}")
        print(f"Autodetect:  {config.autodetect}")
        print(f"Device:      #{config.device_index}")
        if config.agent_patterns:
            print("Agent patterns:")
            for ap in config.agent_patterns:
                print(f"  - {ap.name}: {ap.process}")
        else:
            print("Agent patterns: (none — add to ~/.vibe-deck/config.yaml)")
        if config.mcp_servers:
            print("MCP servers:")
            for ms in config.mcp_servers:
                print(f"  - {ms.name}: {' '.join(ms.command)}")
        if config.terminals:
            print("Terminals:")
            for t in config.terminals:
                print(f"  - {t.name} [{t.type}] grid={t.grid} token={t.token[:8]}...")


def cmd_info(args):
    """Show Stream Deck device info."""
    from .render.hardware import HardwareRenderer
    decks = HardwareRenderer.discover()
    if not decks:
        print("⚠️  No Stream Deck devices detected.")
        print("   Check USB connection and udev rules: sudo cp rules /etc/udev/rules.d/")
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
        print("🦞 VibeDeck MCP Server starting on stdio...")
        from .mcp.server import run_mcp_server
        try:
            asyncio.run(run_mcp_server())
        except KeyboardInterrupt:
            print("\n👋 MCP server stopped.")
    else:
        print("Usage: vibe-deck mcp serve")


def cmd_skill(args):
    """Skill management."""
    from .config import SKILLS_DIR
    if args.skill_command == "list":
        skills = sorted(SKILLS_DIR.glob("*/skill.yaml")) if SKILLS_DIR.exists() else []
        if skills:
            for s in skills:
                print(f"  🎯 {s.parent.name}")
        else:
            print("No skills installed.")
            print("Install: vibe-deck skill install <name>")
    elif args.skill_command == "install":
        print(f"Installing skill: {args.source}")
        SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Target: {SKILLS_DIR}/")
    elif args.skill_command == "remove":
        print(f"Removing skill: {args.name}")
    else:
        print("Usage: vibe-deck skill {list|install|remove}")


if __name__ == "__main__":
    main()
