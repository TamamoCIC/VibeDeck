"""
VibeDeck MCP Server — exposes VibeDeck state via Model Context Protocol.

Uses stdio transport so external AI Agents (Claude Code, Codex, etc.)
can call these tools and read these resources.

Tools:
  - vibedeck.list_agents()     → list all monitored agents
  - vibedeck.get_widget(id)    → full Widget state
  - vibedeck.list_widgets()    → all Widgets on current layout
  - vibedeck.get_deck_info()   → connected device info

Resources:
  - vibedeck://layout/current  → current LayoutFrame as JSON
  - vibedeck://agents/<id>/status → single agent status
"""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

log = logging.getLogger("vibe_deck.mcp.server")


def _get_mock_state() -> dict[str, Any]:
    """Return mock state when daemon is not running."""
    return {
        "version": "0.1.0",
        "agents": [
            {"id": "claude-code-demo", "type": "agent", "icon": "🐙", "label": "Demo", "status": "running"},
            {"id": "opencode-demo", "type": "agent", "icon": "🦊", "label": "Demo", "status": "idle"},
        ],
        "deck": {"type": "Stream Deck XL", "rows": 4, "cols": 8, "key_count": 32, "connected": False},
    }


async def run_mcp_server() -> None:
    """
    Start the MCP server on stdio.

    Uses the official MCP Python SDK if available, otherwise falls
    back to a lightweight stdio JSON-RPC implementation.
    """
    try:
        from mcp.server import Server
        from mcp.server.stdio import stdio_server
        await _run_mcp_sdk(Server, stdio_server)
    except ImportError:
        log.warning("mcp SDK not installed, using lightweight fallback")
        log.warning("Install: pip install mcp")
        await _run_mcp_fallback()


# ── SDK‑based implementation ────────────────────


async def _run_mcp_sdk(Server, stdio_server) -> None:
    """Full MCP server using the official SDK."""
    server = Server("vibe-deck")

    @server.list_tools()
    async def list_tools() -> list:
        return [
            {
                "name": "vibedeck.list_agents",
                "description": "List all agents currently monitored by VibeDeck.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "vibedeck.get_widget",
                "description": "Get the full state of a single Widget.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "widget_id": {"type": "string", "description": "Widget ID to fetch"},
                    },
                    "required": ["widget_id"],
                },
            },
            {
                "name": "vibedeck.list_widgets",
                "description": "List all Widgets on the current Deck layout.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
            {
                "name": "vibedeck.get_deck_info",
                "description": "Get information about the connected Stream Deck device.",
                "inputSchema": {
                    "type": "object",
                    "properties": {},
                },
            },
        ]

    @server.list_resources()
    async def list_resources() -> list:
        return [
            {
                "uri": "vibedeck://layout/current",
                "name": "Current Layout",
                "description": "The current Deck layout as a LayoutFrame JSON object.",
                "mimeType": "application/json",
            },
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list:
        state = _get_mock_state()

        if name == "vibedeck.list_agents":
            return [{"type": "text", "text": json.dumps(state["agents"], indent=2)}]
        elif name == "vibedeck.get_widget":
            widget_id = arguments.get("widget_id", "")
            for a in state["agents"]:
                if a["id"] == widget_id:
                    return [{"type": "text", "text": json.dumps(a, indent=2)}]
            return [{"type": "text", "text": json.dumps({"error": f"Widget '{widget_id}' not found"})}]
        elif name == "vibedeck.list_widgets":
            return [{"type": "text", "text": json.dumps(state["agents"], indent=2)}]
        elif name == "vibedeck.get_deck_info":
            return [{"type": "text", "text": json.dumps(state["deck"], indent=2)}]
        return [{"type": "text", "text": f"Unknown tool: {name}"}]

    @server.read_resource()
    async def read_resource(uri: str) -> str:
        state = _get_mock_state()
        if uri == "vibedeck://layout/current":
            return json.dumps({
                "deck_type": state["deck"]["type"],
                "rows": state["deck"]["rows"],
                "cols": state["deck"]["cols"],
                "widgets": {a["id"]: a for a in state["agents"]},
            }, indent=2)
        return json.dumps({"error": f"Unknown resource: {uri}"})

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


# ── Lightweight fallback (no SDK dependency) ────


async def _run_mcp_fallback() -> None:
    """
    Minimal JSON-RPC MCP server over stdio.

    Implements the MCP basic lifecycle (initialize, tools/list,
    tools/call, resources/list, resources/read) without the SDK.
    """
    state = _get_mock_state()

    # Read initialize request
    init_line = sys.stdin.readline()
    if init_line:
        try:
            init_req = json.loads(init_line)
            init_id = init_req.get("id")
            # Send initialize response
            _write_json({
                "jsonrpc": "2.0",
                "id": init_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "vibe-deck", "version": "0.1.0"},
                    "capabilities": {"tools": {}, "resources": {}},
                },
            })
        except json.JSONDecodeError:
            pass

    # Read initialized notification
    sys.stdin.readline()

    # Main loop
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method", "")
            params = req.get("params", {})

            if method == "tools/list":
                _write_json({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"tools": [
                        {"name": "vibedeck.list_agents", "description": "List all monitored agents",
                         "inputSchema": {"type": "object", "properties": {}}},
                        {"name": "vibedeck.get_widget", "description": "Get a single Widget state",
                         "inputSchema": {"type": "object", "properties": {"widget_id": {"type": "string"}},
                                         "required": ["widget_id"]}},
                        {"name": "vibedeck.list_widgets", "description": "List all Widgets",
                         "inputSchema": {"type": "object", "properties": {}}},
                        {"name": "vibedeck.get_deck_info", "description": "Get deck device info",
                         "inputSchema": {"type": "object", "properties": {}}},
                    ]},
                })

            elif method == "tools/call":
                tool_name = params.get("name", "")
                tool_args = params.get("arguments", {})
                result = _handle_tool_call(tool_name, tool_args, state)
                _write_json({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]},
                })

            elif method == "resources/list":
                _write_json({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"resources": [
                        {"uri": "vibedeck://layout/current", "name": "Current Layout",
                         "mimeType": "application/json"},
                    ]},
                })

            elif method == "resources/read":
                uri = params.get("uri", "")
                _write_json({
                    "jsonrpc": "2.0", "id": req_id,
                    "result": {"contents": [
                        {"uri": uri, "mimeType": "application/json",
                         "text": json.dumps({"deck": state["deck"], "agents": state["agents"]}, indent=2)},
                    ]},
                })

        except json.JSONDecodeError:
            pass
        except Exception:
            log.exception("MCP fallback error")


def _handle_tool_call(name: str, args: dict, state: dict) -> Any:
    """Handle a single tool call."""
    if name == "vibedeck.list_agents":
        return state["agents"]
    elif name == "vibedeck.get_widget":
        widget_id = args.get("widget_id", "")
        for a in state["agents"]:
            if a["id"] == widget_id:
                return a
        return {"error": f"Widget '{widget_id}' not found"}
    elif name == "vibedeck.list_widgets":
        return state["agents"]
    elif name == "vibedeck.get_deck_info":
        return state["deck"]
    return {"error": f"Unknown tool: {name}"}


def _write_json(obj: dict) -> None:
    """Write a JSON-RPC message to stdout followed by newline."""
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()
