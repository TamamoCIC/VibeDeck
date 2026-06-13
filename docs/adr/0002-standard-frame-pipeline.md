# ADR 0002: Standard Frame Rendering Pipeline

## Status

Accepted (2026-06-13)

## Context

The rendering pipeline had two separate, duplicated code paths — `HardwareRenderer` for physical Stream Decks and `SimRenderer` for web-based virtual terminals. Each implemented its own icon drawing, label rendering, badge compositing, and animation handling independently, leading to:

- **Divergent visual output**: Hardware and web showed different icons (geometric shapes vs colored squares) and different label styling (shadow vs black bar).
- **Format coupling**: `HardwareRenderer` mixed JPEG encoding + flip + HID transport into the rendering loop. `SimRenderer` mixed PNG base64 encoding + SSE broadcast.
- **No intermediate representation**: There was no device-independent frame — every output was rendered straight to its final transport format, making it impossible to add new output types (e.g., saving frames as video, streaming to a different protocol) without adding yet another renderer.
- **Hard to extend**: Adding a new visual effect or icon shape required changes in both renderers.

## Decision

VibeDeck adopts a **Standard Frame rendering pipeline** that separates rendering into three independent stages:

### 1. Layer Compositing (PIL Renderer)

Each key image is built from a stack of independent `Layer` objects, composed in z-order:

```
Backdrop → Sprite → Icon → Label → Badge → Effect → final
```

Each Layer:
- Receives `DisplayState`, key size, and a timestamp
- Returns an RGBA `PIL.Image` or `None` (if inactive)
- Has zero knowledge of other layers

Layers are declared per-adapter in `adapter.yaml`, allowing each Agent type to ship its own visual scheme. A web-based Layer Editor lets designers and artists compose layer stacks without writing code.

### 2. Standard Frame (device-independent intermediate)

A `StandardFrame` is a fully-rendered snapshot of a Terminal at one moment:

```python
StandardFrame
├── grid: (rows, cols)
├── key_size: (w, h)
└── keys: list[KeyImage]
    ├── index: int
    ├── widget_id: str | None
    ├── jpeg: bytes    # for HID / embedded devices
    └── png: bytes     # for web / high-quality consumers
```

Key properties:
- **Complete frame** — every key, every time. No diff in the StandardFrame itself; downstream Transports decide whether to diff.
- **Dual format** — JPEG (compact, Stream Deck firmware requirement) and PNG (lossless, browser-friendly) produced simultaneously.
- **Fully composited** — label text and badge are already rendered into the final image bytes. Multi-language support re-runs the renderer.
- **Serializable** — `bytes` fields make the frame suitable for cross-process transport, disk storage, or network streaming.

### 3. Transport (output-specific encoding)

Transports consume `StandardFrame` and push it to the target device:

| Transport | Input | Output |
|-----------|-------|--------|
| `HIDTransport` | `jpeg` bytes | USB HID → Stream Deck (after flip) |
| `WebTransport` | `png` bytes | SSE → browser (base64) |

Transports are intentionally thin:
- They do NOT render text, draw shapes, or composite layers.
- They MAY implement their own diffing, throttling, or queuing.
- They do NOT control frame rate — the Supervisor drives the render loop.

### Frame Rate

The Supervisor (event loop) controls rendering cadence — fast (~30 Hz) during activity, slow (~1 Hz) when idle. This is a Supervisor concern, not a Renderer or Transport concern.

## Consequences

### Code changes

- **New**: `vibe_deck/render/layers/` — layer implementations (backdrop, icon, label, badge, effect, sprite)
- **New**: `vibe_deck/render/standard_frame.py` — `StandardFrame` and `KeyImage` types
- **New**: `vibe_deck/render/renderer.py` — `PILRenderer` that composes layers into `StandardFrame`
- **New**: `vibe_deck/transport/` — `HIDTransport`, `WebTransport`
- **Refactored**: `hardware.py` and `sim.py` — reduced to thin Transport wrappers, all rendering logic extracted into layers and renderer
- **Updated**: `adapter.yaml` schema — add `layers` section for per-adapter layer stack declarations

### Design principles established

1. **Separation of concerns**: Rendering ≠ Encoding ≠ Transport
2. **Device independence**: StandardFrame is the universal currency — any device that can consume JPEG or PNG can be a Terminal
3. **Extensibility by non-programmers**: New visual styles come from composing existing layers in YAML + a visual editor — no Python code required
4. **No Transport intelligence**: Text rendering, diff logic, and format conversion stay in the Renderer; Transports just push bytes

### StandardFrame as middleware

Because StandardFrame is serializable `bytes`, it opens future use cases:
- Recording sessions as frame streams for playback
- Streaming frames over WebSocket to remote displays
- Caching / pre-rendering frames for known states
- Third-party tools consuming the frame stream without depending on VibeDeck internals
