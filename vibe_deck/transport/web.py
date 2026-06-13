"""
Web Transport — convert StandardFrame to SSE-ready JSON with base64 PNG.

This is a stateless utility, not a transport that holds a connection.
The Web Server calls ``web_frame()`` to convert a StandardFrame into
the list-of-dicts format that the browser frontend consumes via SSE.
"""

from __future__ import annotations

import base64

from ..render.standard_frame import StandardFrame


def web_frame(frame: StandardFrame) -> list[dict]:
    """Convert a StandardFrame to the web frontend's JSON format.

    Each key gets its PNG bytes as a base64 data-URI.  Metadata fields
    (icon, color, animation, label, badge) are kept for the frontend's
    informational use, but the authoritative visual is the ``image``
    field — the browser displays the pre-rendered PNG directly.

    Returns:
        List of dicts, one per key, suitable for JSON serialization
        and SSE broadcast.
    """
    keys = []
    for ki in frame.keys:
        keys.append({
            "index": ki.index,
            "widget_id": ki.widget_id,
            "image": base64.b64encode(ki.png).decode("ascii") if ki.png else "",
            "animation_mode": "sprite",  # tell frontend to display the pre-rendered PNG
        })
    return keys
