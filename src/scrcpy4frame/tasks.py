"""Runs blocking work (adb calls) on a thread pool and delivers results on the Qt main thread."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

log = logging.getLogger(__name__)


class TaskRunner(QObject):
    _finished = Signal(object)

    def __init__(self, parent: QObject | None = None, workers: int = 6):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="task")
        self._closed = False
        # Emitted from worker threads; Qt queues delivery to this object's (main) thread.
        self._finished.connect(self._deliver)

    def run(self, fn: Callable[..., Any], *args: Any,
            on_done: Callable[[Any], None] | None = None,
            on_error: Callable[[BaseException], None] | None = None) -> None:
        if self._closed:
            return

        def job() -> None:
            try:
                payload = (on_done, True, fn(*args))
            except Exception as exc:
                log.exception("background task %s failed", getattr(fn, "__name__", fn))
                payload = (on_error, False, exc)
            if not self._closed:
                self._finished.emit(payload)

        self._pool.submit(job)

    @Slot(object)
    def _deliver(self, payload) -> None:
        callback, _ok, value = payload
        if callback is not None:
            try:
                callback(value)
            except Exception:
                log.exception("task callback failed")

    def shutdown(self) -> None:
        self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
