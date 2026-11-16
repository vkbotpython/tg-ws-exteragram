"""In-plugin log bus: ring buffer + logging.Handler + rotating file.

All proxy logs use the standard `logging` API, so routing the root logger here
makes every handshake, pool hit and traceback visible in the plugin's debug UI
without adb.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import traceback
from collections import deque
from logging.handlers import RotatingFileHandler
from typing import Deque, List, Optional

from wsproxy.utils import DomainCensorFilter

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_FORMAT = '%(asctime)s %(levelname)-5s %(name)s | %(message)s'
_DATEFMT = '%m-%d %H:%M:%S'


class LogBus:
    def __init__(self, capacity: int = 500, log_dir: Optional[str] = None,
                 max_mb: float = 1.0, backups: int = 2):
        self.capacity = max(50, int(capacity))
        self._records: Deque[str] = deque(maxlen=self.capacity)
        self._lock = threading.RLock()
        self._handler = _RingHandler(self)
        self._file_handler: Optional[RotatingFileHandler] = None
        self._log_dir = log_dir or os.path.join(PLUGIN_ROOT, 'logs')
        self._max_mb = max_mb
        self._backups = max(1, backups)
        self._installed = False
        self._prev_thread_excepthook = None
        self.level = logging.DEBUG

    # -- logging.Handler plumbing ------------------------------------------

    def install(self, logger: Optional[logging.Logger] = None) -> None:
        """Attach the ring handler to a logger (root by default), once."""
        if self._installed:
            return
        target = logger or logging.getLogger()
        fmt = logging.Formatter(_FORMAT, datefmt=_DATEFMT)
        self._handler.setFormatter(fmt)
        self._handler.setLevel(self.level)
        self._handler.addFilter(DomainCensorFilter())
        target.addHandler(self._handler)
        self._target = target

        # Third-party chatter should not drown proxy logs.
        for noisy in ('asyncio', 'httpx', 'httpcore', 'hpack', 'h2', 'h11'):
            logging.getLogger(noisy).setLevel(logging.WARNING)

        self._install_file(fmt)
        self._install_thread_excepthook()
        self._installed = True

    def _install_file(self, fmt: logging.Formatter) -> None:
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            path = os.path.join(self._log_dir, 'bridge.log')
            handler = RotatingFileHandler(
                path, maxBytes=int(self._max_mb * 1024 * 1024),
                backupCount=self._backups, encoding='utf-8', delay=True,
            )
            handler.setFormatter(fmt)
            handler.setLevel(logging.INFO)
            handler.addFilter(DomainCensorFilter())
            self._target.addHandler(handler)
            self._file_handler = handler
            self.log_path = path
        except Exception as exc:
            # A read-only plugin dir must never prevent the proxy from running.
            self.log_path = None
            sys.stderr.write('logbus: file logging unavailable: %r\n' % (exc,))

    def _install_thread_excepthook(self) -> None:
        prev = getattr(threading, 'excepthook', None)
        self._prev_thread_excepthook = prev

        def hook(args):
            text = ''.join(traceback.format_exception(
                args.exc_type, args.exc_value, args.exc_traceback))
            self._push(logging.ERROR, 'Uncaught thread %s:\n%s' % (
                getattr(args.thread, 'name', '?'), text.rstrip()))
            if prev is not None:
                try:
                    prev(args)
                except Exception:
                    pass

        threading.excepthook = hook  # type: ignore[assignment]

    def set_level(self, level) -> None:
        if isinstance(level, str):
            level = getattr(logging, level.upper(), logging.DEBUG)
        self.level = level
        self._handler.setLevel(level)

    def set_capacity(self, size: int) -> None:
        self.capacity = max(50, int(size))
        with self._lock:
            kept = list(self._records)[-self.capacity:]
            self._records.clear()
            self._records.extend(kept)
            self._handler.records = self._records

    # -- buffer API --------------------------------------------------------

    def _push(self, level: int, message: str) -> None:
        with self._lock:
            self._records.append(message)

    def emit(self, level: int, message: str) -> None:
        logging.log(level, message)

    def records(self, limit: Optional[int] = None,
                min_level: Optional[int] = None) -> List[str]:
        with self._lock:
            items = list(self._records)
        if min_level is not None:
            names = {logging.DEBUG: 'DEBUG', logging.INFO: 'INFO',
                     logging.WARNING: 'WARNING', logging.ERROR: 'ERROR',
                     logging.CRITICAL: 'CRITICAL'}
            wanted = names.get(min_level)
            if wanted:
                items = [item for item in items
                         if item.startswith(wanted + ' ')
                         or (' ' + wanted + ' ') in item]
        if limit is not None:
            items = items[-int(limit):]
        return items

    def text(self, limit: Optional[int] = None,
             min_level: Optional[int] = None) -> str:
        return '\n'.join(self.records(limit, min_level))

    def clear(self) -> None:
        with self._lock:
            self._records.clear()

    def close(self) -> None:
        if not self._installed:
            return
        target = getattr(self, '_target', None)
        if target is not None:
            for handler in (self._handler, self._file_handler):
                if handler is not None:
                    try:
                        target.removeHandler(handler)
                    except Exception:
                        pass
        if self._file_handler is not None:
            try:
                self._file_handler.close()
            except Exception:
                pass
        if (self._prev_thread_excepthook is not None
                and getattr(threading, 'excepthook', None) is not None):
            try:
                threading.excepthook = self._prev_thread_excepthook  # type: ignore[assignment]
            except Exception:
                pass
        self._installed = False


class _RingHandler(logging.Handler):
    def __init__(self, bus: LogBus):
        super().__init__()
        self.bus = bus
        self.records = bus._records

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            return
        with self.bus._lock:
            self.records.append(message)


# A process-wide bus makes it safe across Elyx reloads: the plugin can pick up
# the existing bus instead of stacking handlers on the root logger.
_bus: Optional[LogBus] = None
_bus_lock = threading.Lock()


def get_bus(capacity: int = 500, min_level=None) -> LogBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = LogBus(capacity=capacity)
        if min_level is not None:
            _bus.set_level(min_level)
        return _bus


def reset_bus() -> None:
    """Close and forget the current bus (used after a full plugin unload)."""
    global _bus
    with _bus_lock:
        if _bus is not None:
            _bus.close()
            _bus = None
