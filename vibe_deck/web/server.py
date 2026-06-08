"""
VibeDeck Web Server — serves the Web Simulator and REST API.

Uses aiohttp for async HTTP + SSE + WebSocket support.
Serves static files from vibe_deck/web/static/ and provides:
  - GET  /              → Web Simulator SPA
  - GET  /api/frame     → Current LayoutFrame as JSON
  - GET  /api/events    → SSE stream of frame updates
  - POST /api/key/{i}   → Simulate a key press
  - GET  /api/layouts   → List available layouts
  - POST /api/layouts   → Save/load layout
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

from ..core.layout import LayoutEngine

log = logging.getLogger("vibe_deck.web")

STATIC_DIR = Path(__file__).parent / "static"


class VibeDeckWebServer:
    """Async HTTP server for the VibeDeck Web UI."""

    def __init__(self, layout_engine: LayoutEngine, port: int = 9734) -> None:
        self._engine = layout_engine
        self._port = port
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        self._clients: list[web.StreamResponse] = []  # SSE subscribers

        # Routes
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/api/frame", self._get_frame)
        self._app.router.add_get("/api/events", self._sse_events)
        self._app.router.add_post("/api/key/{index}", self._key_press)
        self._app.router.add_get("/api/layouts", self._list_layouts)
        self._app.router.add_static("/static/", STATIC_DIR, show_index=False)

    @property
    def port(self) -> int:
        return self._port

    @property
    def app(self) -> web.Application:
        return self._app

    async def start(self) -> None:
        """Start the web server."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "localhost", self._port)
        await site.start()
        log.info("Web server started at http://localhost:%d", self._port)

    async def stop(self) -> None:
        """Stop the web server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            log.info("Web server stopped")

    async def broadcast_frame(self, terminal_id: str = "default", frame=None) -> None:
        """Push a LayoutFrame to SSE subscribers for a specific terminal.

        Args:
            terminal_id: The terminal to broadcast for.
            frame: Optional LayoutFrame override. If None, uses the default frame.
        """
        if not self._clients:
            return

        from ..render.sim import SimRenderer
        if frame is None:
            frame = self._engine.frame
        renderer = SimRenderer(frame.rows, frame.cols, frame.display_name)
        keys = renderer.render_frame(frame)

        data = json.dumps({"type": "frame", "keys": keys, "display_name": frame.display_name, "terminal_id": terminal_id})

        dead: list[web.StreamResponse] = []
        for resp in self._clients:
            try:
                await resp.write(f"data: {data}\n\n".encode())
            except Exception:
                dead.append(resp)

        for d in dead:
            self._clients.remove(d)

    # ── Handlers ──────────────────────────────────

    async def _index(self, request: web.Request) -> web.Response:
        """Serve the Web Simulator SPA."""
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return web.Response(text="Web Simulator not yet built", status=404)
        return web.FileResponse(index_path)

    async def _get_frame(self, request: web.Request) -> web.Response:
        """Return the current LayoutFrame as JSON."""
        from ..render.sim import SimRenderer
        frame = self._engine.frame
        renderer = SimRenderer(frame.rows, frame.cols, frame.display_name)
        keys = renderer.render_frame(frame)
        return web.json_response({
            "display_name": frame.display_name,
            "rows": frame.rows,
            "cols": frame.cols,
            "keys": keys,
        })

    async def _sse_events(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for real-time frame updates."""
        resp = web.StreamResponse(
            status=200,
            reason="OK",
            headers={
                "Content-Type": "text/event-stream",
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )
        await resp.prepare(request)
        self._clients.append(resp)

        # Keep connection alive
        try:
            while True:
                await resp.write(b": heartbeat\n\n")
                # 30s heartbeat to keep connection alive
                import asyncio
                await asyncio.sleep(30)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if resp in self._clients:
                self._clients.remove(resp)

        return resp

    async def _key_press(self, request: web.Request) -> web.Response:
        """Handle a simulated key press from the Web Simulator."""
        index = int(request.match_info["index"])
        log.debug("Simulated key press: %d", index)
        # In production, this publishes to the MessageBus as KEY_PRESSED
        return web.json_response({"status": "ok", "key": index})

    async def _list_layouts(self, request: web.Request) -> web.Response:
        """List available layout files."""
        from ..config import LAYOUTS_DIR
        layouts = []
        if LAYOUTS_DIR.exists():
            for f in sorted(LAYOUTS_DIR.glob("*.yaml")):
                layouts.append({"name": f.stem, "path": str(f)})
        return web.json_response({"layouts": layouts})
