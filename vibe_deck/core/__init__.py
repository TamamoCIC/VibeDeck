"""
VibeDeck Core — the central nervous system.

The Core Daemon owns the asyncio event loop, message bus, layout engine,
and Web API. It starts/stops Connectors and pushes LayoutFrame snapshots
to Render targets.
"""
