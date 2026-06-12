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
import socket
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
    _whoami_parser(sub)
    _widget_parser(sub)
    _layout_parser(sub)
    _adapter_parser(sub)
    _config_parser(sub)
    _info_parser(sub)
    _mcp_parser(sub)
    _skill_parser(sub)
    _setup_parser(sub)

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
        "whoami": cmd_whoami,
        "widget": cmd_widget,
        "layout": cmd_layout,
        "adapter": cmd_adapter,
        "config": cmd_config,
        "info": cmd_info,
        "mcp": cmd_mcp,
        "skill": cmd_skill,
        "setup": cmd_setup,
    }
    handler = cmd_map.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()


# ── Parser builders ──────────────────────────────


def _setup_parser(sub):
    p = sub.add_parser("setup", help="Set up agent integrations",
        description="Set up VibeDeck integrations with AI agents.",
        epilog="Example: vibe-deck setup claude-code")
    sp = p.add_subparsers(dest="setup_target")
    sc = sp.add_parser("claude-code", help="Install Claude Code hooks for VibeDeck",
        epilog="Example: vibe-deck setup claude-code")
    sc.add_argument("--reporter-path", help="Custom path for the hook reporter script")
    sc.add_argument("--no-handshake", action="store_true",
                    help="Skip the agent handshake message")


def _serve_parser(sub):
    p = sub.add_parser("serve", help="Start the VibeDeck daemon",
        description="Start the VibeDeck daemon with Web UI.",
        epilog="Example: vibe-deck serve --port 9734")
    p.add_argument("--port", type=int, default=9734, help="HTTP server port (default: 9734)")
    p.add_argument("--render", choices=["sim", "hardware"], default="sim",
                   help="[DEPRECATED] Render target. Use --no-physical instead")
    p.add_argument("--device", type=int, default=0, help="Stream Deck device index")
    p.add_argument("--no-autodetect", action="store_true", help="Disable agent auto-discovery")
    p.add_argument("--expose", action="store_true", help="Bind to 0.0.0.0 (allow LAN connections)")
    p.add_argument("--no-physical", action="store_true", help="Skip Stream Deck hardware detection (virtual-only mode)")


def _status_parser(sub):
    p = sub.add_parser("status", help="Show agent and deck status (terminal dashboard)",
        epilog="Example: vibe-deck status --watch")
    p.add_argument("--watch", action="store_true", help="Live-updating dashboard")
    p.add_argument("--json", action="store_true", help="Machine-readable JSON output")


def _whoami_parser(sub):
    p = sub.add_parser("whoami", help="Identify this agent instance and show its VibeDeck status",
        description="Walk up the process tree to find a Claude Code ancestor, then query the VibeDeck daemon for the matching widget. Use this inside Claude Code hooks or from the same terminal session.",
        epilog="Examples:\n  vibe-deck whoami\n  vibe-deck whoami --json\n  vibe-deck whoami --pid 12345")
    p.add_argument("--pid", type=int, default=0,
                   help="Explicit PID (auto-detected from process tree if omitted)")
    p.add_argument("--json", action="store_true",
                   help="Machine-readable JSON output")
    p.add_argument("--port", type=int, default=9734,
                   help="Daemon port (default: 9734)")


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


