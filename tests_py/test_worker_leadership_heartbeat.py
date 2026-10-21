"""Heartbeat de liderazgo del worker: un fallo transitorio de SQLite no puede matar el hilo.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import threading

from local_control_center.workers.runtime import LocalWorkerRuntime


def test_leadership_watch_loop_survives_a_transient_sqlite_error() -> None:
    runtime = LocalWorkerRuntime.__new__(LocalWorkerRuntime)
    runtime.worker_id = "worker-test"
    runtime._stop_event = threading.Event()
    runtime._fencing_token = 1
    runtime._status = "running"
    calls: list[int] = []

    def renew(*, status: str) -> bool:
        calls.append(1)
        if len(calls) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        if len(calls) >= 3:
            runtime._stop_event.set()
        return True

    runtime._renew_leadership = renew
    runtime._govern_resources_if_due = lambda: None
    thread = threading.Thread(target=runtime._leadership_watch_loop, daemon=True)
    thread.start()
    thread.join(10)

    assert not thread.is_alive()
    assert len(calls) >= 3
