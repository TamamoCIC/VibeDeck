"""
VibeDeck Adapter Config Schema System.

Each adapter can declare its configurable fields via a simple dict schema.
The Settings UI reads the schema to auto-generate forms for editing adapter
appearance (icon, color, animation, label) and timing parameters.

Schema shape::

    {
        "<field_name>": {
            "type": "<field_type>",          # required — one of the types below
            "label": "<human-readable name>", # required
            "default": <value>,               # optional — used when no value set
            "description": "<help text>",     # optional — shown as tooltip in UI
            "ui": "<ui_hint>",                # optional — hints to the form renderer
            "options": [...],                 # required when type="select"
            "min": <number>,                  # optional — for int/float
            "max": <number>,                  # optional — for int/float
        },
        ...
    }

Supported field types:
    "string"     — Free text
    "int"        — Integer number
    "float"      — Floating-point number
    "bool"       — True/false
    "color"      — Hex color string (#rrggbb)
    "select"     — Pick one from a list of options
    "icon"       — Emoji icon
    "animation"  — One of the AnimationType values (none, pulse, crawl, blink, progress)

UI hints:
    "text"         — Plain text input (default for string)
    "number"       — Numeric input (default for int/float)
    "slider"       — Range slider (for int/float with min/max)
    "toggle"       — On/off switch (default for bool)
    "color-picker" — Color picker widget (default for color)
    "dropdown"     — Dropdown selector (default for select)
    "emoji-picker" — Emoji picker (default for icon)
"""

from __future__ import annotations

from typing import Any, Dict

# ── Public type alias ─────────────────────────────────────────────────

ADAPTER_CONFIG_SCHEMA = Dict[str, Dict[str, Any]]
"""
``{field_name: {type, label, default, description, ui, options?, min?, max?}}``
"""

# ── Valid values ──────────────────────────────────────────────────────

_VALID_TYPES: frozenset[str] = frozenset({
    "string",
    "int",
    "float",
    "bool",
    "color",
    "select",
    "icon",
    "animation",
})

_VALID_UI_HINTS: frozenset[str] = frozenset({
    "text",
    "number",
    "slider",
    "toggle",
    "color-picker",
    "dropdown",
    "emoji-picker",
})

_VALID_ANIMATIONS: frozenset[str] = frozenset({
    "none",
    "pulse",
    "crawl",
    "blink",
    "progress",
})

# ── Schema builder ────────────────────────────────────────────────────


def build_config_schema(fields: dict[str, dict[str, Any]]) -> ADAPTER_CONFIG_SCHEMA:
    """Validate and normalise a raw schema dict.

    Each field entry **must** have at least ``type`` and ``label``.
    The returned schema has all optional keys filled with their defaults.

    Raises ``ValueError`` on invalid input — callers should catch this
    and fall back to an empty schema.
    """
    schema: ADAPTER_CONFIG_SCHEMA = {}

    for field_name, config in fields.items():
        if not isinstance(field_name, str) or not field_name.strip():
            raise ValueError(f"Field name must be a non-empty string, got {field_name!r}")

        if not isinstance(config, dict):
            raise ValueError(
                f"Config for field {field_name!r} must be a dict, "
                f"got {type(config).__name__}"
            )

        field_type = config.get("type")
        if not isinstance(field_type, str) or field_type not in _VALID_TYPES:
            raise ValueError(
                f"Field {field_name!r}: invalid or missing type {field_type!r}. "
                f"Must be one of {sorted(_VALID_TYPES)}"
            )

        label = config.get("label")
        if not isinstance(label, str) or not label.strip():
            raise ValueError(
                f"Field {field_name!r}: 'label' is required and must be a non-empty string"
            )

        # Start building the normalised entry
        entry: dict[str, Any] = {
            "type": field_type,
            "label": label.strip(),
        }

        # default — optional, type-specific default if missing
        if "default" in config:
            entry["default"] = config["default"]

        # description
        desc = config.get("description")
        if isinstance(desc, str) and desc.strip():
            entry["description"] = desc.strip()

        # ui hint — infer from type if not provided
        ui = config.get("ui")
        if ui is not None and ui not in _VALID_UI_HINTS:
            raise ValueError(
                f"Field {field_name!r}: invalid ui hint {ui!r}. "
                f"Must be one of {sorted(_VALID_UI_HINTS)}"
            )
        if ui:
            entry["ui"] = ui

        # options — required for select
        if field_type == "select":
            options = config.get("options")
            if not isinstance(options, (list, tuple)) or len(options) == 0:
                raise ValueError(
                    f"Field {field_name!r}: type 'select' requires a non-empty 'options' list"
                )
            entry["options"] = list(options)

        elif "options" in config:
            # Non-select fields with options are fine but useless — ignore silently
            pass

        # min / max — only meaningful for int / float
        for bound in ("min", "max"):
            if bound in config:
                if field_type not in ("int", "float"):
                    continue  # silently ignore on non-numeric fields
                val = config[bound]
                if not isinstance(val, (int, float)):
                    raise ValueError(
                        f"Field {field_name!r}: {bound} must be a number, "
                        f"got {type(val).__name__}"
                    )
                entry[bound] = val

        # Validate min <= max if both present
        if "min" in entry and "max" in entry and entry["min"] > entry["max"]:
            raise ValueError(
                f"Field {field_name!r}: min ({entry['min']}) > max ({entry['max']})"
            )

        schema[field_name.strip()] = entry

    return schema