def _get_lan_ip() -> str:
    """Detect the primary LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _print_qr(url: str) -> None:
    """Print an ASCII QR code to the terminal."""
    try:
        import qrcode
        qr = qrcode.QRCode(border=1)
        qr.add_data(url)
        qr.make(fit=True)
        qr.print_ascii()
    except ImportError:
        print(f"   🔗 Connect: {url}")


def cmd_serve(args):
    """Start the VibeDeck daemon."""
    expose = getattr(args, 'expose', False)
    no_physical = getattr(args, 'no_physical', False)
    host = "0.0.0.0" if expose else "localhost"
    print(f"\n🦞  VibeDeck {__version__}")
    print(f"   Mode:     live (agent detection active)")
    print(f"   Render:   {'virtual-only' if no_physical else args.render}")
    print(f"   Web UI:   http://{host}:{args.port}")
    print(f"   LAN:      {'yes' if expose else 'no (--expose to enable)'}")
    print(f"   Ctrl+C to stop\n")

    if expose:
        from .config import load_config
        config = load_config()
        if config.terminals:
            token = config.terminals[0].token
            lan_ip = _get_lan_ip()
            url = f"http://{lan_ip}:{args.port}/?token={token}"
            print("   📱 Scan to connect:\n")
            _print_qr(url)
            print(f"\n   {url}\n")

    from .core.event_loop import run_supervisor

    try:
        asyncio.run(run_supervisor(
            port=args.port,
            render=args.render,
            device_index=args.device,
            autodetect=not args.no_autodetect,
            expose=getattr(args, 'expose', False),
            no_physical=getattr(args, 'no_physical', False),
        ))
    except RuntimeError as e:
        # Port-in-use → try to shutdown the old daemon and restart
        msg = str(e)
        if "already in use" in msg:
            import urllib.request
            print(f"⚠ Port {args.port} in use — asking old daemon to shut down...")
            try:
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"http://localhost:{args.port}/api/shutdown",
                        method="POST",
                    ),
                    timeout=3,
                )
                print("   Old daemon stopped. Waiting 2s for port release...")
                import time
                time.sleep(2)
                # Retry once
                asyncio.run(run_supervisor(
                    port=args.port,
                    render=args.render,
                    device_index=args.device,
                    autodetect=not args.no_autodetect,
                    expose=getattr(args, 'expose', False),
                    no_physical=getattr(args, 'no_physical', False),
                ))
                return
            except Exception:
                pass
        print(f"\n❌ {msg}")
        import sys
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 VibeDeck stopped.")


def cmd_status(args):
    """Show terminal dashboard: terminals, agents, and connection info."""
    if args.watch:
        _cmd_status_watch(args)
        return

    from .config import load_config

    config = load_config()
    agents_data = _get_status_data()

    if getattr(args, 'json', False):
        output = {
            "version": __version__,
            "terminals": [t.to_dict() for t in config.terminals],
            "agents": agents_data.get("agents", []),
        }
        print(json.dumps(output, indent=2))
        return

    print("\n🦞  VibeDeck Status")
    print(f"   Time: {time.strftime('%H:%M:%S')}")
    print()

    # Terminals
    terminals = config.terminals
    print(f" Terminals ({len(terminals)} registered):")
    if terminals:
        print(f" {'NAME':<16} {'TYPE':<10} {'GRID':<8} {'TOKEN':<20}")
        print("-" * 56)
        for t in terminals:
            token_preview = t.token[:12] + "..." if len(t.token) > 12 else t.token
            print(f" {t.name:<15} {t.type:<10} {t.grid:<8} {token_preview:<20}")
    else:
        print("   (none)")
    print()

    # Agents
    agents = agents_data.get("agents", [])
    print(f" Agents ({len(agents)} active):")
    if agents:
        print(f" {'NAME':<20} {'ICON':<6} {'STATUS':<14}")
        print("-" * 42)
        for a in agents:
            icon = a.get('icon', '')
            print(f" {a['name']:<19} {icon:<6} {a['status']:<14}")
    else:
        print("   No agents detected.")
        print("   Start a supported agent (Claude Code, OpenCode, etc.)")
    print()

    # QR Code for reconnection (uses current LAN IP)
    if terminals:
        lan_ip = _get_lan_ip()
        if lan_ip != "127.0.0.1":
            port = config.port
            url = f"http://{lan_ip}:{port}/?token={terminals[0].token}"
            print(" 📱 Scan to connect:\n")
            _print_qr(url)
            print(f"\n   {url}\n")


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


def _find_agent_ancestor_pid() -> int:
    """Walk up the process tree looking for a known agent process.

    Delegates to :func:`vibe_deck.platform.find_agent_ancestor_pid`.
    """
    from vibe_deck.platform import find_agent_ancestor_pid
    return find_agent_ancestor_pid()


def cmd_whoami(args):
    """Identify this agent instance and show its VibeDeck widget state."""
    pid = args.pid or _find_agent_ancestor_pid()

    try:
        import urllib.request
        url = f"http://localhost:{args.port}/api/widgets?pid={pid}"
        resp = urllib.request.urlopen(url, timeout=3)
        data = json.loads(resp.read())
    except Exception as e:
        if getattr(args, 'json', False):
            print(json.dumps({"found": False, "error": str(e)}))
        else:
            print(f"⚠️  Could not reach VibeDeck daemon on port {args.port}")
            print(f"   Error: {e}")
            print(f"   Is the daemon running?  vibe-deck serve")
        return

    if args.json:
        data["_query_pid"] = pid
        print(json.dumps(data, indent=2))
        return

    if not data.get("found"):
        print(f"🔍 No widget found for PID {pid}")
        print(f"   Is the agent process running?")
        print(f"   Detected PID: {pid}")
        return

    widget_id = data["widget_id"]
    display = data["display"]
    meta = data["meta"]
    terminal = data["terminal_id"]
    key_index = data.get("key_index")

    print()
    print("🦞  VibeDeck — Who Am I?")
    print(f"   PID:         {pid}")
    print(f"   Widget ID:   {widget_id}")
    print(f"   Terminal:    {terminal}" + (f" (key {key_index})" if key_index is not None else ""))
    print(f"   Agent:       {meta.get('agent', 'unknown')}")
    session_id = meta.get("session_id", "")
    if session_id:
        print(f"   Session:     {session_id[:16]}...")
    print()
    print(f"   Display:     {display.get('icon', '')} {display.get('label', '')}")
    print(f"   Color:       {display.get('color', '')}")
    print(f"   Animation:   {display.get('animation', '')}")
    print(f"   Status:      {meta.get('status', 'unknown')}")
    print()


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


def cmd_setup(args):
    """Set up agent integrations."""
    if args.setup_target == "claude-code":
        _cmd_setup_claude_code(args)
    else:
        print("Usage: vibe-deck setup claude-code")


def _cmd_setup_claude_code(args):
    """Install Claude Code hook reporter and generate hooks config."""
    import shutil
    from pathlib import Path
    from .config import VIBEDECK_HOME

    reporters_dir = VIBEDECK_HOME / "reporters" / "claude-code"
    reporters_dir.mkdir(parents=True, exist_ok=True)

    # 1. Copy reporter.py to ~/.vibe-deck/reporters/claude-code/
    src = Path(__file__).parent / "adapters" / "claude_code" / "reporter.py"
    dst = reporters_dir / "reporter.py"
    reporter_path = args.reporter_path or str(dst.resolve())

    if src.exists():
        shutil.copy2(src, dst)
        print(f"📋 Reporter installed: {dst}")
    else:
        print(f"⚠️  Reporter source not found: {src}")
        print("   Skipping reporter copy — existing installation will be used if present.")

    # 2. Generate hooks.json from template
    template_path = Path(__file__).parent / "adapters" / "claude_code" / "hooks.template.json"
    hooks_dst = reporters_dir / "hooks.json"

    if template_path.exists():
        import json as _json
        template = _json.loads(template_path.read_text(encoding="utf-8"))
        raw = _json.dumps(template, indent=2, ensure_ascii=False)
        # Replace placeholder with actual path
        raw = raw.replace("{{REPORTER_PATH}}", str(Path(reporter_path).resolve()).replace("\\", "/"))
        hooks_dst.write_text(raw, encoding="utf-8")
        print(f"🔧 Hooks config:   {hooks_dst}")
    else:
        print(f"⚠️  Template not found: {template_path}")

    print()

    # 3. Handshake message
    if not getattr(args, "no_handshake", False):
        print("🤝 Agent Handshake")
        print("─" * 50)
        print()
        print("   Tell your Claude Code session:")
        print()
        print(f'   "Merge the hooks from {hooks_dst}')
        print(f'    into ~/.claude/settings.json')
        print(f'    so VibeDeck can monitor this session."')
        print()
        print("   Or add this to your CLI invocation:")
        print(f"   claude --settings {hooks_dst}")
        print()
        print("─" * 50)
        print("✅ Setup complete. VibeDeck will now see Claude Code hook events.")
    else:
        print("✅ Setup complete (handshake suppressed).")


if __name__ == "__main__":
    main()
