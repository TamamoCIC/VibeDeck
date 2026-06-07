"""
VibeDeck Connectors — bridge external Agent state into the core.

Each connector runs as an independent asyncio task, pushing Widget state
updates into the Core Message Bus via internal queues.
"""
