"""Arranque por linea de comandos del Local Control Center: dashboard FastAPI y/o worker.

Parsea flags (host/puerto, modos dashboard/worker, rutas de db y estaticos), inicializa
el runtime del control plane y lanza uvicorn y el loop del worker segun la combinacion
elegida. Tambien fija la politica de event loop en Windows para compatibilidad con asyncio.

@author Rodrigo Mason
"""

from __future__ import annotations

import argparse
import asyncio
import os
import threading
import time
from pathlib import Path

import uvicorn

from .app import create_app
from .control_plane.runtime import ControlCenterRuntime
from .shared.settings import default_db_path
from .worker import ConcurrentWorker


def configure_windows_event_loop_policy(platform_name: str = os.name) -> bool:
    """Activa el selector event loop en Windows; devuelve True si se aplico el cambio.

    Evita el ProactorEventLoop por defecto en `nt`, que provoca fallos con sockets/subprocesos
    bajo uvicorn. En otras plataformas o sin la politica disponible no hace nada y devuelve False.
    """
    if platform_name != "nt":
        return False
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return False
    asyncio.set_event_loop_policy(selector_policy())
    return True


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def validate_dashboard_host(host: str, *, allow_external: bool = False) -> str:
    """Devuelve `host` si es loopback; aborta si expone la API salvo override explicito.

    El control plane declara `loopbackOnly: True` en su handshake: servir en un host no-loopback
    expondria el token de escritura a la red. Solo se permite con `allow_external=True`
    (env `AIDO_ALLOW_EXTERNAL_DASHBOARD=1`), decision consciente del operador.
    """
    normalized = str(host or "").strip()
    if normalized in LOOPBACK_HOSTS or allow_external:
        return normalized
    raise SystemExit(
        "--dashboard-host must stay on loopback (127.0.0.1, localhost, ::1); "
        f"got {normalized!r}. Set AIDO_ALLOW_EXTERNAL_DASHBOARD=1 to override deliberately."
    )


def parse_args() -> argparse.Namespace:
    """Define y parsea los flags de la CLI (host/puerto, modos dashboard/worker, db, estaticos)."""
    parser = argparse.ArgumentParser(description="Local Control Center Python backend")
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=4310)
    parser.add_argument("--dashboard-only", action="store_true")
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--no-worker", action="store_true")
    parser.add_argument("--worker-interval-ms", type=int, default=5000)
    parser.add_argument("--worker-count", type=int, default=2)
    parser.add_argument("--workspace", default=os.getcwd())
    parser.add_argument("--db-path", default=str(default_db_path()))
    parser.add_argument("--static-dir", default=str(Path("local-control-center") / "dist" / "web"))
    return parser.parse_args()


def main() -> None:
    """Punto de entrada de la CLI: configura runtime y arranca worker y/o dashboard segun flags.

    Con `--worker --no-dashboard` corre el loop del worker en primer plano; en otro caso opcionalmente
    lanza el worker en un hilo daemon y sirve la app FastAPI con uvicorn.
    """
    configure_windows_event_loop_policy()
    args = parse_args()
    cwd = Path(args.workspace)
    db_path = Path(args.db_path)

    runtime = ControlCenterRuntime(cwd=cwd, db_path=db_path)
    runtime.init()
    runtime.ensure_runtime_project()

    def worker_loop() -> None:
        worker = ConcurrentWorker(db_path=db_path)
        while True:
            worker.run_batch(worker_count=args.worker_count, max_jobs=args.worker_count)
            time.sleep(max(args.worker_interval_ms, 100) / 1000)

    if args.worker and args.no_dashboard:
        worker_loop()
    else:
        if args.worker and not args.no_worker:
            thread = threading.Thread(target=worker_loop, name="local-control-center-worker", daemon=True)
            thread.start()
        host = validate_dashboard_host(
            args.dashboard_host,
            allow_external=os.environ.get("AIDO_ALLOW_EXTERNAL_DASHBOARD") == "1",
        )
        app = create_app(runtime=runtime, static_dir=args.static_dir)
        uvicorn.run(app, host=host, port=args.dashboard_port)


if __name__ == "__main__":
    main()
