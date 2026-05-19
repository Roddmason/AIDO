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
    if platform_name != "nt":
        return False
    selector_policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if selector_policy is None:
        return False
    asyncio.set_event_loop_policy(selector_policy())
    return True


def parse_args() -> argparse.Namespace:
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
        app = create_app(runtime=runtime, static_dir=args.static_dir)
        uvicorn.run(app, host=args.dashboard_host, port=args.dashboard_port)


if __name__ == "__main__":
    main()
