"""
VibeDeck MCP — Model Context Protocol integration.

VibeDeck speaks MCP as both server and client:

- MCP Server: exposes VibeDeck state (agents, widgets, deck info) to
  external AI Agents via stdio transport. Claude Code, Codex, etc.
  can call vibedeck.list_agents() to query the Deck programmatically.

- MCP Client (future): connects to external MCP servers to ingest
  monitoring data (GPU stats, service health, etc.) and render them
  as System Widgets on the Deck.
"""
