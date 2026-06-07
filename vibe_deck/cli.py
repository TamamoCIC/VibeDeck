"""
VibeDeck CLI — command-line entry point.
"""

import argparse
import sys

from . import __version__

def main():
    parser = argparse.ArgumentParser(
        prog="vibe-deck",
        description="VibeDeck — Stream Deck toolkit for Vibe Coding & local AI orchestration",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", help="Sub-commands")

    # info
    info_p = sub.add_parser("info", help="List connected Stream Deck devices")

    # demo
    demo_p = sub.add_parser("demo", help="Run a simple key-test demo")
    demo_p.add_argument(
        "--device", type=int, default=0, help="Device index (default: 0)"
    )

    # listen
    listen_p = sub.add_parser("listen", help="Listen for key events")
    listen_p.add_argument(
        "--device", type=int, default=0, help="Device index (default: 0)"
    )

    args = parser.parse_args()

    if args.command == "info":
        cmd_info()
    elif args.command == "demo":
        cmd_demo(args.device)
    elif args.command == "listen":
        cmd_listen(args.device)
    else:
        parser.print_help()


def cmd_info():
    """Display all connected Stream Decks."""
    from .core import DeckController

    decks = DeckController.discover()
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


def cmd_demo(device_index: int):
    """Simple demo: light up keys in sequence."""
    from .core import DeckController

    try:
        with DeckController(device_index) as deck:
            print(f"Deck: {deck.deck_type} ({deck.key_count} keys)")
            deck.set_brightness(80)

            import time
            colors = ["red", "orange", "yellow", "green", "cyan", "blue", "purple", "white"]

            deck.clear_all()
            for i in range(deck.key_count):
                deck.set_key_color(i, colors[i % len(colors)])
                time.sleep(0.05)

            print("✨ Keys lit! Press Ctrl+C to exit.")
            for event in deck.listen():
                color = "lime" if event.pressed else "black"
                deck.set_key_color(event.key, color)

    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


def cmd_listen(device_index: int):
    """Listen for key events and print them."""
    from .core import DeckController

    try:
        with DeckController(device_index) as deck:
            print(f"Listening on {deck.deck_type}... (Ctrl+C to exit)")
            for event in deck.listen():
                action = "🟢 pressed" if event.pressed else "🔴 released"
                print(f"  Key {event.key:2d} {action}")
    except Exception as e:
        print(f"❌ {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
