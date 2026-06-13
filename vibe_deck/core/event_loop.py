"""
VibeDeck Core Supervisor — asyncio event loop and wiring.

Orchestrates all layers: starts Connectors, routes messages through the
MessageBus to the LayoutEngine, pushes frames to Render targets.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from pathlib import Path

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
        expose: bool = False,
        no_physical: bool = False,
    ) -> None:
        self._port = port
        self._render = render
        self._device_index = device_index
        self._autodetect = autodetect
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
        self._last_autosave: float = 0.0  # monotonic timestamp of last autosave (debounce)
        self._next_thinking_fire_at: float = 0.0  # monotonic: when the thinking timer will fire
        self._scanner = None     # ProcessScanner instance (set by _start_connectors)
        self._watcher = None     # FileWatcher instance (set by _start_connectors)

    # ── Public API ─────────────────────────────────

    @property
    def bus(self) -> MessageBus:
        return self._bus

    @property
    def engine(self) -> LayoutEngine:
        return self._engine

    async def start(self) -> None:
        """Start all components and run until shutdown."""
        log.info("VibeDeck supervisor starting (port=%d, render=%s, expose=%s)",
                 self._port, self._render, self._expose)

        # 0. Load Terminal Registry and sync with LayoutEngine
        from .terminal_registry import TerminalRegistry
        self._registry = TerminalRegistry()
        self._registry.load()

        # Sync all registered terminals into the LayoutEngine
        for t in self._registry.list_all():
            rows, cols = _parse_grid(t.grid)
            self._engine.register_terminal(t.id, rows, cols, t.name)
            log.debug("Synced terminal %r (id=%s, grid=%s)", t.name, t.id, t.grid)

        # Restore pool from autosave
        self._engine.pool_restore()

        # 0a. Create PIL Renderer (shared for all terminals)
        from ..render.renderer import PILRenderer
        self._renderer = PILRenderer()
        self._anim_engine = self._renderer._anim  # for web server SSE / compat

        # 1. Start Web Server (first so we can show status early)
        from ..web.server import VibeDeckWebServer
        self._web_server = VibeDeckWebServer(
            self._engine, self._registry, port=self._port, expose=self._expose, bus=self._bus,
            shutdown_cb=lambda: self._shutdown_event.set(),
            animation_engine=self._anim_engine,
            renderer=self._renderer,
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

        # 5. Start Render Engine
        await self._start_renderer()

        # 6. Start frame push loop
        self._tasks.append(asyncio.create_task(self._frame_push_loop()))

        # 7. Start message consumer loop
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

    async def _start_connectors(self) -> None:
        """Start process scanner and file watcher."""
        from ..connectors.process_scanner import ProcessScanner
        from ..connectors.file_watcher import FileWatcher
        from ..config import load_config

        config = load_config()

        self._scanner = ProcessScanner(self._bus, patterns=config.agent_patterns)
        self._watcher = FileWatcher(self._bus)

        self._tasks.append(asyncio.create_task(self._scanner.start()))
        self._tasks.append(asyncio.create_task(self._watcher.start()))

        log.info("Connectors started (scanner + file watcher)")

    async def _start_renderer(self) -> None:
        """Start the appropriate transport target."""
        if self._no_physical:
            log.info("Physical terminal disabled (--no-physical)")
            self._render = "sim"

        if self._render == "hardware":
            from ..transport.hid import HIDTransport

            # Thread-safe key callback → message bus bridge
            _loop = asyncio.get_running_loop()

            def _on_key(key_index: int, pressed: bool) -> None:
                tid = self._engine.list_terminals()[0] if self._engine.list_terminals() else "default"
                asyncio.run_coroutine_threadsafe(
                    self._bus.publish(Message(
                        type=MessageType.KEY_PRESSED,
                        source="hardware",
                        payload={"key": key_index, "pressed": pressed, "terminal_id": tid},
                    )),
                    _loop,
                )

            self._hid = HIDTransport(
                device_index=self._device_index,
                key_callback=_on_key,
            )
            if self._hid.open():
                log.info("HID transport started: %s (%d keys)",
                         self._hid.deck_type, self._hid.key_count)
                # Hot-plug monitor
                self._tasks.append(asyncio.create_task(self._hid.hotplug_loop()))
            else:
                log.warning("HID transport failed to open. Falling back to sim-only.")
                self._render = "sim"

        if self._render == "sim":
            log.info("Web transport ready (PILRenderer → SSE)")

    async def _frame_push_loop(self) -> None:
        """Adaptive frame-rate push loop.

        Frame rate and activity window are controlled by the Claude Code
        adapter's timing config (``TIMING`` dict, overridable via YAML).

        Each frame includes ``_debug`` timing info so the Web UI can
        surface real-time latency diagnostics (hook age, thinking timer,
        frame rate).
        """
        import time as _time
        from ..adapters.claude_code import TIMING

        fast_ms = TIMING.get("fast_frame_interval_ms", 33)
        slow_ms = TIMING.get("slow_frame_interval_ms", 1000)
        activity_ms = TIMING.get("activity_window_ms", 3000)

        FAST_INTERVAL = fast_ms / 1000.0
        SLOW_INTERVAL = slow_ms / 1000.0
        ACTIVITY_WINDOW = activity_ms / 1000.0

        try:
            while not self._shutdown_event.is_set():
                now = _time.monotonic()
                active = (now - self._last_hook_activity) < ACTIVITY_WINDOW
                interval = FAST_INTERVAL if active else SLOW_INTERVAL

                hook_age_ms = (now - self._last_hook_activity) * 1000
                thinking_ms = max(0, (self._next_thinking_fire_at - now) * 1000)
                debug_info = {
                    "hook_age_ms": round(hook_age_ms, 1),
                    "frame_rate": "fast" if active else "slow",
                    "interval_ms": round(interval * 1000, 1),
                    "thinking_ms": round(thinking_ms, 0) if thinking_ms > 0 else 0,
                }

                for terminal_id in self._engine.list_terminals():
                    layout_frame = self._engine.get_frame(terminal_id)
                    if layout_frame is None:
                        continue

                    # 1. Render: LayoutFrame → StandardFrame (device-independent)
                    sf = self._renderer.render(layout_frame)

                    # 2. Web: StandardFrame → SSE (all terminals)
                    from ..transport.web import web_frame as _web_frame
                    await self._web_server.broadcast_frame(
                        terminal_id,
                        frame=_web_frame(sf),
                        layout_frame=layout_frame,
                        debug_info=debug_info,
                    )

                    # 3. HID: StandardFrame → Stream Deck (physical only)
                    if self._render == "hardware" and hasattr(self, '_hid'):
                        t_info = self._registry.get_by_id(terminal_id) if self._registry else None
                        is_physical = t_info and t_info.type == "physical"
                        if is_physical:
                            self._hid.push(sf)

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
                    # Persist layout after state changes — debounced
                    # to at most 1 save per second so rapid-fire event
                    # bursts (e.g. truncation re-reads, tool cascades)
                    # don't hammer the disk with redundant writes.
                    if msg.type in (MessageType.WIDGET_STATE_UPDATE,
                                    MessageType.AGENT_ONLINE,
                                    MessageType.AGENT_OFFLINE):
                        try:
                            import time as _time
                            now = _time.monotonic()
                            if now - self._last_autosave >= 1.0:
                                self._engine.autosave_all()
                                self._last_autosave = now
                        except Exception:
                            pass
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    log.exception("[CONSUMER] unhandled error in message consumer — "
                                  "consumer continues but this message was lost")
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
            widget_id = msg.payload.get("widget_id", f"{agent_name}-{pid}")

            log.info("[AGENT] %s detected (pid=%d) → widget %s on terminal %r",
                     agent_name, pid, widget_id, terminal_id)

            # Create a placeholder WidgetState — PID stored in meta for
            # "vibe-deck whoami" lookups, but widget_id kept as-is so
            # hook events (FileWatcher) and adapter heartbeat target
            # the same widget.
            ds = DisplayState(icon="🆕", color="#64748b", animation="pulse", label="Starting")
            ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds,
                             meta={"agent": agent_name, "pid": pid})
            self._engine.pool_add(ws)  # pool only — user activates on terminals

        elif msg.type == MessageType.AGENT_OFFLINE:
            agent_name = msg.payload.get("agent_name", "unknown")
            pid = msg.payload.get("pid", 0)
            widget_id = msg.payload.get("widget_id", f"{agent_name}-{pid}")

            # ── Crash detection ────────────────────────────
            # If the process exited while still in an active state
            # (Running, Thinking, Writing, Tool, etc.), it likely
            # crashed due to a network error or unhandled exception.
            # Clean exits (Stop hook, SessionEnd) transition to
            # "Idle" first, so they won't match here.
            _ACTIVE_LABELS = {
                "Running", "Thinking", "Writing", "Tool",
                "Starting", "Compact", "Waiting", "Approve",
                "Asking...", "Approval?",
            }
            _was_active = False
            frame = self._engine.get_frame(terminal_id)
            existing_term = frame.widgets.get(widget_id) if frame else None
            if existing_term and existing_term.display.label in _ACTIVE_LABELS:
                _was_active = True
            pool_ws = self._engine.pool_get(widget_id)
            if pool_ws and pool_ws.display.label in _ACTIVE_LABELS:
                _was_active = True

            if _was_active:
                log.warning("[AGENT] %s (pid=%d) exited unexpectedly while active → Error",
                           agent_name, pid)
                crash_display = {"icon": "⚠️", "color": "#ef4444", "animation": "blink", "label": "Error"}
                if pool_ws:
                    pool_ws.update_display(**crash_display)
                    self._engine.pool_add(pool_ws)
                if existing_term:
                    existing_term.update_display(**crash_display)
            else:
                # Clean exit — normal offline transition
                if pool_ws:
                    pool_ws.update_display(icon="⚫", color="#374151", animation="none", label="Offline")
                    self._engine.pool_add(pool_ws)
                if existing_term:
                    existing_term.update_display(icon="⚫", color="#374151", animation="none", label="Offline")

        elif msg.type == MessageType.WIDGET_STATE_UPDATE:
            agent_name = msg.payload.get("agent_name", "unknown")
            data = msg.payload.get("data", {})
            widget_id = msg.payload.get("widget_id", f"{agent_name}-auto")  # -auto fallback for old-format files

            _hook_event = data.get("hook_event_name", "")
            _tool_name = data.get("tool_name", "")
            _status = data.get("status", "")
            log.info("[HOOK→UI] agent=%s widget=%s hook_event=%s tool=%s status=%s",
                     agent_name, widget_id, _hook_event, _tool_name, _status)

            # ── Register console HWND from agent self-reporting ──
            # The reporter attaches _console_hwnd (from GetConsoleWindow)
            # so we can reliably map PID → terminal window.
            _console_hwnd = data.get("_console_hwnd")
            if _console_hwnd:
                import re as _re_hwnd
                _pid_match = _re_hwnd.search(r"-(\d+)$", widget_id)
                if _pid_match:
                    _agent_pid = int(_pid_match.group(1))
                    from ..platform import register_hwnd
                    register_hwnd(_agent_pid, int(_console_hwnd))

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

            # Detect interactive / user-blocking tools (AskUserQuestion etc.)
            # and switch to "Waiting for user" state so the thinking timer
            # doesn't overwrite it while Claude waits for a response.
            _INTERACTIVE_TOOLS = {"AskUserQuestion", "AskUserQuestionTool"}
            if _hook_event == "PreToolUse" and _tool_name in _INTERACTIVE_TOOLS:
                ds.icon = "❓"
                ds.color = "#eab308"
                ds.animation = "blink"
                ds.label = "Asking..."
                log.info("[HOOK→UI] interactive tool %s → Waiting for user", _tool_name)

            # Apply session status label from hook events that carry it
            if _hook_event == "Stop" and data.get("stop_hook_active"):
                ds.label = "Paused"
                ds.animation = "blink"  # thinking timer protects blink states
                ds.color = "#eab308"
                ds.icon = "⏸️"

            # ── Project identity (from cwd) ──────────────────
            # Extract project name from working directory so the Pool
            # and Deck show human-readable project labels (e.g. "VibeDeck",
            # "dungeonless") instead of raw PIDs.
            _cwd = data.get("cwd", "")
            if _cwd:
                from pathlib import Path as _Path
                _project = _Path(_cwd).name
                data["project"] = _project
                # Set initial label to project name if this is the first
                # hook event (SessionStart) and no tool event has overridden it.
                if _hook_event == "SessionStart":
                    ds.label = _project[:12]

            log.info("[HOOK→UI] display resolved → icon=%s color=%s anim=%s label=%s",
                     ds.icon, ds.color, ds.animation, ds.label)

            # Keep pool in sync with the latest display state
            pool_ws = WidgetState(id=widget_id, type=WidgetType.AGENT, display=ds, meta=data)
            if _is_hook_event:
                import time as _ptime
                pool_ws._last_hook_ts = _ptime.time()
            self._engine.pool_add(pool_ws)

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

                # ── Minimum display duration (from adapter config) ─
                from ..adapters.claude_code import get_min_display_ms
                _WAITING_MIN_S = get_min_display_ms("UserPromptSubmit") / 1000.0

                if _hook_event == "UserPromptSubmit":
                    existing._waiting_since = now

                if (_hook_event in ("PreToolUse", "PostToolUse")
                        and hasattr(existing, '_waiting_since')
                        and (now - existing._waiting_since) < _WAITING_MIN_S):
                    existing._pending_display = ds
                    existing._pending_meta = data
                    remaining = _WAITING_MIN_S - (now - existing._waiting_since)
                    log.info("[HOOK→UI] deferring %s display for %.2fs (Waiting grace period)",
                             _hook_event, remaining)
                    self._schedule_deferred_display(tid, widget_id, remaining)
                    updated_tids.append(tid)
                    # Clear _waiting_since so the NEXT tool event
                    # displays immediately instead of deferring again.
                    # Without this, rapid-fire tool calls create a
                    # cascading chain of defers that never resolves.
                    del existing._waiting_since
                    continue

                if _hook_event == "Stop":
                    if hasattr(existing, '_waiting_since'):
                        del existing._waiting_since
                existing.display = ds
                existing.meta.update(data)
                if _is_hook_event:
                    existing._last_hook_ts = _time.time()
                # Track tool-name updates so the thinking timer can respect
                # a minimum display duration before overwriting with "Thinking".
                if _hook_event in ("PreToolUse", "PostToolUse"):
                    existing._last_tool_ts = _time.time()
                # Track hook event type & permission mode for inference
                # (e.g. PreToolUse + non-auto permission → likely waiting for approval)
                if _is_hook_event:
                    existing._last_hook_event = _hook_event
                    _pm = data.get("permission_mode", "")
                    if _pm:
                        existing._permission_mode = _pm
                updated_tids.append(tid)
                log.debug("[HOOK→UI] widget %s UPDATED on terminal %s", widget_id, tid)

            # Widget stays in pool — user activates on terminals via UI/API.
            # State updates to activated terminals are handled by the loop above.

        elif msg.type == MessageType.KEY_PRESSED:
            key = msg.payload.get("key", -1)
            log.info("[KEY] Key %d pressed on terminal %r", key, terminal_id)
            frame = self._engine.get_frame(terminal_id)
            if frame:
                ws = frame.get_widget_at(key)
                if ws:
                    log.info("[KEY] Widget %s at key %d — current state: icon=%s label=%s",
                             ws.id, key, ws.display.icon, ws.display.label)
                else:
                    log.info("[KEY] No widget at key %d on terminal %r", key, terminal_id)

        elif msg.type == MessageType.WIDGET_REMOVED:
            agent_name = msg.payload.get("agent_name", "unknown")
            widget_id = msg.payload.get("widget_id", f"{agent_name}-auto")  # -auto fallback for old-format files
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

    async def _reset_thinking_timer(self, terminal_id: str) -> None:
        """Reset the inactivity timer.  If no new hook event arrives for
        ``thinking_timeout_ms``, apply "Thinking" state to widgets on
        ALL terminals and force-push frames so every connected device
        sees it.

        All timing parameters are read from the Claude Code adapter's
        ``TIMING`` config (overridable via ``claude-code.yaml``).
        """
        import asyncio
        import time as _ptime
        from ..adapters.claude_code import TIMING, STATUS_TO_DISPLAY, get_min_display_ms

        timeout_s = TIMING.get("thinking_timeout_ms", 800) / 1000.0

        async with self._thinking_timer_lock:
            if self._thinking_timer and not self._thinking_timer.done():
                self._thinking_timer.cancel()
                try:
                    await self._thinking_timer
                except asyncio.CancelledError:
                    pass

            # Track when the thinking timer will fire (for debug UI)
            self._next_thinking_fire_at = _ptime.monotonic() + timeout_s

            async def _fire_thinking():
                await asyncio.sleep(timeout_s)
                self._next_thinking_fire_at = 0.0  # timer fired, reset
                from .types import DisplayState
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
                        # Definitive states — never overwrite.
                        if current_label in ("Idle", "Offline", "Error", "Sub done", "Paused", "Asking..."):
                            continue
                        if current_anim in ("blink",):
                            continue
                        # Respect per-event min_display_ms from adapter config.
                        if hasattr(ws, '_last_tool_ts'):
                            tool_age_s = _ptime.time() - ws._last_tool_ts
                            min_display_s = get_min_display_ms("PreToolUse") / 1000.0
                            if tool_age_s < min_display_s:
                                continue  # tool name is still fresh

                        # ── Permission / Approval inference ──────
                        # If the last hook was PreToolUse and permission is
                        # not auto, Claude is likely blocked waiting for user
                        # approval — not actually thinking.
                        _last_evt = getattr(ws, '_last_hook_event', '')
                        _perm = getattr(ws, '_permission_mode', 'auto')
                        if _last_evt == "PreToolUse" and _perm != "auto":
                            ds = DisplayState(
                                icon="🛡️", color="#eab308",
                                animation="blink", label="Approval?",
                            )
                        else:
                            ds = DisplayState(**thinking_cfg)

                        ws.display = ds
                        pushed = True
                        log.info("[THINKING] widget %s → %s (%.1fs silence on terminal %s, last_evt=%s, perm=%s)",
                                 widget_id, ds.label, timeout_s, tid, _last_evt, _perm)
                    # Force immediate frame push for terminals that changed
                    if pushed and hasattr(self, '_web_server'):
                        await self._web_server.broadcast_frame(tid, frame)

            self._thinking_timer = asyncio.create_task(_fire_thinking())

    async def _shutdown(self) -> None:
        """Gracefully shut down all components.

        Order matters here:

        1. Cancel the thinking timer (no more Thinking state pushes).
        2. Stop *producers* (connectors) so no new events arrive.
        3. Stop *adapters* (publish one final Offline state, then stop).
        4. Cancel all supervisor-owned background tasks.
        5. Tear down the web server and render targets.
        6. Persist layout state.
        """
        log.info("Shutting down...")

        # ── 1. Cancel thinking timer ──────────────────
        if self._thinking_timer and not self._thinking_timer.done():
            self._thinking_timer.cancel()

        # ── 2. Stop connectors (scanner + watcher) ────
        #    Must come first so no new messages enter the bus during teardown.
        #    Each .stop() cancels the connector's internal asyncio task.
        if self._scanner:
            try:
                await self._scanner.stop()
            except Exception:
                log.debug("Scanner stop error (ignored)", exc_info=True)
        if self._watcher:
            try:
                await self._watcher.stop()
            except Exception:
                log.debug("Watcher stop error (ignored)", exc_info=True)

        # ── 3. Stop adapters ──────────────────────────
        if hasattr(self, '_adapter_manager'):
            try:
                await self._adapter_manager.stop()
            except Exception:
                log.debug("AdapterManager stop error (ignored)", exc_info=True)

        # ── 4. Cancel all supervisor background tasks ──
        #    _frame_push_loop, _message_consumer, deferred-display tasks, etc.
        for task in self._tasks:
            if not task.done():
                task.cancel()
        if self._tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                log.warning("Timed out waiting for background tasks to finish")
            except Exception:
                log.debug("Error gathering background tasks (ignored)", exc_info=True)

        # ── 5. Close SSE connections ───────────────────
        #    Closes all active SSE streams before tearing down the server.
        if hasattr(self, '_web_server'):
            self._web_server.close_sse_connections()

        # ── 6. Stop web server ────────────────────────
        if hasattr(self, '_web_server'):
            try:
                await asyncio.wait_for(
                    self._web_server.stop(), timeout=5.0
                )
            except asyncio.TimeoutError:
                log.warning("Timed out waiting for web server to stop")
            except Exception:
                log.debug("Web server stop error (ignored)", exc_info=True)

        # ── 7. Close HID transport ──────────────────────
        if hasattr(self, '_hid') and hasattr(self._hid, 'close'):
            try:
                self._hid.close()
            except Exception:
                pass

        # ── 8. Persist layout state ────────────────────
        try:
            self._engine.autosave_all()
            log.info("Layout state autosaved on shutdown")
        except Exception:
            log.debug("Autosave error (ignored)", exc_info=True)

        log.info("Shutdown complete")
        # Force the process to exit after a short grace period.
        # asyncio.run()'s internal cleanup (_cancel_all_tasks,
        # shutdown_asyncgens) can hang on Windows due to third-party
        # async generators (e.g. watchfiles) or aiohttp-internal tasks
        # that don't respond to cancellation.  All important state has
        # already been persisted by this point, so a forced exit is safe.
        import os as _os
        _os._exit(0)


async def run_supervisor(
    port: int = 9734,
    render: str = "sim",
    device_index: int = 0,
    autodetect: bool = True,
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
