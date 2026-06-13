#!/usr/bin/env python3
"""
Key press listener — direct Stream Deck hardware test.
Press keys on your Stream Deck to see them light up green.
Ctrl+C to exit.
"""
import sys
import time
from PIL import Image
from StreamDeck.DeviceManager import DeviceManager

def main():
    decks = DeviceManager().enumerate()
    if not decks:
        print("No Stream Deck found!")
        return

    deck = decks[0]
    deck.open()
    deck.reset()

    print(f"Stream Deck {deck.deck_type()} — {deck.key_count()} keys")
    print("Press any key on the Stream Deck! Ctrl+C to exit.\n")

    def on_key(deck, key_index, pressed):
        if pressed:
            img = Image.new("RGB", (96, 96), "#00FF00")  # bright green
            deck.set_key_image(key_index, img.tobytes())
            print(f"  KEY {key_index:2d} ⬇  PRESSED")
        else:
            img = Image.new("RGB", (96, 96), "#000000")  # black
            deck.set_key_image(key_index, img.tobytes())
            print(f"  KEY {key_index:2d} ⬆  released")

    deck.set_key_callback(on_key)

    try:
        while True:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\nDone.")
    finally:
        deck.reset()
        deck.close()

if __name__ == "__main__":
    main()
