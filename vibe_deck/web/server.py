"""
VibeDeck Web Server — serves the Web Simulator and REST API.

Uses aiohttp for async HTTP + SSE + WebSocket support.
Serves static files from vibe_deck/web/static/ and provides:
  - GET  /              → Web Simulator SPA
  - GET  /api/frame?token=xxx  → Current LayoutFrame as JSON
  - GET  /api/events?token=xxx → SSE stream of frame updates (per-terminal)
  - POST /api/key/{i}?token=xxx → Simulate a key press
  - GET  /api/terminal/status?token=xxx → Check if terminal is registered
  - POST /api/terminal/register → Register a new virtual terminal
  - GET  /api/layouts   → List available layouts
  - POST /api/layouts   → Save/load layout
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from aiohttp import web

from ..core.layout import LayoutEngine
from ..core.terminal_registry import TerminalRegistry
from ..render.sim import SimRenderer

log = logging.getLogger("vibe_deck.web")

STATIC_DIR = Path(__file__).parent / "static"


def _extract_token(request: web.Request) -> str | None:
    """Extract token from query string or Authorization header."""
    token = request.query.get("token")
    if token:
        return token
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


class VibeDeckWebServer:
    """Async HTTP server for the VibeDeck Web UI."""

    def __init__(
        self,
        layout_engine: LayoutEngine,
        registry: TerminalRegistry,
        port: int = 9734,
        expose: bool = False,
    ) -> None:
        self._engine = layout_engine
        self._registry = registry
        self._port = port
        self._expose = expose
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        # Per-terminal SSE subscribers: terminal_id → list[StreamResponse]
        self._clients: dict[str, list[web.StreamResponse]] = {}
        # Per-terminal SimRenderer instances
        self._renderers: dict[str, SimRenderer] = {}

        # Routes
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/api/frame", self._get_frame)
        self._app.router.add_get("/api/events", self._sse_events)
        self._app.router.add_post("/api/key/{index}", self._key_press)
        self._app.router.add_get("/api/terminal/status", self._terminal_status)
        self._app.router.add_post("/api/terminal/register", self._terminal_register)
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
        host = "0.0.0.0" if self._expose else "localhost"
        site = web.TCPSite(self._runner, host, self._port)
        await site.start()
        log.info("Web server started at http://%s:%d (expose=%s)", host, self._port, self._expose)

    def close_sse_connections(self) -> None:
        """Close all active SSE connections gracefully.

        Must be called before stop() to prevent aiohttp InvalidStateError
        on Windows during shutdown.
        """
        count = sum(len(clients) for clients in self._clients.values())
        if count:
            log.info("Closing %d SSE connection(s)...", count)
            for terminal_id, clients in list(self._clients.items()):
                for resp in clients:
                    try:
                        resp.force_close()
                    except Exception:
                        pass
            self._clients.clear()
            self._renderers.clear()

    async def stop(self) -> None:
        """Stop the web server."""
        self.close_sse_connections()
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            log.info("Web server stopped")

    # ── Auth helper ────────────────────────────────

    def _auth(self, request: web.Request) -> str | None:
        """Validate token and return terminal_id, or None if invalid."""
        token = _extract_token(request)
        if not token:
            return None
        terminal = self._registry.get_by_token(token)
        if terminal is None:
            return None
        return terminal.id

    # ── Per-terminal renderer ──────────────────────

    def _get_renderer(self, rows: int, cols: int, display_name: str) -> SimRenderer:
        """Get or create a SimRenderer for the given grid dimensions."""
        key = f"{rows}x{cols}"
        if key not in self._renderers:
            self._renderers[key] = SimRenderer(rows, cols, display_name)
        return self._renderers[key]

    # ── Frame broadcast ────────────────────────────

    async def broadcast_frame(self, terminal_id: str = "default", frame=None) -> None:
        """Push a LayoutFrame to SSE subscribers for a specific terminal."""
        clients = self._clients.get(terminal_id, [])
        if not clients:
            return

        if frame is None:
            frame = self._engine.get_frame(terminal_id)
        if frame is None:
            return

        renderer = self._get_renderer(frame.rows, frame.cols, frame.display_name)
        keys = renderer.render_frame(frame)

        data = json.dumps({
            "type": "frame",
            "keys": keys,
            "display_name": frame.display_name,
            "terminal_id": terminal_id,
        })

        dead: list[web.StreamResponse] = []
        for resp in clients:
            try:
                await resp.write(f"data: {data}\n\n".encode())
            except Exception:
                dead.append(resp)

        for d in dead:
            clients.remove(d)

    # ── Handlers ──────────────────────────────────

    async def _index(self, request: web.Request) -> web.Response:
        """Serve the Web Simulator SPA."""
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return web.Response(text="Web Simulator not yet built", status=404)
        return web.FileResponse(index_path)

    async def _get_frame(self, request: web.Request) -> web.Response:
        """Return the current LayoutFrame as JSON (token-authenticated)."""
        terminal_id = self._auth(request)
        if terminal_id is None:
            return web.json_response({"error": "invalid or missing token"}, status=401)

        frame = self._engine.get_frame(terminal_id)
        if frame is None:
            return web.json_response({"error": "terminal not found"}, status=404)

        renderer = self._get_renderer(frame.rows, frame.cols, frame.display_name)
        keys = renderer.render_frame(frame)
        return web.json_response({
            "display_name": frame.display_name,
            "rows": frame.rows,
            "cols": frame.cols,
            "keys": keys,
            "terminal_id": terminal_id,
        })

    async def _sse_events(self, request: web.Request) -> web.StreamResponse:
        """SSE endpoint for real-time frame updates (token-authenticated)."""
        token = _extract_token(request)
        if not token:
            raise web.HTTPUnauthorized(text="Missing token")

        terminal = self._registry.get_by_token(token)
        if terminal is None:
            raise web.HTTPUnauthorized(text="Invalid token")

        terminal_id = terminal.id

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

        # Register in per-terminal group
        if terminal_id not in self._clients:
            self._clients[terminal_id] = []
        self._clients[terminal_id].append(resp)
        log.debug("SSE client connected for terminal %r (%d total)", terminal_id, len(self._clients[terminal_id]))

        # Keep connection alive
        try:
            while True:
                await resp.write(b": heartbeat\n\n")
                import asyncio
                await asyncio.sleep(30)
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if terminal_id in self._clients and resp in self._clients[terminal_id]:
                self._clients[terminal_id].remove(resp)
                log.debug("SSE client disconnected for terminal %r", terminal_id)

        return resp

    async def _key_press(self, request: web.Request) -> web.Response:
        """Handle a simulated key press (token-authenticated)."""
        terminal_id = self._auth(request)
        if terminal_id is None:
            return web.json_response({"error": "invalid or missing token"}, status=401)

        index = int(request.match_info["index"])
        log.debug("Key %d pressed on terminal %r", index, terminal_id)
        return web.json_response({"status": "ok", "key": index, "terminal_id": terminal_id})

    async def _terminal_status(self, request: web.Request) -> web.Response:
        """Check if a token is registered. Returns terminal info or 404."""
        token = _extract_token(request)
        if not token:
            return web.json_response({"error": "missing token"}, status=400)

        terminal = self._registry.get_by_token(token)
        if terminal is None:
            return web.json_response({"registered": False}, status=404)

        return web.json_response({
            "registered": True,
            "terminal": {
                "id": terminal.id,
                "name": terminal.name,
                "type": terminal.type,
                "grid": terminal.grid,
            },
        })

    async def _terminal_register(self, request: web.Request) -> web.Response:
        """Register a new virtual terminal. Returns terminal info with token."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        name = body.get("name", "").strip()
        grid = body.get("grid", "4x8")
        terminal_type = body.get("type", "virtual")

        if not name:
            return web.json_response({"error": "name is required"}, status=400)

        # Validate grid
        if grid not in ("3x4", "3x5", "4x8"):
            return web.json_response({"error": f"invalid grid: {grid}"}, status=400)

        # Parse rows/cols from grid
        rows, cols = map(int, grid.split("x"))

        # Create terminal in registry
        terminal = self._registry.register(
            name=name,
            terminal_type=terminal_type,
            grid=grid,
            layout=f"{name}.yaml",
        )

        # Register in LayoutEngine
        self._engine.register_terminal(terminal.id, rows, cols, terminal.name)

        log.info("Virtual terminal %r registered (grid=%s, token=%s...)", name, grid, terminal.token[:8])

        return web.json_response({
            "status": "ok",
            "terminal": terminal.to_dict(),
        }, status=201)

    async def _list_layouts(self, request: web.Request) -> web.Response:
        """List available layout files."""
        from ..config import LAYOUTS_DIR
        layouts = []
        if LAYOUTS_DIR.exists():
            for f in sorted(LAYOUTS_DIR.glob("*.yaml")):
                layouts.append({"name": f.stem, "path": str(f)})
        return web.json_response({"layouts": layouts})
