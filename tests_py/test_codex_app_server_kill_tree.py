"""Cierre del app-server de Codex: liberar el proceso nunca debe colgar al worker (Windows incluido).

@author Rodrigo Mason
"""

from __future__ import annotations


def test_kill_tree_does_not_block_while_the_pump_still_reads_a_pipe_held_open_elsewhere():
    """Un nieto que hereda la tubería la mantiene abierta: cerrar ``stdout`` no debe esperar al lector."""
    import os
    import queue
    import threading
    from types import SimpleNamespace

    from local_control_center.agents.codex_app_server import _kill_tree, _pump

    read_fd, write_fd = os.pipe()
    stdout = open(read_fd, "rb")  # noqa: SIM115
    lines: queue.Queue[bytes | None] = queue.Queue()
    threading.Thread(target=_pump, args=(stdout, lines), daemon=True).start()
    fake_process = SimpleNamespace(
        pid=-1, stdin=None, stdout=stdout, kill=lambda: None, wait=lambda timeout=None: 0
    )
    finished = threading.Event()

    def run() -> None:
        _kill_tree(fake_process)
        finished.set()

    threading.Thread(target=run, daemon=True).start()
    try:
        assert finished.wait(5), "_kill_tree se bloqueó cerrando stdout con un lector activo"
    finally:
        os.close(write_fd)
