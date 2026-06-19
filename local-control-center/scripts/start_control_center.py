from __future__ import annotations

import argparse
import os
import subprocess
import sys
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


def cli_argv(args: argparse.Namespace) -> list[str]:
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
    if args.worker:
        argv.append("--worker")
    if args.workspace.strip():
        argv.extend(["--workspace", args.workspace])
    if args.db_path.strip():
        argv.extend(["--db-path", args.db_path])
    return argv


def main() -> None:
    args = parse_args()
    static_dir = Path(args.static_dir).resolve()
    ensure_dashboard(static_dir, no_build=args.no_build)
    apply_safe_environment_defaults()
    print(f"Dashboard URL: http://{args.dashboard_host}:{args.dashboard_port}", flush=True)
    print("Provider calls: disabled by default (AIDO_ENABLE_REAL_PROVIDER_CALLS=false)", flush=True)
    print("CLI runtimes: disabled by default (AIDO_ENABLE_CLI_RUNTIMES=false)", flush=True)
    os.chdir(ROOT)
    sys.argv = cli_argv(args)
    from local_control_center.cli import main as control_center_main

    control_center_main()


if __name__ == "__main__":
    main()
