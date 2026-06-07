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
    ) -> None:
        self._port = port
        self._render = render
        self._device_index = device_index
        self._autodetect = autodetect
        self._demo = demo

        self._bus = MessageBus()
        self._engine = LayoutEngine()
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
        log.info("VibeDeck supervisor starting (port=%d, render=%s, demo=%s)",
                 self._port, self._render, self._demo)

        # 1. Start Web Server (first so we can show status early)
        from ..web.server import VibeDeckWebServer
        self._web_server = VibeDeckWebServer(self._engine, port=self._port)
        await self._web_server.start()

        # 2. Demo mode: populate sample widgets
        if self._demo:
            self._setup_demo_widgets()

        # 3. Start Connectors
        if self._autodetect:
            await self._start_connectors()

        # 4. Start Render Engine
        await self._start_renderer()

        # 5. Start frame push loop
        self._tasks.append(asyncio.create_task(self._frame_push_loop()))

        # 6. Start message consumer loop
        self._tasks.append(asyncio.create_task(self._message_consumer()))

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
            ws = WidgetState(
                id=wid,
                type=wtype,
                display=DisplayState(icon=icon, color=color, animation=anim, label=label, badge=meta.get("unread")),
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
        if self._render == "hardware":
            from ..render.hardware import HardwareRenderer
            self._renderer = HardwareRenderer(device_index=self._device_index)
            if self._renderer.open():
                log.info("Hardware renderer started: %s", self._renderer.deck_type)
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
        """Periodically push LayoutFrames to all render targets."""
        interval = 0.1 if self._render == "hardware" else 0.5
        try:
            while not self._shutdown_event.is_set():
                await self._web_server.broadcast_frame()
                # If hardware, also push to physical deck
                if self._render == "hardware" and hasattr(self, '_renderer'):
                    if hasattr(self._renderer, 'render_frame'):
                        self._renderer.render_frame(self._engine.frame)
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

        if msg.type == MessageType.AGENT_ONLINE:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"

            # Create a placeholder WidgetState
            ds = DisplayState(icon="🆕", color="#64748b", animation="pulse", label="Starting")
            ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds,
                             meta={"agent": agent_name, "pid": msg.payload.get("pid")})
            self._engine.update_widget(ws)

        elif msg.type == MessageType.AGENT_OFFLINE:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"
            existing = self._engine.frame.widgets.get(widget_id)
            if existing:
                existing.update_display(icon="⚫", color="#374151", animation="none", label="Offline")

        elif msg.type == MessageType.WIDGET_STATE_UPDATE:
            agent_name = msg.payload.get("agent_name", "unknown")
            data = msg.payload.get("data", {})
            widget_id = f"{agent_name}-auto"

            status = data.get("status", "offline")
            display_map = DISPLAY_MAP.get(agent_name, {})
            display_cfg = display_map.get(status, {"icon": "⚫", "color": "#374151", "animation": "none", "label": status})

            existing = self._engine.frame.widgets.get(widget_id)
            if existing:
                existing.update_display(**display_cfg)
                existing.meta.update(data)
            else:
                ds = DisplayState(**display_cfg)
                if "badge" in data:
                    ds.badge = str(data["badge"])
                ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds, meta=data)
                self._engine.update_widget(ws)

        elif msg.type == MessageType.KEY_PRESSED:
            key = msg.payload.get("key", -1)
            log.debug("Key %d pressed", key)

        elif msg.type == MessageType.WIDGET_REMOVED:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"
            self._engine.frame.remove_widget(widget_id)

    async def _shutdown(self) -> None:
        """Gracefully shut down all components."""
        log.info("Shutting down...")

        # Cancel all background tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Stop web server
        if hasattr(self, '_web_server'):
            await self._web_server.stop()

        # Close hardware
        if hasattr(self, '_renderer') and hasattr(self._renderer, 'close'):
            self._renderer.close()

        log.info("Shutdown complete")


async def run_supervisor(
    port: int = 9734,
    render: str = "sim",
    device_index: int = 0,
    autodetect: bool = True,
    demo: bool = False,
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
