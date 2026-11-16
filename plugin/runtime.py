"""Background asyncio loop that hosts the WS bridge inside the plugin.

The client's main thread must never be blocked, so the engine from
`wsproxy.tg_ws_proxy` runs on a dedicated thread with its own event loop.
Elyx can reload a plugin in-process; a registry keyed by owner id makes a
restart reuse the same runtime instead of leaving a dead thread / busy port.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Dict, Optional

from wsproxy import tg_ws_proxy
from wsproxy.stats import stats

log = logging.getLogger('tg-ws-bridge.runtime')


class ProxyRuntime:
    def __init__(self, owner_id: str = 'tg_ws_bridge'):
        self.owner_id = owner_id
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None
        self._stop_event: Optional[asyncio.Event] = None
        self._started_at: Optional[float] = None
        self._stopped_at: Optional[float] = None
        self._last_error: Optional[str] = None

    # -- state -------------------------------------------------------------

    def is_running(self) -> bool:
        with self._lock:
            return bool(self._thread and self._thread.is_alive()
                        and self._loop and not self._loop.is_closed())

    def uptime(self) -> float:
        with self._lock:
            if self._started_at is None:
                return 0.0
            end = self._stopped_at if self._stopped_at else time.monotonic()
            return max(0.0, end - self._started_at)

    def last_error(self) -> Optional[str]:
        with self._lock:
            return self._last_error

    def status(self) -> dict:
        """Snapshot for the debug UI (all values are plain Python types)."""
        return {
            'running': self.is_running(),
            'uptime': round(self.uptime(), 1),
            'connections_total': stats.connections_total,
            'connections_active': stats.connections_active,
            'connections_ws': stats.connections_ws,
            'connections_h2': stats.connections_h2,
            'connections_tcp_fallback': stats.connections_tcp_fallback,
            'connections_cfproxy': stats.connections_cfproxy,
            'connections_fronting': stats.connections_fronting,
            'connections_bad': stats.connections_bad,
            'connections_masked': stats.connections_masked,
            'ws_errors': stats.ws_errors,
            'bytes_up': stats.bytes_up,
            'bytes_down': stats.bytes_down,
            'pool_hits': stats.pool_hits,
            'pool_misses': stats.pool_misses,
            'cf_pool_hits': stats.cf_pool_hits,
            'cf_pool_misses': stats.cf_pool_misses,
            'last_error': self.last_error() or '-',
            'summary': stats.summary(),
        }

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> bool:
        """Start the engine. Safe to call repeatedly; returns False on failure."""
        with self._lock:
            if self.is_running():
                log.debug('runtime already running')
                return True
            if self._thread is not None and self._thread.is_alive():
                # A previous loop is tearing down; give it a moment.
                self._thread.join(timeout=2)
            self._last_error = None
            self._stopped_at = None
            thread = threading.Thread(
                target=self._thread_main, daemon=True,
                name='tg-ws-bridge-engine',
            )
            self._thread = thread
            thread.start()
            # Wait briefly for the loop to come up so a synchronous caller
            # can immediately report status.
            deadline = time.monotonic() + 3
            while self._loop is None and time.monotonic() < deadline:
                time.sleep(0.01)
            return self.is_running()

    def _thread_main(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._lock:
            self._loop = loop
            self._stop_event = asyncio.Event()
            self._started_at = time.monotonic()
        stop_event = self._stop_event

        async def _runner():
            engine = await tg_ws_proxy.start(stop_event)
            self._task = engine
            await engine

        try:
            loop.run_until_complete(_runner())
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            with self._lock:
                self._last_error = repr(exc)
            log.exception('proxy engine stopped with an error: %s', exc)
        finally:
            with self._lock:
                self._stopped_at = time.monotonic()
                self._task = None
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(
                        asyncio.gather(*pending, return_exceptions=True))
            except Exception:
                pass
            try:
                loop.close()
            except Exception:
                pass
            with self._lock:
                self._loop = None
                self._stop_event = None
                self._task = None

    def stop(self, timeout: float = 8.0) -> bool:
        """Request graceful shutdown and join the thread."""
        with self._lock:
            loop = self._loop
            event = self._stop_event
            thread = self._thread
            if loop is None or loop.is_closed():
                self._mark_stopped()
                return True

            def _request():
                try:
                    tg_ws_proxy.stop()
                except Exception:
                    pass
                if event is not None:
                    event.set()

            try:
                loop.call_soon_threadsafe(_request)
            except RuntimeError:
                self._mark_stopped()
                return True

        if thread is not None:
            thread.join(timeout=timeout)
            if thread.is_alive() and loop is not None and not loop.is_closed():
                # Forced path: cancel every task and stop the loop.
                def _force():
                    try:
                        tg_ws_proxy.stop()
                    except Exception:
                        pass
                    for task in asyncio.all_tasks(loop):
                        task.cancel()
                    loop.stop()

                try:
                    loop.call_soon_threadsafe(_force)
                except RuntimeError:
                    pass
                thread.join(timeout=2.0)
        with self._lock:
            self._mark_stopped()
        return not (thread is not None and thread.is_alive())

    def _mark_stopped(self) -> None:
        if self._stopped_at is None:
            self._stopped_at = time.monotonic()
        self._task = None
        self._loop = None
        self._stop_event = None
        tg_ws_proxy.reset_engine_state()

    def restart(self) -> bool:
        self.stop()
        return self.start()


# ---------------------------------------------------------------------------
# Registry - prevents duplicate runtimes after an Elyx hot reload.
# ---------------------------------------------------------------------------

_runtimes: Dict[str, ProxyRuntime] = {}
_registry_lock = threading.RLock()


def get_runtime(owner_id: str = 'tg_ws_bridge') -> ProxyRuntime:
    with _registry_lock:
        runtime = _runtimes.get(owner_id)
        if runtime is None:
            runtime = ProxyRuntime(owner_id)
            _runtimes[owner_id] = runtime
        return runtime


def release_runtime(owner_id: str = 'tg_ws_bridge') -> None:
    with _registry_lock:
        runtime = _runtimes.pop(owner_id, None)
    if runtime is not None:
        runtime.stop()


def stop_all() -> None:
    with _registry_lock:
        runtimes = list(_runtimes.values())
    for runtime in runtimes:
        runtime.stop()
