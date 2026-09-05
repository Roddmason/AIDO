"""Cierre de un hijo de QA propio con identidad nativa, nunca mediante búsqueda por puerto.

El runner conserva la hora de creación obtenida al lanzar el hijo. Si la identidad cambia o
el root desaparece antes del cierre, falla sin seleccionar procesos sustitutos.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import json
from contextlib import suppress

import psutil


def owned_root(root_pid: int, parent_pid: int, created_at: float | None = None) -> psutil.Process:
    """Resuelve sólo el hijo directo del runner y rechaza identidad reciclada."""
    process = psutil.Process(root_pid)
    if process.ppid() != parent_pid or (
        created_at is not None and abs(process.create_time() - created_at) >= 0.01
    ):
        raise RuntimeError("QA process ownership/creation-time mismatch; no process was terminated.")
    return process


def stop_owned_tree(root_pid: int, parent_pid: int, created_at: float) -> dict:
    """Snapshot de descendientes antes de terminar el redirector; psutil protege contra PID reuse."""
    root = owned_root(root_pid, parent_pid, created_at)
    descendants = root.children(recursive=True)
    processes = [root, *reversed(descendants)]
    for process in processes:
        with suppress(psutil.NoSuchProcess):
            process.terminate()
    _, alive = psutil.wait_procs(processes, timeout=5)
    for process in alive:
        with suppress(psutil.NoSuchProcess):
            process.kill()
    _, alive = psutil.wait_procs(alive, timeout=5)
    if alive:
        raise RuntimeError("Owned QA descendants did not exit; outer supervisor cleanup is required.")
    return {"status": "stopped", "descendantCount": len(descendants), "remainingCount": 0}


def main() -> None:
    """Opera exclusivamente sobre la identidad que el runner obtuvo desde su ChildProcess."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("identify", "stop"))
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--created-at", type=float)
    args = parser.parse_args()
    if args.action == "identify":
        print(json.dumps({"createdAt": owned_root(args.pid, args.parent_pid).create_time()}))
    else:
        if args.created_at is None:
            parser.error("stop requires --created-at")
        print(json.dumps(stop_owned_tree(args.pid, args.parent_pid, args.created_at)))


if __name__ == "__main__":
    main()
