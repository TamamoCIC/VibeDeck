"""
VibeDeck Core Supervisor — asyncio event loop and wiring.

Orchestrates all layers: starts Connectors, routes messages through the
MessageBus to the LayoutEngine, pushes frames to Render targets.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from .message_bus import Message, MessageBus, MessageType
from .layout import LayoutEngine

log = logging.getLogger("vibe_deck.core.supervisor")

# Built-in adapter class registry (populated by _register_adapters)
_ADAPTER_REGISTRY: dict[str, type] = {}


def _register_adapters() -> None:
    """Register built-in adapter classes (called at import time)."""
    from ..adapters.claude_code import ClaudeCodeAdapter
    from ..adapters.opencode import OpenCodeAdapter
    from ..adapters.openclaw import OpenClawAdapter
    from ..adapters.telegram import TelegramAdapter
    from .adapter_manager import register_adapter

    register_adapter("claude-code", ClaudeCodeAdapter)
    register_adapter("opencode", OpenCodeAdapter)
    register_adapter("openclaw", OpenClawAdapter)
    register_adapter("telegram", TelegramAdapter)

    # Also populate the module-level registry
    _ADAPTER_REGISTRY["claude-code"] = ClaudeCodeAdapter
    _ADAPTER_REGISTRY["opencode"] = OpenCodeAdapter
    _ADAPTER_REGISTRY["openclaw"] = OpenClawAdapter
    _ADAPTER_REGISTRY["telegram"] = TelegramAdapter


def _parse_grid(grid: str) -> tuple[int, int]:
    """Parse a grid string like '4x8' into (rows, cols)."""
    parts = grid.split("x")
    if len(parts) == 2:
        return int(parts[0]), int(parts[1])
    return 4, 8  # default


class VibeDeckSupervisor:
    """
    Central supervisor that owns the event loop and wires all components.

    Lifecycle:
      supervisor = VibeDeckSupervisor(config)
      await supervisor.start()    # blocks until SIGTERM/SIGINT
    """

    def __init__(
        self,
        *,
        port: int = 9734,
        render: str = "sim",
        device_index: int = 0,
        autodetect: bool = True,
        demo: bool = False,
        expose: bool = False,
        no_physical: bool = False,
    ) -> None:
        self._port = port
        self._render = render
        self._device_index = device_index
        self._autodetect = autodetect
        self._demo = demo
        self._expose = expose
        self._no_physical = no_physical

        self._bus = MessageBus()
        self._engine = LayoutEngine()
        self._registry = None  # TerminalRegistry, lazy-loaded
        self._tasks: list[asyncio.Task] = []
        self._shutdown_event = asyncio.Event()

    # ── Public API ─────────────────────────────────

    @property
    def bus(self) -> MessageBus:
        return self._bus

    @property
    def engine(self) -> LayoutEngine:
        return self._engine

    async def start(self) -> None:
        """Start all components and run until shutdown."""
        log.info("VibeDeck supervisor starting (port=%d, render=%s, demo=%s, expose=%s)",
                 self._port, self._render, self._demo, self._expose)

        # 0. Load Terminal Registry and sync with LayoutEngine
        from .terminal_registry import TerminalRegistry
        self._registry = TerminalRegistry()
        self._registry.load()

        # Sync all registered terminals into the LayoutEngine
        for t in self._registry.list_all():
            rows, cols = _parse_grid(t.grid)
            self._engine.register_terminal(t.id, rows, cols, t.name)
            log.debug("Synced terminal %r (id=%s, grid=%s)", t.name, t.id, t.grid)

        # 1. Start Web Server (first so we can show status early)
        from ..web.server import VibeDeckWebServer
        self._web_server = VibeDeckWebServer(
            self._engine, self._registry, port=self._port, expose=self._expose
        )
        await self._web_server.start()

        # 2. Register built-in adapters
        _register_adapters()

        # 3. Start Adapter Manager
        from .adapter_manager import AdapterManager
        self._adapter_manager = AdapterManager(self._bus)
        await self._adapter_manager.start()
        # Try starting telegram if configured
        tg_cls = _ADAPTER_REGISTRY.get("telegram")
        if tg_cls and tg_cls.is_configured():
            await self._adapter_manager.start_adapter("telegram")

        # 4. Start Connectors (process scanner + file watcher)
        if self._autodetect:
            await self._start_connectors()

        # 5. Demo mode: populate sample widgets (only if --demo)
        if self._demo:
            self._setup_demo_widgets()

        # 6. Start Render Engine
        await self._start_renderer()

        # 7. Start frame push loop
        self._tasks.append(asyncio.create_task(self._frame_push_loop()))

        # 8. Start message consumer loop
        self._tasks.append(asyncio.create_task(self._message_consumer()))

        log.info("Supervisor ready — waiting for agents...")

        # Wait for shutdown signal
        try:
            await self._shutdown_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            await self._shutdown()

    async def shutdown(self) -> None:
        """Signal the supervisor to shut down gracefully."""
        self._shutdown_event.set()

    # ── Internals ──────────────────────────────────

    def _setup_demo_widgets(self) -> None:
        """Populate sample widgets so the simulator isn't empty."""
        from .types import DisplayState, WidgetState, WidgetType

        demos = [
            ("claude-code-main", "🐙", "#22c55e", "crawl", "Running", WidgetType.AGENT,
             {"agent": "Claude Code", "status": "running"}),
            ("opencode-main", "🦊", "#22c55e", "crawl", "Busy", WidgetType.AGENT,
             {"agent": "OpenCode", "status": "busy"}),
            ("openclaw-main", "🦞", "#166534", "none", "Idle", WidgetType.AGENT,
             {"agent": "OpenClaw", "status": "completed"}),
            ("telegram-main", "💬", "#6366f1", "pulse", "3 new", WidgetType.AGENT,
             {"agent": "Telegram", "status": "unread", "unread": 3}),
        ]

        for i, (wid, icon, color, anim, label, wtype, meta) in enumerate(demos):
            badge_val = str(meta["unread"]) if meta.get("unread") else None
            ws = WidgetState(
                id=wid,
                type=wtype,
                display=DisplayState(icon=icon, color=color, animation=anim, label=label, badge=badge_val),
                meta=meta,
            )
            self._engine.frame.place_widget(ws, i)

        # Add a command widget
        cmd = WidgetState(
            id="shortcut-terminal",
            type=WidgetType.COMMAND,
            display=DisplayState(icon="🖥️", color="#1e293b", animation="none", label="Terminal"),
            meta={"command": "gnome-terminal"},
        )
        self._engine.frame.place_widget(cmd, 28)

        # Add info widget
        info = WidgetState(
            id="info-time",
            type=WidgetType.SYSTEM,
            display=DisplayState(icon="⏰", color="#1e1e2e", animation="none", label="Time"),
            meta={"source": "system:clock"},
        )
        self._engine.frame.place_widget(info, 29)

        log.info("Demo mode: 6 sample widgets loaded")

    async def _start_connectors(self) -> None:
        """Start process scanner and file watcher."""
        from ..connectors.process_scanner import ProcessScanner
        from ..connectors.file_watcher import FileWatcher
        from ..config import load_config

        config = load_config()

        scanner = ProcessScanner(self._bus, patterns=config.agent_patterns)
        watcher = FileWatcher(self._bus)

        self._tasks.append(asyncio.create_task(scanner.start()))
        self._tasks.append(asyncio.create_task(watcher.start()))

        log.info("Connectors started (scanner + file watcher)")

    async def _start_renderer(self) -> None:
        """Start the appropriate render target."""
        if self._no_physical:
            log.info("Physical terminal disabled (--no-physical)")
            self._render = "sim"

        if self._render == "hardware":
            from ..render.hardware import HardwareRenderer
            self._renderer = HardwareRenderer(device_index=self._device_index)
            if self._renderer.open():
                log.info("Hardware renderer started: %s (%s)", self._renderer.deck_type, self._renderer.grid_name)
                # Hot-plug monitor
                self._tasks.append(asyncio.create_task(self._renderer.hotplug_loop()))
            else:
                log.warning("Hardware renderer failed to open. Falling back to sim.")
                self._render = "sim"

        if self._render == "sim":
            from ..render.sim import SimRenderer
            self._renderer = SimRenderer()
            log.info("Sim renderer ready")

    async def _frame_push_loop(self) -> None:
        """Periodically push LayoutFrames to all render targets.

        Iterates all registered terminals and pushes each one's frame
        to the appropriate render target.
        """
        interval = 0.1 if self._render == "hardware" else 0.5
        try:
            while not self._shutdown_event.is_set():
                for terminal_id in self._engine.list_terminals():
                    frame = self._engine.get_frame(terminal_id)
                    if frame is None:
                        continue
                    # Push to web (SSE broadcast per terminal)
                    await self._web_server.broadcast_frame(terminal_id, frame)
                    # Push to hardware if this is the physical terminal
                    if self._render == "hardware" and hasattr(self, '_renderer'):
                        if hasattr(self._renderer, 'render_frame'):
                            self._renderer.render_frame(frame)
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _message_consumer(self) -> None:
        """Consume messages from the bus and update the layout."""
        q = self._bus.subscribe("supervisor")
        try:
            while not self._shutdown_event.is_set():
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=1.0)
                    await self._handle_message(msg)
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass
        finally:
            self._bus.unsubscribe("supervisor")

    async def _handle_message(self, msg: Message) -> None:
        """Route a message to the appropriate handler."""
        from .types import DisplayState, WidgetState, WidgetType
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

        terminal_id = msg.payload.get("terminal_id", "default")

        if msg.type == MessageType.AGENT_ONLINE:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"

            # Create a placeholder WidgetState
            ds = DisplayState(icon="🆕", color="#64748b", animation="pulse", label="Starting")
            ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds,
                             meta={"agent": agent_name, "pid": msg.payload.get("pid")})
            self._engine.update_widget(ws, terminal_id)

        elif msg.type == MessageType.AGENT_OFFLINE:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"
            frame = self._engine.get_frame(terminal_id)
            if frame:
                existing = frame.widgets.get(widget_id)
                if existing:
                    existing.update_display(icon="⚫", color="#374151", animation="none", label="Offline")

        elif msg.type == MessageType.WIDGET_STATE_UPDATE:
            agent_name = msg.payload.get("agent_name", "unknown")
            data = msg.payload.get("data", {})
            widget_id = msg.payload.get("widget_id", f"{agent_name}-auto")

            # Use display from adapter if provided, else fall back to built-in mapping
            display_raw = msg.payload.get("display")
            if display_raw:
                try:
                    ds = DisplayState(**display_raw)
                except Exception:
                    display_cfg = DISPLAY_MAP.get(agent_name, {}).get(
                        data.get("status", "offline"),
                        {"icon": "⚫", "color": "#374151", "animation": "none", "label": "offline"},
                    )
                    ds = DisplayState(**display_cfg)
            else:
                status = data.get("status", "offline")
                display_map = DISPLAY_MAP.get(agent_name, {})
                display_cfg = display_map.get(status, {"icon": "⚫", "color": "#374151", "animation": "none", "label": status})
                ds = DisplayState(**display_cfg)

            frame = self._engine.get_frame(terminal_id)
            if frame:
                existing = frame.widgets.get(widget_id)
                if existing:
                    existing.display = ds
                    existing.meta.update(data)
                else:
                    if "badge" in data:
                        ds.badge = str(data["badge"])
                    ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds, meta=data)
                    self._engine.update_widget(ws, terminal_id)

        elif msg.type == MessageType.KEY_PRESSED:
            key = msg.payload.get("key", -1)
            log.debug("Key %d pressed on terminal %r", key, terminal_id)

        elif msg.type == MessageType.WIDGET_REMOVED:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"
            self._engine.remove_widget(widget_id, terminal_id)

    async def _shutdown(self) -> None:
        """Gracefully shut down all components."""
        log.info("Shutting down...")

        # 0. Stop AdapterManager
        if hasattr(self, '_adapter_manager'):
            try:
                await self._adapter_manager.stop()
            except Exception:
                log.debug("AdapterManager stop error (ignored)", exc_info=True)

        # 1. Close SSE connections first (prevents aiohttp InvalidStateError on Windows)
        if hasattr(self, '_web_server'):
            self._web_server.close_sse_connections()

        # 2. Cancel all background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # 3. Stop web server
        if hasattr(self, '_web_server'):
            try:
                await self._web_server.stop()
            except Exception:
                log.debug("Web server stop error (ignored)", exc_info=True)

        # 4. Close hardware
        if hasattr(self, '_renderer') and hasattr(self._renderer, 'close'):
            try:
                self._renderer.close()
            except Exception:
                pass

        log.info("Shutdown complete")


async def run_supervisor(
    port: int = 9734,
    render: str = "sim",
    device_index: int = 0,
    autodetect: bool = True,
    demo: bool = False,
    expose: bool = False,
    no_physical: bool = False,
) -> None:
    """
    Entry point: create and run the VibeDeck supervisor.

    Handles SIGINT/SIGTERM for graceful shutdown.
    """
    supervisor = VibeDeckSupervisor(
        port=port,
        render=render,
        device_index=device_index,
        autodetect=autodetect,
        demo=demo,
        expose=expose,
        no_physical=no_physical,
    )

    loop = asyncio.get_running_loop()

    # Register signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(supervisor.shutdown()))
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    await supervisor.start()
