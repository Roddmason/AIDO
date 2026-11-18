"""Test-only startup scope for the explicitly owned HTTP protocol fixture."""

from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse


@contextmanager
def synthetic_runtime_scope(endpoint: str, database: Path):
    """Confine an E2E resource substitution to a disposable runner invocation."""
    import os

    from local_control_center.quality.paths import validate_scratch_parent

    scratch = os.environ.get("AIDO_QUALITY_SCRATCH")
    retained = os.environ.get("AIDO_QUALITY_RETAINED")
    if not scratch or not retained:
        raise ValueError("Synthetic runtime requires an isolated quality invocation")
    root = validate_scratch_parent(Path(scratch), [Path.cwd(), Path(retained)])
    db = validate_scratch_parent(Path(database), [Path.cwd(), Path(retained)])
    parsed = urlparse(endpoint)
    if (
        not db.is_relative_to(root)
        or db.name != "platform.sqlite"
        or not db.parent.name.startswith("aido-lifecycle-")
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or not parsed.port
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Synthetic runtime does not match the owned lifecycle fixture")
    from local_control_center.agents import ai_execution, runtime_readiness
    from local_control_center.executions import dispatcher, workloads
    from local_control_center.host_resources.profiles import WORKLOAD_PROFILES, workload_profile

    original = runtime_readiness.provider_workload_class
    supervised = dispatcher.run_supervised_capture

    def workload(account):
        if account.get("providerId") == "ollama_remote" and account.get("baseUrl") == endpoint:
            return "remote_llm_light"
        return original(account)

    def dispatch(argv, **kwargs):
        # The worker still calls the real supervisor and the real fenced runner.
        # Only this test-owned startup scope is restored in its new Python process.
        if argv[1:3] == ["-m", "local_control_center.executions.runner"]:
            argv = [
                argv[0],
                "-m",
                "tests_web.fixtures.lifecycle_runtime",
                "--endpoint",
                endpoint,
                "--fixture-db",
                str(db),
                "--mode",
                "dispatcher",
                "--",
                *argv[3:],
            ]
        return supervised(argv, **kwargs)

    with ExitStack() as stack:
        # This dispatcher carries only the explicitly owned HTTP test double.
        # Keep the real memory/process ceilings; cap its CPU within the measured
        # 30% browser validation envelope instead of requesting an impossible 40%.
        agent_profile = workload_profile("agent_cli")
        stack.enter_context(
            patch.dict(
                WORKLOAD_PROFILES,
                {
                    "agent_cli": agent_profile.model_copy(update={"cpu_limit_percent": 30.0}),
                },
            )
        )
        for module in (runtime_readiness, ai_execution, workloads):
            stack.enter_context(patch.object(module, "provider_workload_class", workload))
        stack.enter_context(patch.object(dispatcher, "run_supervised_capture", dispatch))
        yield


def main():
    """Private fixture executable; the product CLI never imports this module."""
    import argparse
    import sys

    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--fixture-db", required=True, type=Path)
    parser.add_argument("--mode", choices=("api", "worker", "dispatcher"), required=True)
    parser.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    db_flag = "--db" if args.mode == "dispatcher" else "--db-path"
    if (
        db_flag not in arguments
        or Path(arguments[arguments.index(db_flag) + 1]).resolve() != args.fixture_db.resolve()
    ):
        parser.error("Entrypoint database differs from the owned synthetic scope")
    with synthetic_runtime_scope(args.endpoint, args.fixture_db):
        sys.argv = [sys.argv[0], *arguments]
        if args.mode == "dispatcher":
            from local_control_center.executions.runner import main as run
        else:
            from local_control_center.cli import main as run
        return run()


if __name__ == "__main__":
    raise SystemExit(main())