# ── Built-in schema fragments ─────────────────────────────────────────

_DEFAULT_CONFIG_FIELDS_RAW: dict[str, dict[str, Any]] = {
    "icon": {
        "type": "icon",
        "label": "Icon",
        "default": "🤖",
        "description": "Emoji shown on the Stream Deck key",
        "ui": "emoji-picker",
    },
    "color": {
        "type": "color",
        "label": "Color",
        "default": "#22c55e",
        "description": "Background hex color for the key",
        "ui": "color-picker",
    },
    "animation": {
        "type": "animation",
        "label": "Animation",
        "default": "none",
        "description": "Animation style for the key",
        "ui": "dropdown",
        "options": [
            {"value": "none", "label": "None"},
            {"value": "pulse", "label": "Pulse"},
            {"value": "crawl", "label": "Crawl"},
            {"value": "blink", "label": "Blink"},
            {"value": "progress", "label": "Progress"},
        ],
    },
    "label": {
        "type": "string",
        "label": "Label",
        "default": "Agent",
        "description": "Short text overlay (max 12 characters)",
        "ui": "text",
    },
}

DEFAULT_CONFIG_FIELDS: ADAPTER_CONFIG_SCHEMA = build_config_schema(_DEFAULT_CONFIG_FIELDS_RAW)
"""
Common appearance fields that every adapter shares.
These form the base config form for all adapters.
"""

_TIMING_CONFIG_FIELDS_RAW: dict[str, dict[str, Any]] = {
    "thinking_timeout_ms": {
        "type": "int",
        "label": "Thinking Timeout (ms)",
        "default": 800,
        "description": "Silence duration (ms) before transitioning to 'Thinking' state",
        "ui": "number",
        "min": 0,
        "max": 10000,
    },
    "activity_window_ms": {
        "type": "int",
        "label": "Activity Window (ms)",
        "default": 3000,
        "description": "Fast frame-rate window (ms) after the last hook event",
        "ui": "number",
        "min": 100,
        "max": 60000,
    },
    "slow_frame_interval_ms": {
        "type": "int",
        "label": "Slow Frame Interval (ms)",
        "default": 1000,
        "description": "Frame push interval (ms) when idle (~1 fps)",
        "ui": "number",
        "min": 100,
        "max": 30000,
    },
    "fast_frame_interval_ms": {
        "type": "int",
        "label": "Fast Frame Interval (ms)",
        "default": 33,
        "description": "Frame push interval (ms) when active (~30 fps)",
        "ui": "number",
        "min": 16,
        "max": 1000,
    },
}

TIMING_CONFIG_FIELDS: ADAPTER_CONFIG_SCHEMA = build_config_schema(_TIMING_CONFIG_FIELDS_RAW)
"""
The 4 timing parameters exposed as a schema.
Claude Code is the only built-in adapter that currently uses timing fields.
"""


# ── Per-adapter schema resolution ─────────────────────────────────────


# Registry of adapter-specific schema fragments.
# Key is the adapter short name, value is a list of schema dicts to merge.
# Appearance (DEFAULT_CONFIG_FIELDS) is always included automatically.
_ADAPTER_EXTRA_SCHEMAS: dict[str, ADAPTER_CONFIG_SCHEMA] = {
    "claude-code": TIMING_CONFIG_FIELDS,
}


def _merge_schemas(*schemas: ADAPTER_CONFIG_SCHEMA) -> ADAPTER_CONFIG_SCHEMA:
    """Merge multiple schema dicts. Later schemas win on conflict."""
    merged: ADAPTER_CONFIG_SCHEMA = {}
    for schema in schemas:
        merged.update(schema)
    return merged


def get_adapter_schema(adapter_name: str) -> ADAPTER_CONFIG_SCHEMA:
    """Return the full config schema for a built-in adapter.

    Every adapter gets the ``DEFAULT_CONFIG_FIELDS`` (appearance).
    Some adapters (e.g. ``claude-code``) additionally have timing fields.

    Args:
        adapter_name: Short name of the adapter (e.g. ``"claude-code"``,
            ``"opencode"``, ``"openclaw"``, ``"telegram"``).

    Returns:
        A merged ``ADAPTER_CONFIG_SCHEMA`` dict.
    """
    name = adapter_name.lower().strip()
    extra = _ADAPTER_EXTRA_SCHEMAS.get(name, {})
    return _merge_schemas(DEFAULT_CONFIG_FIELDS, extra)
