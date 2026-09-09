"""Resource injection only for the owned, model-free native capture fixture."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch

GIB = 1024**3
SYNTHETIC_EXECUTION_BYTES = 2 * GIB
SYNTHETIC_SESSION_BYTES = 12 * GIB


@contextmanager
def capture_resource_scope(database: Path):
    import os

    from local_control_center.quality.paths import inherited_paths, validate_scratch_parent

    paths = inherited_paths(os.environ)
    db = validate_scratch_parent(database, [Path.cwd(), paths.evidence]) if paths else database
    if paths is None or not db.is_relative_to(paths.scratch) or db.name != "isolated.sqlite":
        raise ValueError("Capture scope requires the owned isolated fixture database")
    from local_control_center.host_resources.profiles import CAPTURE_SESSION_PARTS, WORKLOAD_PROFILES
    from local_control_center.process_supervision import launcher_session

    def profile(name, memory):
        current = WORKLOAD_PROFILES[name]
        return type(current).model_validate({**current.model_dump(), "memory_limit_bytes": memory})

    start_control = launcher_session.LauncherCaptureSession.start_control
    supervised = launcher_session.run_supervised_capture

    def entrypoint(argv, mode):
        return [
            argv[0],
            "-m",
            "tests_py.fixtures.capture_runtime",
            "--fixture-db",
            str(db),
            "--mode",
            mode,
            "--",
            *argv[3:],
        ]

    def control(session, mode, argv):
        if argv[1:3] != ["-m", "local_control_center"]:
            raise ValueError("Unexpected canonical control-plane entrypoint")
        return start_control(session, mode, entrypoint(argv, "cli"))

    def dispatch(argv, **kwargs):
        if argv[1:3] == ["-m", "local_control_center.executions.runner"]:
            argv = entrypoint(argv, "dispatcher")
        return supervised(argv, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(
            patch.dict(
                WORKLOAD_PROFILES,
                {
                    "agent_cli": profile("agent_cli", SYNTHETIC_EXECUTION_BYTES),
                    "capture_session": profile("capture_session", SYNTHETIC_SESSION_BYTES),
                },
            )
        )
        stack.enter_context(
            patch.dict(
                CAPTURE_SESSION_PARTS,
                {
                    "execution": {
                        **CAPTURE_SESSION_PARTS["execution"],
                        "memoryBytes": SYNTHETIC_EXECUTION_BYTES,
                    },
                },
            )
        )
        stack.enter_context(
            patch.dict(launcher_session.SESSION_BUDGET, {"memoryBytes": SYNTHETIC_SESSION_BYTES})
        )
        stack.enter_context(patch.object(launcher_session.LauncherCaptureSession, "start_control", control))
        stack.enter_context(patch.object(launcher_session, "run_supervised_capture", dispatch))
        yield


def main():
    """Run the unchanged canonical launcher/CLI/dispatcher within this test-owned scope."""
    import argparse
    import runpy
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture-db", type=Path, required=True)
    parser.add_argument("--mode", choices=("launcher", "cli", "dispatcher"), required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    flag = "--db" if args.mode == "dispatcher" else "--db-path"
    if (
        flag not in arguments
        or Path(arguments[arguments.index(flag) + 1]).resolve() != args.fixture_db.resolve()
    ):
        parser.error("Entrypoint database differs from the owned capture fixture")
    with capture_resource_scope(args.fixture_db):
        import os
        from contextlib import closing

        from local_control_center.quality.paths import inherited_paths
        from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
        from local_control_center.shared.db import open_sqlite_connection

        with closing(open_sqlite_connection(args.fixture_db)) as connection:
            installation = RuntimeConfigRepository(connection).get_installation("codex_cli")
        executable = Path(installation["executablePath"])
        paths = inherited_paths(os.environ)
        if (
            not executable.is_file()
            or not executable.resolve().is_relative_to(paths.scratch)
            or executable.name != "codex.exe"
            or not executable.parent.name.startswith("native-http")
        ):
            raise PermissionError("Capture resource injection requires the compiled native-http fixture")
        sys.argv = [sys.argv[0], *arguments]
        if args.mode == "launcher":
            runpy.run_path("local-control-center/scripts/start_control_center.py", run_name="__main__")
            return 0
        if args.mode == "dispatcher":
            from local_control_center.executions.runner import main as run
        else:
            from local_control_center.cli import main as run
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
