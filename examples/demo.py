#!/usr/bin/env python3
"""
VibeDeck Demo — Quick showcase of Stream Deck capabilities.

Requirements:
    pip install vibe-deck[deck]

Usage:
    python examples/demo.py

What it does:
    1. Detects your Stream Deck
    2. Shows device info
    3. Lights up keys with a rainbow
    4. Reacts to key presses (green = pressed, resets on release)
"""

import sys
import time

try:
    from vibe_deck.core import DeckController, NoDeviceFound
except ImportError:
    sys.path.insert(0, "..")
    from vibe_deck.core import DeckController, NoDeviceFound


def rainbow() -> list[str]:
    """Generate a cheerful colour palette."""
    return [
        "#FF0000", "#FF7F00", "#FFFF00", "#00FF00",
        "#0000FF", "#4B0082", "#8B00FF", "#FF1493",
        "#00FFFF", "#FF69B4", "#7FFF00", "#FFD700",
    ]


def main():
    print("🦞 VibeDeck Demo")
    print("=" * 40)

    try:
        with DeckController() as deck:
            # Device info
            print(f"\n📟 Device:  {deck.deck_type}")
            print(f"   Keys:    {deck.key_count}")
            print(f"   Serial:  {deck.serial}")
            print(f"   Firmwar: {deck.firmware}")

            deck.set_brightness(80)
            deck.clear_all()

            # Rainbow light-up
            print("\n🎨 Lighting up keys...")
            colors = rainbow()
            for i in range(deck.key_count):
                deck.set_key_color(i, colors[i % len(colors)])
                time.sleep(0.03)

            time.sleep(1)

            print("💡 Press keys to see them light up!")
            print("   Ctrl+C to exit.\n")

            for event in deck.listen():
                if event.pressed:
                    deck.set_key_color(event.key, "#00FF00")  # bright green
                    print(f"   Key {event.key:2d} pressed")
                else:
                    deck.set_key_color(event.key, "black")
                    print(f"   Key {event.key:2d} released")

    except NoDeviceFound as e:
        print(f"\n❌ {e}")
        print("\nTroubleshooting:")
        print("  1. Is your Stream Deck plugged in?")
        print("  2. udev rules: sudo cp /usr/local/share/streamdeck/udev.rules /etc/udev/rules.d/")
        print("  3. Or just: pip install streamdeck && python -c \"from StreamDeck.DeviceManager import DeviceManager; print(DeviceManager().enumerate())\"")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
