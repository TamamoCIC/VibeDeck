# ADR 0003: Endpoint / Renderer Split

## Status

Accepted (2026-06-13)

## Context

ADR 0001 introduced the **Terminal** abstraction — "a display-and-input grid device." Physical Stream Decks and virtual phone/tablet/browser Terminals were peers at the logical layer.

In practice, this conflation of *logical workspace* and *physical device* caused problems:

1. **Hardware registration displacement** — detecting a Stream Deck overwrote the `"default"` Terminal's identity (name, type, grid), because the code treated "physical terminal" as a Terminal *type* rather than a *renderer*.
2. **SSE mirroring** — when a user switches to a physical Stream Deck's view in the browser, the web viewer must show the **same widget state** as the hardware. The existing model had no concept of "two renderers, one logical surface."
3. **Many-to-one confusion** — a single Stream Deck cannot plausibly be two Terminals, but a single logical workspace can be rendered to two outputs (hardware + browser). The code had no term for this relationship.

During the `/grill-with-docs` session on 2026-06-13, the correct decomposition was identified:

- A **logical widget surface** (grid, layout, keymap, widget pool)
- Zero or more **renderers** that push that surface's frames to devices

The old "Terminal" term conflated both.

## Decision

VibeDeck adopts a three-concept decomposition:

### Endpoint（服务端点）

A logical grid surface — the persistent "workspace." An Endpoint owns:
- `id`, `name`, `grid`, `token` (persisted to config)
- A widget set and keymap (managed by LayoutEngine)
- Layout configuration

An Endpoint is **always active** and always has at least one Virtual Renderer (SSE). It is *not* a device.

### Renderer（渲染器）

A stateless byte-pusher that consumes a `StandardFrame` from an Endpoint and delivers it to a specific output. Two kinds:

| Renderer | Transport | Count per Endpoint |
|---|---|---|
| Virtual Renderer | SSE | Exactly 1 (always active, per-Endpoint SSE channel) |
| Physical Renderer | USB HID | 0 or 1 (active only after Binding) |

### Binding（绑定）

A persistent, exclusive relationship: one physical Stream Deck → one Endpoint. A Binding is user-initiated and grid-validated (hardware grid must contain or match the Endpoint grid). Stored in config.

### `default` Endpoint

The initial Endpoint created on first boot. It has no special status — it is not an "anchor" or a required entry. Users can delete it, rename it, or create others. It serves only as a convenient starting point.

## Consequences

- **Terminology**: `TerminalInfo` → `EndpointInfo`, `TerminalRegistry` → `EndpointRegistry`, `terminal_id` → `endpoint_id`. The word "Terminal" is deprecated in code and docs.
- **Config format**: `terminals:` key in `config.yaml` becomes `endpoints:`.
- **SSE & frame push**: per-Endpoint SSE channels remain, but the concept is now explicitly "Virtual Renderer" rather than "virtual terminal."
- **HID auto-detection**: no longer creates or overwrites an Endpoint — it creates a Binding proposal that the user confirms.
- **Widget pool activation**: `terminal_id` parameter in API endpoints becomes `endpoint_id`.
- **Migration**: existing `config.yaml` files with `terminals:` are read transparently. On first save, the key is migrated to `endpoints:`.
- ADR 0001 is superseded in terminology but its architectural decisions (multi-surface, per-Endpoint layout) remain valid.
