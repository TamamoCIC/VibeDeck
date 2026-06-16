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
        bus=None,
        shutdown_cb=None,
        animation_engine=None,
        renderer=None,   # PILRenderer (new pipeline)
    ) -> None:
        self._engine = layout_engine
        self._registry = registry
        self._bus = bus
        self._port = port
        self._expose = expose
        self._shutdown_cb = shutdown_cb
        self._anim_engine = animation_engine
        self._renderer = renderer  # PILRenderer for StandardFrame rendering
        self._app = web.Application()
        self._runner: web.AppRunner | None = None
        # Per-terminal SSE subscribers: terminal_id → list[StreamResponse]
        self._clients: dict[str, list[web.StreamResponse]] = {}
        # Legacy SimRenderer cache (used only when PILRenderer is unavailable)
        self._renderers: dict[str, "SimRenderer"] = {}

        # Routes
        self._app.router.add_get("/", self._index)
        self._app.router.add_get("/api/frame", self._get_frame)
        self._app.router.add_get("/api/events", self._sse_events)
        self._app.router.add_post("/api/key/{index}", self._key_press)
        self._app.router.add_get("/api/terminal/status", self._terminal_status)
        self._app.router.add_post("/api/terminal/register", self._terminal_register)
        self._app.router.add_get("/api/terminals", self._list_terminals)
        self._app.router.add_get("/api/widgets", self._find_widget)
        self._app.router.add_get("/api/layouts", self._list_layouts)
        self._app.router.add_post("/api/layouts/save", self._save_layout)
        self._app.router.add_post("/api/layouts/load", self._load_layout)
        self._app.router.add_get("/api/appearance", self._get_appearance)
        self._app.router.add_post("/api/appearance", self._save_appearance)
        self._app.router.add_get("/api/theme", self._get_theme)
        self._app.router.add_post("/api/theme", self._save_theme)
        # Pool API
        self._app.router.add_get("/api/pool", self._pool_list)
        self._app.router.add_post("/api/pool/activate", self._pool_activate_handler)
        self._app.router.add_post("/api/pool/deactivate", self._pool_deactivate_handler)
        self._app.router.add_route("DELETE", "/api/pool/{widget_id}", self._pool_delete_handler)
        # Focus agent window
        self._app.router.add_post("/api/widget/{widget_id}/focus", self._widget_focus_handler)
        # Animation clips
        self._app.router.add_get("/api/clips", self._list_clips)
        # Shutdown
        self._app.router.add_post("/api/shutdown", self._shutdown_handler)

        # ── Daemon Config ────────────────────────────
        self._app.router.add_get("/api/config", self._get_config)
        self._app.router.add_post("/api/config", self._post_config)

        # ── Terminal Management ──────────────────────
        self._app.router.add_post("/api/terminals/{id}/rename", self._terminal_rename)
        self._app.router.add_delete("/api/terminals/{id}", self._terminal_delete)
        self._app.router.add_post("/api/terminals/{id}/grid", self._terminal_set_grid)

        # ── Layout Management ────────────────────────
        self._app.router.add_delete("/api/layouts/{name}", self._layout_delete)
        self._app.router.add_post("/api/layouts/{name}/rename", self._layout_rename)

        # ── Adapter Management ───────────────────────
        self._app.router.add_get("/api/adapters", self._list_adapters)
        self._app.router.add_get("/api/adapters/{name}/config", self._get_adapter_config)
        self._app.router.add_post("/api/adapters/{name}/config", self._post_adapter_config)
        self._app.router.add_get("/api/adapters/{name}/appearance", self._get_adapter_appearance)
        self._app.router.add_post("/api/adapters/{name}/appearance", self._post_adapter_appearance)

        # ── Timing ───────────────────────────────────
        self._app.router.add_get("/api/timing", self._get_timing)
        self._app.router.add_post("/api/timing", self._post_timing)

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
        try:
            await site.start()
        except OSError as e:
            raise RuntimeError(
                f"Port {self._port} is already in use — another VibeDeck "
                f"instance may still be running.\n"
                f"  • Close the other terminal window, or\n"
                f"  • Run: curl -X POST http://localhost:{self._port}/api/shutdown\n"
                f"  • Or try: vibe-deck serve --port {self._port + 1}"
            ) from e
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
            self._renderers[key] = SimRenderer(
                rows, cols, display_name,
                animation_engine=self._anim_engine,
            )
        return self._renderers[key]

    # ── Frame broadcast ────────────────────────────

    async def broadcast_frame(
        self,
        terminal_id: str = "default",
        frame=None,              # list[dict] — web_frame output
        layout_frame=None,       # LayoutFrame — for metadata/pool
        debug_info: dict | None = None,
    ) -> None:
        """Push pre-rendered key images to SSE subscribers."""
        clients = self._clients.get(terminal_id, [])
        if not clients:
            return

        if frame is None:
            return

        keys = frame  # already rendered by PILRenderer → web_frame()

        # Collect widget metadata from the layout frame for the UI inspector
        widget_meta = {}
        if layout_frame is not None:
            widget_meta = {
                wid: ws.meta for wid, ws in layout_frame.widgets.items()
            }

        # Pool data for the UI widget panel
        pool_widgets = []
        for ws in self._engine.pool_list():
            cwd = ws.meta.get("cwd", "")
            project = ws.meta.get("project", "")
            if not project and cwd:
                from pathlib import Path as _PPath
                project = _PPath(cwd).name
            pool_widgets.append({
                "id": ws.id,
                "type": ws.type.value,
                "icon": ws.display.icon,
                "color": ws.display.color,
                "animation": ws.display.animation.value if hasattr(ws.display.animation, 'value') else str(ws.display.animation),
                "label": ws.display.label,
                "badge": ws.display.badge,
                "project": project,
                "meta": ws.meta,
                "activated_on": self._engine.pool_activated_terminals(ws.id),
            })

        display_name = (
            layout_frame.display_name if layout_frame else "4x8"
        )

        data = json.dumps({
            "type": "frame",
            "keys": keys,
            "display_name": display_name,
            "terminal_id": terminal_id,
            "_meta": widget_meta,
            "_pool": pool_widgets,
            "_debug": debug_info or {},
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
        """Serve the Web Simulator SPA (no-cache to always pick up changes)."""
        index_path = STATIC_DIR / "index.html"
        if not index_path.exists():
            return web.Response(text="Web Simulator not yet built", status=404)
        return web.FileResponse(
            index_path,
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )

    async def _get_frame(self, request: web.Request) -> web.Response:
        """Return the current LayoutFrame as JSON (token-authenticated)."""
        terminal_id = self._auth(request)
        if terminal_id is None:
            return web.json_response({"error": "invalid or missing token"}, status=401)

        layout_frame = self._engine.get_frame(terminal_id)
        if layout_frame is None:
            return web.json_response({"error": "terminal not found"}, status=404)

        # Render via new pipeline if available, else fall back to SimRenderer
        if self._renderer is not None:
            from ..transport.web import web_frame
            sf = self._renderer.render(layout_frame)
            keys = web_frame(sf)
        else:
            renderer = self._get_renderer(
                layout_frame.rows, layout_frame.cols, layout_frame.display_name,
            )
            keys = renderer.render_frame(layout_frame)

        return web.json_response({
            "display_name": layout_frame.display_name,
            "rows": layout_frame.rows,
            "cols": layout_frame.cols,
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
        log.info("Key %d pressed on terminal %r", index, terminal_id)

        # Publish to MessageBus so the supervisor can react
        if self._bus:
            from ..core.message_bus import Message, MessageType
            try:
                frame = self._engine.get_frame(terminal_id)
                widgets = list(frame.widgets.keys()) if frame else []
                widget_at_key = None
                for wid, ws in (frame.widgets.items() if frame else {}):
                    if ws.key_index == index:
                        widget_at_key = ws.id
                        break

                import asyncio
                asyncio.create_task(
                    self._bus.publish(Message(
                        type=MessageType.KEY_PRESSED,
                        source="web-server",
                        payload={
                            "terminal_id": terminal_id,
                            "key": index,
                            "widgets_at_key": widget_at_key or "none",
                        },
                    ))
                )
            except Exception:
                log.debug("Failed to publish key press", exc_info=True)

        return web.json_response({
            "status": "ok",
            "key": index,
            "terminal_id": terminal_id,
        })

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

        # Validate grid format (allows all Stream Deck models:
        # Mini 3x2, Neo 2x4, Original 3x5, XL 4x8, Plus 4x2, etc.)
        try:
            rows, cols = map(int, grid.split("x"))
            if rows < 1 or cols < 1 or rows > 32 or cols > 32:
                raise ValueError
        except (ValueError, TypeError):
            return web.json_response({"error": f"invalid grid: {grid!r} (expected NxM)"}, status=400)

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

    async def _list_terminals(self, request: web.Request) -> web.Response:
        """List all registered terminals (no auth required — local daemon).

        Returns id, name, type, grid, widget count, and the full token
        for every terminal so the sidebar can switch between them.
        """
        terminals = []
        for t in self._registry.list_all():
            frame = self._engine.get_frame(t.id)
            entry = {
                "id": t.id,
                "name": t.name,
                "type": t.type,
                "grid": t.grid,
                "widget_count": len(frame.widgets) if frame else 0,
                "created_at": t.created_at,
                "token": t.token,
            }
            terminals.append(entry)
        # Sort: default first, then by name
        terminals.sort(key=lambda t: (0 if t["id"] == "default" else 1, t["name"]))
        return web.json_response({"terminals": terminals})

    async def _find_widget(self, request: web.Request) -> web.Response:
        """Find a widget by PID or session_id across all terminals.

        Query params:
          ?pid=N         — search by process ID (meta.pid)
          ?session_id=S  — search by session_id (meta.session_id)

        Returns the first matching widget with its terminal context,
        or ``{"found": false}`` if no match.
        """
        pid_str = request.query.get("pid", "")
        session_id = request.query.get("session_id", "")

        if not pid_str and not session_id:
            return web.json_response(
                {"found": False, "error": "pass ?pid=N or ?session_id=S"},
                status=400,
            )

        target_pid = int(pid_str) if pid_str else None

        for terminal_id in self._engine.list_terminals():
            frame = self._engine.get_frame(terminal_id)
            if frame is None:
                continue
            for widget_id, ws in frame.widgets.items():
                meta = ws.meta
                if target_pid is not None and meta.get("pid") == target_pid:
                    return web.json_response({
                        "found": True,
                        "terminal_id": terminal_id,
                        "widget_id": widget_id,
                        "type": ws.type.value,
                        "display": ws.display.model_dump(),
                        "meta": meta,
                        "key_index": self._widget_key_index(frame, widget_id),
                    })
                if session_id and meta.get("session_id", "").startswith(session_id):
                    return web.json_response({
                        "found": True,
                        "terminal_id": terminal_id,
                        "widget_id": widget_id,
                        "type": ws.type.value,
                        "display": ws.display.model_dump(),
                        "meta": meta,
                        "key_index": self._widget_key_index(frame, widget_id),
                    })

        return web.json_response({"found": False})

    def _widget_key_index(self, frame, widget_id: str) -> int | None:
        """Return the key index where a widget is placed, or None."""
        try:
            return frame.keymap.index(widget_id)
        except ValueError:
            return None

    async def _list_layouts(self, request: web.Request) -> web.Response:
        """List available layout files (excluding autosave internal files)."""
        from ..config import LAYOUTS_DIR
        layouts = []
        if LAYOUTS_DIR.exists():
            for f in sorted(LAYOUTS_DIR.glob("*.yaml")):
                # Exclude autosave internal files
                if f.name.startswith("_autosave-"):
                    continue
                layouts.append({"name": f.stem, "path": str(f)})
        return web.json_response({"layouts": layouts})

    async def _save_layout(self, request: web.Request) -> web.Response:
        """Save the current active layout as a named layout."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        name = body.get("name", "").strip()
        terminal_id = body.get("terminal_id", "default")
        if not name:
            return web.json_response({"error": "name is required"}, status=400)

        result = self._engine.save_layout_as(name, terminal_id)
        if result is None:
            return web.json_response({"error": f"terminal {terminal_id!r} not found"}, status=404)

        frame = self._engine.get_frame(terminal_id)
        log.info("Layout saved: %s (terminal=%s, widgets=%d)", name, terminal_id,
                 len(frame.widgets) if frame else 0)
        return web.json_response({"status": "ok", "path": str(result)})

    async def _load_layout(self, request: web.Request) -> web.Response:
        """Load a saved layout and set it as the active layout for a terminal."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        name = body.get("name", "").strip()
        terminal_id = body.get("terminal_id", "default")
        if not name:
            return web.json_response({"error": "name is required"}, status=400)

        from ..config import LAYOUTS_DIR
        safe_name = name.replace("/", "_").replace("\\", "_")
        path = LAYOUTS_DIR / f"{safe_name}.yaml"
        if not path.exists():
            return web.json_response({"error": f"layout {name!r} not found"}, status=404)

        frame = self._engine.load_layout(str(path), terminal_id, as_name=name)
        if frame is None:
            return web.json_response({"error": f"terminal {terminal_id!r} not found"}, status=404)

        log.info("Layout loaded: %s → terminal %s (widgets=%d)", name, terminal_id, len(frame.widgets))
        return web.json_response({"status": "ok", "name": name})

    async def _get_appearance(self, request: web.Request) -> web.Response:
        """Return the current widget appearance config."""
        from ..adapters.claude_code import STATUS_TO_DISPLAY
        return web.json_response({"events": STATUS_TO_DISPLAY})

    async def _save_appearance(self, request: web.Request) -> web.Response:
        """Update the widget appearance config and persist to YAML."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        events = body.get("events")
        if not events or not isinstance(events, dict):
            return web.json_response({"error": "events dict is required"}, status=400)

        from ..adapters.claude_code import STATUS_TO_DISPLAY, _save_appearance_config
        STATUS_TO_DISPLAY.clear()
        STATUS_TO_DISPLAY.update(events)
        _save_appearance_config(events)
        log.info("Appearance config saved (%d entries)", len(events))
        return web.json_response({"status": "ok"})

    async def _get_theme(self, request: web.Request) -> web.Response:
        """Return the current web UI theme as CSS variables."""
        from ..config import TEMPLATES_DIR
        theme_path = TEMPLATES_DIR.parent / "themes" / "default.css"
        if theme_path.exists():
            css = theme_path.read_text(encoding="utf-8")
        else:
            css = _DEFAULT_THEME_CSS
        return web.json_response({"css": css})

    async def _save_theme(self, request: web.Request) -> web.Response:
        """Save a custom web UI theme."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        css = body.get("css", "")
        if not css:
            return web.json_response({"error": "css is required"}, status=400)

        from ..config import TEMPLATES_DIR
        themes_dir = TEMPLATES_DIR.parent / "themes"
        themes_dir.mkdir(parents=True, exist_ok=True)
        theme_path = themes_dir / "default.css"
        theme_path.write_text(css, encoding="utf-8")
        log.info("Theme saved (%d chars)", len(css))
        return web.json_response({"status": "ok"})


    # ── Pool API ──────────────────────────────────

    async def _pool_list(self, request: web.Request) -> web.Response:
        """Return all widgets in the pool with activation status."""
        widgets = []
        for ws in self._engine.pool_list():
            cwd = ws.meta.get("cwd", "")
            project = ws.meta.get("project", "")
            if not project and cwd:
                from pathlib import Path as _PPath
                project = _PPath(cwd).name
            widgets.append({
                "id": ws.id,
                "type": ws.type.value,
                "icon": ws.display.icon,
                "color": ws.display.color,
                "animation": ws.display.animation.value if hasattr(ws.display.animation, 'value') else str(ws.display.animation),
                "label": ws.display.label,
                "badge": ws.display.badge,
                "project": project,
                "meta": ws.meta,
                "activated_on": self._engine.pool_activated_terminals(ws.id),
            })
        return web.json_response({"pool": widgets})

    async def _pool_activate_handler(self, request: web.Request) -> web.Response:
        """Activate a pool widget on a terminal."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        widget_id = body.get("widget_id", "").strip()
        terminal_id = body.get("terminal_id", "default")
        key_index = body.get("key_index", None)

        if not widget_id:
            return web.json_response({"error": "widget_id is required"}, status=400)

        ok = self._engine.pool_activate(widget_id, terminal_id, key_index)
        if not ok:
            return web.json_response(
                {"error": f"failed to activate {widget_id!r} on {terminal_id!r}"},
                status=404)

        # Force immediate frame push so the physical device updates instantly.
        if self._bus and hasattr(self, '_renderer') and self._renderer:
            try:
                from ..core.message_bus import Message, MessageType
                await self._bus.publish(Message(
                    type=MessageType.LAYOUT_CHANGED,
                    source="web",
                    payload={"terminal_id": terminal_id, "widget_id": widget_id},
                ))
            except Exception:
                pass  # best-effort — frame loop will catch it on next tick

        # Determine the key where the widget landed
        frame = self._engine.get_frame(terminal_id)
        placed_key = None
        if frame:
            try:
                placed_key = frame.keymap.index(widget_id)
            except ValueError:
                for kw_id, kw_ws in frame.widgets.items():
                    if kw_id == widget_id:
                        placed_key = kw_ws.key_index
                        break

        log.info("Pool widget %r activated on terminal %r at key %s",
                 widget_id, terminal_id, placed_key)
        return web.json_response({"status": "ok", "key_index": placed_key})

    async def _pool_deactivate_handler(self, request: web.Request) -> web.Response:
        """Deactivate a pool widget from a terminal (widget stays in pool)."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        widget_id = body.get("widget_id", "").strip()
        terminal_id = body.get("terminal_id", "default")

        if not widget_id:
            return web.json_response({"error": "widget_id is required"}, status=400)

        self._engine.pool_deactivate(widget_id, terminal_id)
        log.info("Pool widget %r deactivated from terminal %r", widget_id, terminal_id)

        # Force immediate frame push
        if self._bus:
            try:
                from ..core.message_bus import Message, MessageType
                await self._bus.publish(Message(
                    type=MessageType.LAYOUT_CHANGED,
                    source="web",
                    payload={"terminal_id": terminal_id, "widget_id": widget_id},
                ))
            except Exception:
                pass

        return web.json_response({"status": "ok"})

    async def _pool_delete_handler(self, request: web.Request) -> web.Response:
        """Delete a widget from the pool entirely.

        The widget is removed from the pool and from all terminals it was
        placed on.  If the agent process is still alive it will be
        re-registered automatically on the next scan cycle.
        """
        widget_id = request.match_info.get("widget_id", "")
        if not widget_id:
            return web.json_response({"error": "widget_id is required"}, status=400)

        log.info("[API] DELETE /api/pool/%s — removing from pool and all terminals", widget_id)

        try:
            # Remove from all terminals first, then push updated frames
            for tid in self._engine.list_terminals():
                self._engine.remove_widget(widget_id, tid)
                if self._bus:
                    try:
                        from ..core.message_bus import Message, MessageType
                        await self._bus.publish(Message(
                            type=MessageType.LAYOUT_CHANGED,
                            source="web",
                            payload={"terminal_id": tid, "widget_id": widget_id},
                        ))
                    except Exception:
                        pass

            # Remove from pool
            self._engine.pool_remove(widget_id)

            log.info("Pool widget %r deleted (will re-register if agent is still alive)", widget_id)
            return web.json_response({"status": "ok", "widget_id": widget_id})
        except Exception:
            log.exception("Failed to delete pool widget %r", widget_id)
            return web.json_response({"error": "internal error"}, status=500)

    async def _widget_focus_handler(self, request: web.Request) -> web.Response:
        """Focus the agent's program window (bring to foreground by PID)."""
        widget_id = request.match_info.get("widget_id", "")
        log.info("[API] POST /api/widget/%s/focus", widget_id)

        if not widget_id:
            return web.json_response({"error": "widget_id is required"}, status=400)

        # Search all terminals for the widget's PID
        pid = 0
        for tid in self._engine.list_terminals():
            frame = self._engine.get_frame(tid)
            if frame is None:
                continue
            ws = frame.widgets.get(widget_id)
            if ws is not None:
                pid = ws.meta.get("pid", 0)
                break

        # Fallback: check the pool
        if not pid:
            pool_ws = self._engine.pool_get(widget_id)
            if pool_ws:
                pid = pool_ws.meta.get("pid", 0)

        # Last resort: extract PID from widget_id (format: "agent-name-12345")
        if not pid:
            import re as _re
            _m = _re.search(r"-(\d+)$", widget_id)
            if _m:
                pid = int(_m.group(1))
                log.info("[API] focus: extracted pid=%d from widget_id %r", pid, widget_id)

        if not pid:
            log.warning("[API] focus: no PID found for widget %r", widget_id)
            return web.json_response(
                {"error": f"no PID found for widget {widget_id!r}"},
                status=404,
            )

        from ..platform import toggle_window_by_pid
        import asyncio
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, toggle_window_by_pid, pid)
        log.info("[API] focus toggle: pid=%d → %s", pid, result.get("action"))

        if result.get("action") == "error":
            return web.json_response({
                "error": result.get("message", "focus failed"),
            }, status=404)

        return web.json_response({
            "status": "ok",
            **result,
        })

    # ── Shutdown ──────────────────────────────────

    async def _shutdown_handler(self, request: web.Request) -> web.Response:
        """Shut down the VibeDeck daemon gracefully."""
        log.info("Shutdown requested via API")
        if self._shutdown_cb:
            import asyncio
            asyncio.get_running_loop().call_soon(self._shutdown_cb)
        return web.json_response({"status": "ok", "message": "Shutting down..."})


    # ── Daemon Config ──────────────────────────────

    async def _list_clips(self, request: web.Request) -> web.Response:
        """Return available sprite animation clip names."""
        if self._anim_engine is None:
            return web.json_response({"clips": []})
        return web.json_response({
            "clips": [{"name": n, "value": n} for n in self._anim_engine.clip_names],
        })

    async def _get_config(self, request: web.Request) -> web.Response:
        """Return the full daemon configuration."""
        from ..config import load_config
        try:
            cfg = load_config()
        except Exception:
            log.exception("Failed to load config")
            return web.json_response({"error": "failed to load config"}, status=500)

        return web.json_response({
            "port": cfg.port,
            "expose": cfg.expose,
            "autodetect": cfg.autodetect,
            "render": cfg.render,
            "device_index": cfg.device_index,
            "auto_enter_approval": cfg.auto_enter_approval,
            "timing": cfg.timing.to_dict(),
            "adapter_configs": [a.to_dict() for a in cfg.adapter_configs],
        })

    async def _post_config(self, request: web.Request) -> web.Response:
        """Update daemon config with partial JSON merge."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        from ..config import load_config, save_config
        try:
            cfg = load_config()
        except Exception:
            log.exception("Failed to load config for update")
            return web.json_response({"error": "failed to load config"}, status=500)

        # Merge top-level fields
        if "port" in body:
            cfg.port = int(body["port"])
        if "expose" in body:
            cfg.expose = bool(body["expose"])
        if "autodetect" in body:
            cfg.autodetect = bool(body["autodetect"])
        if "render" in body:
            cfg.render = str(body["render"])
        if "device_index" in body:
            cfg.device_index = int(body["device_index"])
        if "auto_enter_approval" in body:
            cfg.auto_enter_approval = bool(body["auto_enter_approval"])

        # Merge timing sub-config
        if "timing" in body and isinstance(body["timing"], dict):
            for key, val in body["timing"].items():
                if hasattr(cfg.timing, key):
                    setattr(cfg.timing, key, int(val))

        # Merge adapter_configs
        if "adapter_configs" in body and isinstance(body["adapter_configs"], list):
            from ..config import AdapterConfig
            updated = {a.name for a in cfg.adapter_configs}
            for raw in body["adapter_configs"]:
                if isinstance(raw, dict) and "name" in raw:
                    ac = AdapterConfig.from_dict(raw)
                    if raw["name"] in updated:
                        # Replace existing
                        for i, existing in enumerate(cfg.adapter_configs):
                            if existing.name == raw["name"]:
                                cfg.adapter_configs[i] = ac
                                break
                    else:
                        cfg.adapter_configs.append(ac)

        try:
            save_config(cfg)
        except Exception:
            log.exception("Failed to save config")
            return web.json_response({"error": "failed to save config"}, status=500)

        log.info("Daemon config updated via API")
        return web.json_response({
            "status": "ok",
            "config": {
                "port": cfg.port,
                "autodetect": cfg.autodetect,
                "render": cfg.render,
                "timing": cfg.timing.to_dict(),
                "adapter_configs": [a.to_dict() for a in cfg.adapter_configs],
            },
        })

    # ── Terminal Management ─────────────────────────

    async def _terminal_rename(self, request: web.Request) -> web.Response:
        """Rename a terminal by id."""
        terminal_id = request.match_info["id"]

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        new_name = body.get("name", "").strip()
        if not new_name:
            return web.json_response({"error": "name is required"}, status=400)

        terminal = self._registry.get_by_id(terminal_id)
        if terminal is None:
            return web.json_response({"error": f"terminal {terminal_id!r} not found"}, status=404)

        terminal.name = new_name
        self._registry.save()
        log.info("Terminal %r renamed to %r", terminal_id, new_name)
        return web.json_response({"status": "ok", "terminal": terminal.to_dict()})

    async def _terminal_delete(self, request: web.Request) -> web.Response:
        """Remove a terminal by id."""
        terminal_id = request.match_info["id"]

        if terminal_id == "default":
            return web.json_response({"error": "cannot delete the default terminal"}, status=400)

        terminal = self._registry.get_by_id(terminal_id)
        if terminal is None:
            return web.json_response({"error": f"terminal {terminal_id!r} not found"}, status=404)

        # Remove from registry
        removed = self._registry.remove(terminal_id)
        if not removed:
            return web.json_response({"error": "failed to remove terminal"}, status=500)

        # Unregister from layout engine
        self._engine.unregister_terminal(terminal_id)

        log.info("Terminal %r deleted", terminal_id)
        return web.json_response({"status": "ok"})

    async def _terminal_set_grid(self, request: web.Request) -> web.Response:
        """Change a terminal's grid size."""
        terminal_id = request.match_info["id"]

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        grid = body.get("grid", "").strip()
        if not grid:
            return web.json_response({"error": "grid is required (e.g. 4x8)"}, status=400)

        # Validate grid format
        try:
            rows, cols = map(int, grid.split("x"))
        except (ValueError, TypeError):
            return web.json_response({"error": f"invalid grid format: {grid!r} (expected e.g. 4x8)"}, status=400)

        # Validate grid dimensions (allows all Stream Deck models)
        if rows < 1 or cols < 1 or rows > 32 or cols > 32:
            return web.json_response({"error": f"invalid grid dimensions: {rows}x{cols}"}, status=400)

        terminal = self._registry.get_by_id(terminal_id)
        if terminal is None:
            return web.json_response({"error": f"terminal {terminal_id!r} not found"}, status=404)

        # Update registry
        terminal.grid = grid
        self._registry.save()
        log.info("Terminal %r grid changed to %s", terminal_id, grid)

        # Re-register in layout engine with new dimensions
        self._engine.register_terminal(terminal_id, rows, cols, terminal.name)

        return web.json_response({"status": "ok", "terminal": terminal.to_dict()})

    # ── Layout Management ───────────────────────────

    async def _layout_delete(self, request: web.Request) -> web.Response:
        """Delete a saved layout file by name."""
        name = request.match_info["name"]
        safe_name = name.replace("/", "_").replace("\\", "_")

        from ..config import LAYOUTS_DIR
        path = LAYOUTS_DIR / f"{safe_name}.yaml"
        if not path.exists():
            return web.json_response({"error": f"layout {name!r} not found"}, status=404)

        try:
            path.unlink()
        except OSError:
            log.exception("Failed to delete layout %s", path)
            return web.json_response({"error": "failed to delete layout file"}, status=500)

        log.info("Layout %r deleted", name)
        return web.json_response({"status": "ok", "name": name})

    async def _layout_rename(self, request: web.Request) -> web.Response:
        """Rename a saved layout file."""
        old_name = request.match_info["name"]

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        new_name = body.get("new_name", "").strip()
        if not new_name:
            return web.json_response({"error": "new_name is required"}, status=400)

        safe_old = old_name.replace("/", "_").replace("\\", "_")
        safe_new = new_name.replace("/", "_").replace("\\", "_")

        from ..config import LAYOUTS_DIR
        old_path = LAYOUTS_DIR / f"{safe_old}.yaml"
        new_path = LAYOUTS_DIR / f"{safe_new}.yaml"

        if not old_path.exists():
            return web.json_response({"error": f"layout {old_name!r} not found"}, status=404)

        if new_path.exists():
            return web.json_response({"error": f"layout {new_name!r} already exists"}, status=409)

        try:
            old_path.rename(new_path)
        except OSError:
            log.exception("Failed to rename layout %s -> %s", old_path, new_path)
            return web.json_response({"error": "failed to rename layout file"}, status=500)

        log.info("Layout %r renamed to %r", old_name, new_name)
        return web.json_response({"status": "ok", "old_name": old_name, "new_name": new_name})

    # ── Adapter Management ──────────────────────────

    async def _list_adapters(self, request: web.Request) -> web.Response:
        """List all registered adapters with status and config schema."""
        from ..core.event_loop import _ADAPTER_REGISTRY
        from ..config import load_config

        cfg = load_config()
        adapter_cfgs = {a.name: a for a in cfg.adapter_configs}

        adapters = []
        for agent_name, adapter_cls in _ADAPTER_REGISTRY.items():
            ac = adapter_cfgs.get(agent_name)
            adapters.append({
                "name": agent_name,
                "enabled": ac.enabled if ac else True,
                "class": adapter_cls.__name__,
                "module": adapter_cls.__module__,
            })

        return web.json_response({"adapters": adapters})

    async def _get_adapter_config(self, request: web.Request) -> web.Response:
        """Get adapter-specific config."""
        adapter_name = request.match_info["name"]

        from ..config import load_config
        cfg = load_config()
        for ac in cfg.adapter_configs:
            if ac.name == adapter_name:
                return web.json_response({
                    "name": ac.name,
                    "enabled": ac.enabled,
                    "config": ac.config,
                })

        return web.json_response({
            "name": adapter_name,
            "enabled": True,
            "config": {},
        })

    async def _post_adapter_config(self, request: web.Request) -> web.Response:
        """Update adapter-specific config."""
        adapter_name = request.match_info["name"]

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        from ..config import load_config, save_config, AdapterConfig
        cfg = load_config()

        # Find or create the adapter config entry
        found = None
        for ac in cfg.adapter_configs:
            if ac.name == adapter_name:
                found = ac
                break

        if found is None:
            found = AdapterConfig(name=adapter_name)
            cfg.adapter_configs.append(found)

        # Merge fields
        if "enabled" in body:
            found.enabled = bool(body["enabled"])
        if "config" in body and isinstance(body["config"], dict):
            found.config.update(body["config"])

        try:
            save_config(cfg)
        except Exception:
            log.exception("Failed to save adapter config for %r", adapter_name)
            return web.json_response({"error": "failed to save config"}, status=500)

        log.info("Adapter config updated for %r", adapter_name)
        return web.json_response({
            "status": "ok",
            "name": adapter_name,
            "enabled": found.enabled,
            "config": found.config,
        })

    async def _get_adapter_appearance(self, request: web.Request) -> web.Response:
        """Get adapter appearance mapping.

        Reads from the adapter's live STATUS_TO_DISPLAY (loaded from
        adapter-specific YAML), falling back to config.yaml adapter_configs.
        """
        adapter_name = request.match_info["name"]

        # 1. Try the adapter's live STATUS_TO_DISPLAY (e.g. claude-code.yaml)
        from ..adapters.claude_code import STATUS_TO_DISPLAY as CC_DISPLAY
        from ..adapters.opencode import STATUS_TO_DISPLAY as OC_DISPLAY
        from ..adapters.openclaw import STATUS_TO_DISPLAY as OW_DISPLAY
        from ..adapters.telegram import STATUS_TO_DISPLAY as TG_DISPLAY

        DISPLAY_MAP = {
            "claude-code": CC_DISPLAY,
            "opencode": OC_DISPLAY,
            "openclaw": OW_DISPLAY,
            "telegram": TG_DISPLAY,
        }

        live = DISPLAY_MAP.get(adapter_name)
        if live:
            return web.json_response({
                "name": adapter_name,
                "appearance": dict(live),
            })

        # 2. Fallback: config.yaml adapter_configs
        from ..config import load_config
        cfg = load_config()
        for ac in cfg.adapter_configs:
            if ac.name == adapter_name:
                return web.json_response({
                    "name": ac.name,
                    "appearance": ac.appearance,
                })

        return web.json_response({
            "name": adapter_name,
            "appearance": {},
        })

    async def _post_adapter_appearance(self, request: web.Request) -> web.Response:
        """Update adapter appearance mapping.

        Saves to both the adapter's live STATUS_TO_DISPLAY (which persists to
        adapter-specific YAML) AND config.yaml for unified visibility.
        """
        adapter_name = request.match_info["name"]

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        appearance = body.get("appearance")
        if appearance is None or not isinstance(appearance, dict):
            return web.json_response({"error": "appearance dict is required"}, status=400)

        # 1. Update the adapter's live STATUS_TO_DISPLAY (persists to YAML)
        from ..adapters.claude_code import STATUS_TO_DISPLAY as CC_DISPLAY, _save_appearance_config as SAVE_CC
        from ..adapters.opencode import STATUS_TO_DISPLAY as OC_DISPLAY

        DISPLAY_MAP = {
            "claude-code": CC_DISPLAY,
            "opencode": OC_DISPLAY,
        }

        live = DISPLAY_MAP.get(adapter_name)
        if live:
            live.clear()
            live.update(appearance)
            # Persist to adapter YAML (claude-code has this, others will follow)
            if adapter_name == "claude-code":
                try:
                    SAVE_CC(appearance)
                except Exception:
                    log.debug("Failed to persist claude-code appearance", exc_info=True)

        # 2. Also persist to config.yaml for unified visibility
        from ..config import load_config, save_config, AdapterConfig
        cfg = load_config()

        found = None
        for ac in cfg.adapter_configs:
            if ac.name == adapter_name:
                found = ac
                break

        if found is None:
            found = AdapterConfig(name=adapter_name)
            cfg.adapter_configs.append(found)

        found.appearance.update(appearance)

        try:
            save_config(cfg)
        except Exception:
            log.exception("Failed to save config appearance for %r", adapter_name)
            return web.json_response({"error": "failed to save config"}, status=500)

        log.info("Adapter appearance updated for %r (%d event mappings)", adapter_name, len(found.appearance))
        return web.json_response({
            "status": "ok",
            "name": adapter_name,
            "appearance": found.appearance,
        })

    # ── Timing ──────────────────────────────────────

    async def _get_timing(self, request: web.Request) -> web.Response:
        """Get global timing config."""
        from ..adapters.claude_code import TIMING as cc_timing
        return web.json_response({"timing": dict(cc_timing)})

    async def _post_timing(self, request: web.Request) -> web.Response:
        """Update global timing config."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid JSON body"}, status=400)

        timing = body.get("timing")
        if timing is None or not isinstance(timing, dict):
            return web.json_response({"error": "timing dict is required"}, status=400)

        # Validate keys are known timing parameters
        known_keys = {
            "thinking_timeout_ms",
            "activity_window_ms",
            "fast_frame_interval_ms",
            "slow_frame_interval_ms",
        }
        for key in timing:
            if key not in known_keys:
                return web.json_response(
                    {"error": f"unknown timing key: {key!r}; valid: {', '.join(sorted(known_keys))}"},
                    status=400,
                )

        # Update in-memory Timing
        from ..adapters.claude_code import TIMING, APPEARANCE_CONFIG_PATH
        import yaml

        for key, val in timing.items():
            TIMING[key] = int(val)

        # Persist to YAML (same file as appearance)
        try:
            APPEARANCE_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Read existing to preserve events
            raw = {}
            if APPEARANCE_CONFIG_PATH.exists():
                raw = yaml.safe_load(APPEARANCE_CONFIG_PATH.read_text(encoding="utf-8")) or {}
            raw["timing"] = dict(TIMING)
            with open(APPEARANCE_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.safe_dump(raw, f, default_flow_style=False, allow_unicode=True, indent=2)
        except Exception:
            log.exception("Failed to persist timing config")
            return web.json_response({"error": "failed to persist timing config"}, status=500)

        log.info("Timing config updated via API: %s", timing)
        return web.json_response({"status": "ok", "timing": dict(TIMING)})


# Default theme fallback (CSS custom properties)
_DEFAULT_THEME_CSS = """:root {
  --bg: #0f0f1a;
  --panel: #1a1a2e;
  --surface: #16213e;
  --border: #2a2a4a;
  --text: #e0e0e0;
  --text-dim: #888;
  --accent: #22c55e;
  --danger: #ef4444;
  --warn: #eab308;
  --info: #3b82f6;
}"""
