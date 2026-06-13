# ADR 0001: Multi-Terminal Architecture

## Status

Accepted (2026-06-08)

> **Note:** The terminology in this ADR ("Terminal") has been superseded by
> [ADR 0003](0003-endpoint-renderer-split.md) ("Endpoint" / "Renderer" / "Binding").
> The architectural decisions (multi-surface, per-Endpoint layout) remain valid,
> but the vocabulary has been refined. See ADR 0003 for current terminology.

## Context

VibeDeck was originally designed to drive a single Elgato Stream Deck. The `LayoutEngine` held one `LayoutFrame`, and the render loop pushed that frame to at most two outputs (hardware USB + web simulator) — both showing the same layout.

The phone-as-virtual-terminal feature forces a re-evaluation: a user might want a Stream Deck XL on their desk *and* a phone monitoring display on the other side of the room, each showing a different Widget arrangement. Treating them as mirror images of each other doesn't work.

## Decision

VibeDeck adopts a **multi-terminal architecture**:

1. **Terminal** is the canonical abstraction — a display-and-input grid device. Physical Stream Decks and virtual phone/tablet/browser Terminals are peers at the logical layer.
2. The `LayoutEngine` manages **one `LayoutFrame` per connected Terminal**, not a single global frame.
3. The render loop pushes each Terminal's frame independently through the appropriate render adapter (`HardwareRenderer` for physical, `SimRenderer` for virtual).
4. Each Terminal has its own layout file, stored under `~/.vibe-deck/layouts/<terminal-name>.yaml`.

This means the daemon is always a multi-terminal server — even when only one Terminal is connected, the architecture doesn't assume a singleton.

## Considered Options

### Option A: Single-terminal (original design)

Keep one `LayoutFrame` globally. Phone mirrors the same layout as the physical Deck. Simpler code but fails the dashboard use case (different devices, different views).

### Option B: Multi-terminal (chosen)

Each Terminal gets its own layout and render pipeline. Slightly more complex internally, but the abstraction is cleaner — "one daemon, many displays" maps directly to how users think about their setup.

## Consequences

- `LayoutEngine` changes from holding a single `LayoutFrame` to holding a `dict[terminal_id, LayoutFrame]`.
- `MessageBus` messages that reference a key press must carry `terminal_id` so the engine can route to the correct layout.
- SSE frame broadcasts are per-terminal, not global. The Web Server maintains per-terminal subscriber lists.
- `LayoutFrame.for_deck()` is replaced by `LayoutFrame.for_grid(rows, cols, display_name)`, decoupling the grid concept from Stream Deck hardware model names.
- `MessageType.DECK_CONNECTED/DISCONNECTED` are renamed to `TERMINAL_CONNECTED/DISCONNECTED`.
- Physical Terminals are auto-detected on startup; Virtual Terminals register on first connection via a setup wizard.
