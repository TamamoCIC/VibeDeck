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
        self._thinking_timer: asyncio.Task | None = None
        self._thinking_timer_lock = asyncio.Lock()
        self._last_hook_activity: float = 0.0  # monotonic timestamp of last hook event

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
            self._engine, self._registry, port=self._port, expose=self._expose, bus=self._bus
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
        """Populate Claude Code widget on all registered terminals.

        Only creates the Claude Code agent widget at key 0 so the
        Stream Deck / phone simulator shows live hook-driven status:
          SessionStart       → 🐙 green crawl  "Running"
          UserPromptSubmit   → 🐙 yellow blink "Waiting"
          PreToolUse         → 🐙 green crawl  tool name
          PostToolUse        → 🐙 green crawl  tool name
          Stop               → 🐙 dim green    "Idle"
        """
        from .types import DisplayState, WidgetState, WidgetType

        for terminal_id in self._engine.list_terminals():
            frame = self._engine.get_frame(terminal_id)
            if frame is None:
                continue

            ws = WidgetState(
                id="claude-code-auto",
                type=WidgetType.AGENT,
                display=DisplayState(icon="🐙", color="#22c55e", animation="crawl", label="Running"),
                meta={"agent": "Claude Code", "status": "running"},
            )
            frame.place_widget(ws, 0)
            log.debug("Claude Code widget placed at key 0 on terminal %s (grid=%dx%d)",
                      terminal_id, frame.rows, frame.cols)

        log.info("Claude Code widget ready on %d terminal(s)", len(self._engine.list_terminals()))

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
        """Adaptive frame-rate push loop.

        - 30 fps while agents are active (hook events within last 3s)
        -  1 fps when all agents are idle
        """
        import time as _time
        FAST_INTERVAL = 1.0 / 30   # ~33ms
        SLOW_INTERVAL = 1.0        # 1s when idle
        ACTIVITY_WINDOW = 3.0      # seconds

        try:
            while not self._shutdown_event.is_set():
                now = _time.monotonic()
                active = (now - self._last_hook_activity) < ACTIVITY_WINDOW
                interval = FAST_INTERVAL if active else SLOW_INTERVAL

                for terminal_id in self._engine.list_terminals():
                    frame = self._engine.get_frame(terminal_id)
                    if frame is None:
                        continue
                    await self._web_server.broadcast_frame(terminal_id, frame)
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
                    # Persist layout after every state change — ensures
                    # survival across crashes, not just clean shutdowns
                    if msg.type in (MessageType.WIDGET_STATE_UPDATE,
                                    MessageType.AGENT_ONLINE,
                                    MessageType.AGENT_OFFLINE):
                        try:
                            self._engine.autosave_all()
                        except Exception:
                            pass
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
            pid = msg.payload.get("pid", 0)
            # PID-based widget ID so each agent instance gets its own widget
            widget_id = f"{agent_name}-{pid}" if pid else f"{agent_name}-auto"

            # Create a placeholder WidgetState
            ds = DisplayState(icon="🆕", color="#64748b", animation="pulse", label="Starting")
            ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds,
                             meta={"agent": agent_name, "pid": pid})
            self._engine.upsert_widget(ws, terminal_id)

        elif msg.type == MessageType.AGENT_OFFLINE:
            agent_name = msg.payload.get("agent_name", "unknown")
            pid = msg.payload.get("pid", 0)
            widget_id = f"{agent_name}-{pid}" if pid else f"{agent_name}-auto"
            frame = self._engine.get_frame(terminal_id)
            if frame:
                existing = frame.widgets.get(widget_id)
                if existing:
                    existing.update_display(icon="⚫", color="#374151", animation="none", label="Offline")

        elif msg.type == MessageType.WIDGET_STATE_UPDATE:
            agent_name = msg.payload.get("agent_name", "unknown")
            data = msg.payload.get("data", {})
            widget_id = msg.payload.get("widget_id", f"{agent_name}-auto")

            _hook_event = data.get("hook_event_name", "")
            _tool_name = data.get("tool_name", "")
            _status = data.get("status", "")
            log.info("[HOOK→UI] agent=%s widget=%s hook_event=%s tool=%s status=%s",
                     agent_name, widget_id, _hook_event, _tool_name, _status)

            # Use display from adapter if provided, else resolve from event data
            display_raw = msg.payload.get("display")
            if display_raw:
                try:
                    ds = DisplayState(**display_raw)
                except Exception:
                    ds = self._resolve_display(agent_name, data, DISPLAY_MAP)
            else:
                ds = self._resolve_display(agent_name, data, DISPLAY_MAP)

            # Don't let adapter heartbeats overwrite hook-driven fine-grained status.
            _is_hook_event = bool(_hook_event)
            _is_adapter_heartbeat = bool(not _hook_event and _status in ("running", "idle"))

            # Apply tool name to label for PreToolUse / PostToolUse events
            if _hook_event in ("PreToolUse", "PostToolUse") and _tool_name:
                ds.label = _tool_name[:12]  # max 12 chars
                log.info("[HOOK→UI] label override → %s", ds.label)

            # Apply session status label from hook events that carry it
            if _hook_event == "Stop" and data.get("stop_hook_active"):
                ds.label = "Paused"

            log.info("[HOOK→UI] display resolved → icon=%s color=%s anim=%s label=%s",
                     ds.icon, ds.color, ds.animation, ds.label)

            # ── Targeted widget update ──────────────────────────
            # Pre-refactor: looped over ALL terminals and auto-created
            # widgets everywhere, duplicating state on every device.
            # Now we UPDATE existing widgets in-place on terminals
            # that already have them, and CREATE only on the single
            # terminal the event was routed to.
            import time as _time

            updated_tids: list[str] = []
            for tid in self._engine.list_terminals():
                frame = self._engine.get_frame(tid)
                if frame is None:
                    continue
                existing = frame.widgets.get(widget_id)
                if existing is None:
                    continue  # don't auto-create — only update existing

                # Skip adapter heartbeat when hook events have set the state.
                if _is_adapter_heartbeat and hasattr(existing, '_last_hook_ts'):
                    log.debug("[HOOK→UI] skipping heartbeat for %s (hook events are authoritative)", widget_id)
                    continue

                now = _time.time()

                # ── Waiting-state minimum display duration ──────────
                MIN_WAITING_DISPLAY_S = 0.6

                if _hook_event == "UserPromptSubmit":
                    existing._waiting_since = now

                if (_hook_event in ("PreToolUse", "PostToolUse")
                        and hasattr(existing, '_waiting_since')
                        and (now - existing._waiting_since) < MIN_WAITING_DISPLAY_S):
                    existing._pending_display = ds
                    existing._pending_meta = data
                    remaining = MIN_WAITING_DISPLAY_S - (now - existing._waiting_since)
                    log.info("[HOOK→UI] deferring %s display for %.2fs (Waiting grace period)",
                             _hook_event, remaining)
                    self._schedule_deferred_display(tid, widget_id, remaining)
                    updated_tids.append(tid)
                    continue

                if _hook_event == "Stop":
                    if hasattr(existing, '_waiting_since'):
                        del existing._waiting_since
                existing.display = ds
                existing.meta.update(data)
                if _is_hook_event:
                    existing._last_hook_ts = _time.time()
                updated_tids.append(tid)
                log.debug("[HOOK→UI] widget %s UPDATED on terminal %s", widget_id, tid)

            # Auto-create the widget on the terminal the event targets
            # (only if it didn't already exist there)
            if terminal_id not in updated_tids:
                frame = self._engine.get_frame(terminal_id)
                if frame is not None and widget_id not in frame.widgets:
                    if "badge" in data:
                        ds.badge = str(data["badge"])
                    ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds, meta=data)
                    if _is_hook_event:
                        ws._last_hook_ts = _time.time()
                    self._engine.upsert_widget(ws, terminal_id)
                    log.info("[HOOK→UI] new widget %s CREATED on terminal %s", widget_id, terminal_id)

        elif msg.type == MessageType.KEY_PRESSED:
            key = msg.payload.get("key", -1)
            log.info("[KEY] Key %d pressed on terminal %r", key, terminal_id)
            frame = self._engine.get_frame(terminal_id)
            if frame:
                ws = frame.widget_at_key(key)
                if ws:
                    log.info("[KEY] Widget %s at key %d — current state: icon=%s label=%s",
                             ws.id, key, ws.display.icon, ws.display.label)
                else:
                    log.info("[KEY] No widget at key %d on terminal %r", key, terminal_id)

        elif msg.type == MessageType.WIDGET_REMOVED:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = f"{agent_name}-auto"
            self._engine.remove_widget(widget_id, terminal_id)

        # ── Reset thinking timer + activity timestamp ──────
        # Only real hook events (those carrying hook_event_name) indicate
        # that Claude is actively working.  Adapter heartbeats are periodic
        # liveness checks (status="running" with no hook_event_name) and
        # must not reset the thinking timer — otherwise on cold start the
        # heartbeat resets it, 0.8s of silence fires it, and the widget
        # flips to "Thinking" even though Claude may be idle.
        _is_real_activity = False
        if msg.type == MessageType.AGENT_ONLINE:
            _is_real_activity = True
        elif msg.type == MessageType.WIDGET_STATE_UPDATE:
            _data = msg.payload.get("data", {})
            _is_real_activity = bool(_data.get("hook_event_name", ""))
        if _is_real_activity:
            import time as _time
            self._last_hook_activity = _time.monotonic()
            asyncio.create_task(self._reset_thinking_timer(terminal_id))

    def _resolve_display(
        self, agent_name: str, data: dict, display_map: dict
    ) -> "DisplayState":
        """Resolve a DisplayState from event data.

        Priority:
          1. hook_event_name → lookup in display_map[agent_name]
          2. status          → lookup in display_map[agent_name]
          3. Fallback offline display
        """
        from .types import DisplayState

        adapter_map = display_map.get(agent_name, {})
        fallback = {"icon": "⚫", "color": "#374151", "animation": "none", "label": "offline"}

        # 1. Try hook event name
        hook_event = data.get("hook_event_name", "")
        if hook_event and hook_event in adapter_map:
            return DisplayState(**adapter_map[hook_event])

        # 2. Try status field
        status = data.get("status", "")
        if status and status in adapter_map:
            return DisplayState(**adapter_map[status])

        # 3. Fallback
        return DisplayState(**fallback)

    def _schedule_deferred_display(
        self, terminal_id: str, widget_id: str, delay_s: float
    ) -> None:
        """Schedule a deferred display update after `delay_s` seconds.

        Used to enforce a minimum display duration for transient states
        (e.g. UserPromptSubmit → Waiting) that would otherwise be
        overwritten by the next tool event before the user sees them.
        """
        import asyncio

        async def _apply():
            await asyncio.sleep(delay_s)
            frame = self._engine.get_frame(terminal_id)
            if frame is None:
                return
            existing = frame.widgets.get(widget_id)
            if existing is None:
                return
            pending = getattr(existing, '_pending_display', None)
            if pending is None:
                return
            existing.display = pending
            pending_meta = getattr(existing, '_pending_meta', None) or {}
            existing.meta.update(pending_meta)
            del existing._pending_display
            existing._pending_meta = {}
            log.info("[HOOK→UI] deferred display applied for %s → label=%s",
                     widget_id, pending.label)

        self._tasks.append(asyncio.create_task(_apply()))

    # ── Thinking / Writing timeout detection ─────────

    THINKING_TIMEOUT_S = 0.8  # seconds of silence before "Thinking"

    async def _reset_thinking_timer(self, terminal_id: str) -> None:
        """Reset the inactivity timer.  If no new hook event arrives for
        THINKING_TIMEOUT_S seconds, apply "Thinking" state to widgets on
        ALL terminals and force-push frames so every connected device
        sees it."""
        import asyncio

        async with self._thinking_timer_lock:
            if self._thinking_timer and not self._thinking_timer.done():
                self._thinking_timer.cancel()
                try:
                    await self._thinking_timer
                except asyncio.CancelledError:
                    pass

            async def _fire_thinking():
                await asyncio.sleep(self.THINKING_TIMEOUT_S)
                from .types import DisplayState
                from ..adapters.claude_code import STATUS_TO_DISPLAY
                thinking_cfg = STATUS_TO_DISPLAY.get("thinking", {"icon": "🐙", "color": "#7c3aed", "animation": "pulse", "label": "Thinking"})
                # Update all terminals that have agent widgets — targeted,
                # does NOT auto-create widgets on terminals that don't
                # already have them.
                for tid in self._engine.list_terminals():
                    frame = self._engine.get_frame(tid)
                    if frame is None:
                        continue
                    pushed = False
                    for widget_id, ws in list(frame.widgets.items()):
                        current_label = ws.display.label
                        current_anim = ws.display.animation.value if hasattr(ws.display.animation, 'value') else str(ws.display.animation)
                        if current_label in ("Idle", "Offline", "Error", "Sub done"):
                            continue
                        if current_anim in ("blink",):
                            continue
                        ds = DisplayState(**thinking_cfg)
                        ws.display = ds
                        pushed = True
                        log.info("[THINKING] widget %s → Thinking (%.1fs silence on terminal %s)",
                                 widget_id, self.THINKING_TIMEOUT_S, tid)
                    # Force immediate frame push for terminals that changed
                    if pushed and hasattr(self, '_web_server'):
                        await self._web_server.broadcast_frame(tid, frame)

            self._thinking_timer = asyncio.create_task(_fire_thinking())

    async def _shutdown(self) -> None:
        """Gracefully shut down all components."""
        log.info("Shutting down...")

        # 0. Stop AdapterManager
        if hasattr(self, '_adapter_manager'):
            try:
                await self._adapter_manager.stop()
            except Exception:
                log.debug("AdapterManager stop error (ignored)", exc_info=True)

        # 0.5 Persist current layout state before tearing down
        try:
            self._engine.autosave_all()
            log.info("Layout state autosaved on shutdown")
        except Exception:
            log.debug("Autosave error (ignored)", exc_info=True)

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
