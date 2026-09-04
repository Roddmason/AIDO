from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATIC_DIR = ROOT / "local-control-center" / "dist" / "web"
sys.path.insert(0, str(ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start AIDO Local Control Center as a native OS process.")
    parser.add_argument("--dashboard-host", default="127.0.0.1")
    parser.add_argument("--dashboard-port", type=int, default=4310)
    parser.add_argument("--worker-interval-ms", type=int, default=5000)
    parser.add_argument("--worker-count", type=int, default=2)
    parser.add_argument("--workspace", default="")
    parser.add_argument("--db-path", default="")
    parser.add_argument("--static-dir", default=str(DEFAULT_STATIC_DIR))
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--mode", choices=("api", "worker", "supervisor"), default="supervisor")
    parser.add_argument("--no-build", action="store_true")
    return parser.parse_args()


def dashboard_index(static_dir: Path) -> Path:
    return static_dir / "index.html"


def ensure_dashboard(static_dir: Path, *, no_build: bool) -> None:
    if dashboard_index(static_dir).exists():
        return
    if no_build:
        raise SystemExit(
            f"Built dashboard not found at {static_dir}. "
            "Run `corepack pnpm@10.24.0 run build:control-center` first."
        )
    subprocess.run(
        ["corepack", "pnpm@10.24.0", "run", "build:control-center"],
        cwd=ROOT,
        check=True,
    )
    if not dashboard_index(static_dir).exists():
        raise SystemExit(f"Dashboard build completed but index.html is missing at {static_dir}.")


def apply_safe_environment_defaults() -> None:
    os.environ.setdefault("AIDO_ENABLE_REAL_PROVIDER_CALLS", "false")
    os.environ.setdefault("AIDO_ENABLE_CLI_RUNTIMES", "false")
    os.environ.setdefault("AIDO_REDACT_SECRETS", "true")
    os.environ.setdefault("OPENAI_AGENTS_TRACE_INCLUDE_SENSITIVE_DATA", "false")


def cli_argv(args: argparse.Namespace, *, mode: str) -> list[str]:
    argv = [
        "local_control_center",
        "--dashboard-host",
        args.dashboard_host,
        "--dashboard-port",
        str(args.dashboard_port),
        "--worker-interval-ms",
        str(args.worker_interval_ms),
        "--worker-count",
        str(args.worker_count),
        "--static-dir",
        str(Path(args.static_dir).resolve()),
    ]
    if mode == "api":
        argv.append("--dashboard-only")
    elif mode == "worker":
        argv.extend(["--worker", "--no-dashboard"])
    if args.workspace.strip():
        argv.extend(["--workspace", args.workspace])
    if args.db_path.strip():
        argv.extend(["--db-path", args.db_path])
    return argv


def _spawn(args: argparse.Namespace, *, mode: str) -> subprocess.Popen[bytes]:
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    return subprocess.Popen(
        [sys.executable, "-m", *cli_argv(args, mode=mode)],
        cwd=ROOT,
        env=os.environ.copy(),
        creationflags=creationflags,
    )


def _stop_child(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_supervisor(args: argparse.Namespace) -> int:
    """Mantiene la API viva si el worker cae y detiene sólo sus dos procesos hijos al salir."""
    api_process = _spawn(args, mode="api")
    worker_process = _spawn(args, mode="worker")
    worker_exit_reported = False
    try:
        while api_process.poll() is None:
            worker_exit = worker_process.poll()
            if worker_exit is not None and not worker_exit_reported:
                print(
                    f"El worker terminó con código {worker_exit}; la API continúa disponible.",
                    file=sys.stderr,
                    flush=True,
                )
                worker_exit_reported = True
            time.sleep(0.25)
        return int(api_process.returncode or 0)
    except KeyboardInterrupt:
        return 130
    finally:
        _stop_child(worker_process)
        _stop_child(api_process)


def main() -> None:
    args = parse_args()
    static_dir = Path(args.static_dir).resolve()
    if args.mode in {"api", "supervisor"}:
        ensure_dashboard(static_dir, no_build=args.no_build)
    apply_safe_environment_defaults()
    if args.mode in {"api", "supervisor"}:
        print(f"URL del dashboard: http://{args.dashboard_host}:{args.dashboard_port}", flush=True)
    print("Llamadas a proveedores deshabilitadas por defecto.", flush=True)
    print("Runtimes CLI deshabilitados por defecto.", flush=True)
    os.chdir(ROOT)
    if args.mode == "supervisor":
        raise SystemExit(run_supervisor(args))
    sys.argv = cli_argv(args, mode=args.mode)
    from local_control_center.cli import main as control_center_main

    control_center_main()


if __name__ == "__main__":
    main()
