"""
VibeDeck Render Engine — drives physical and virtual Stream Deck displays.

Consumes LayoutFrame snapshots from the Core and renders them as images
on the target device. Two targets: Web Simulator (Pillow → PNG frames)
and Hardware (real Elgato Stream Deck).
"""
