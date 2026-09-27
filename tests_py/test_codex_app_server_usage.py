"""Cuota de Codex por el app-server oficial (JSON-RPC por stdio) con respaldo HTTP ``wham/usage``.

Un app-server falso (script Python ejecutable) habla el mismo protocolo JSONL que ``codex app-server``:
``initialize`` → ``initialized`` → ``account/rateLimits/read``.

@author Rodrigo Mason
"""

from __future__ import annotations

import ast
import json
import os
import stat
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.agents import codex_app_server
from local_control_center.agents.codex_app_server import (
    CodexAppServerError,
    read_codex_rate_limits,
    resolve_codex_command,
)
from local_control_center.agents.provider_usage import (
    UsageUnavailableError,
    collect_codex_usage,
    parse_codex_usage,
)
from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.shared.db import open_sqlite_connection

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the fake app-server is a POSIX script")

RATE_LIMITS = {
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 42, "windowDurationMins": 300, "resetsAt": 1_900_000_000},
        "secondary": {"usedPercent": 91, "windowDurationMins": 10080, "resetsAt": 1_900_500_000},
        "rateLimitReachedType": None,
    },
    "rateLimitsByLimitId": None,
}

FAKE_SERVER = """#!{python}
import json, sys, time
mode = {mode!r}
log = open({log!r}, "a")
if sys.argv[1:] != ["app-server"]:
    sys.exit(3)
for raw in sys.stdin:
    message = json.loads(raw)
    log.write(json.dumps(message) + "\\n"); log.flush()
    if mode == "hang":
        time.sleep(60)
    method = message.get("method")
    if method == "initialize":
        print(json.dumps({{"method": "thread/started", "params": {{}}}}), flush=True)
        print(json.dumps({{"id": message["id"], "result": {{"userAgent": "fake"}}}}), flush=True)
    elif method == "account/rateLimits/read":
        if mode == "error":
            print(json.dumps({{"id": message["id"], "error": {{"code": -32600, "message": "not logged in"}}}}), flush=True)
        else:
            print(json.dumps({{"id": message["id"], "result": {result}}}), flush=True)
"""


def _fake(tmp_path: Path, mode: str = "ok") -> tuple[Path, Path]:
    script = tmp_path / f"codex-{mode}"
    log = tmp_path / f"codex-{mode}.log"
    script.write_text(
        FAKE_SERVER.format(python=sys.executable, mode=mode, log=str(log), result=repr(RATE_LIMITS)),
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script, log


def _env(command: Path) -> dict[str, str]:
    return {"AIDO_CODEX_COMMAND": str(command), "PATH": os.environ.get("PATH", "")}


def test_the_app_server_handshake_and_rate_limits_read(tmp_path: Path) -> None:
    script, log = _fake(tmp_path)
    result = read_codex_rate_limits(env=_env(script))
    assert result["rateLimits"]["primary"]["usedPercent"] == 42
    sent = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert [item["method"] for item in sent] == ["initialize", "initialized", "account/rateLimits/read"]
    assert sent[0]["params"]["clientInfo"]["name"] == "aido"
    assert all("jsonrpc" not in item for item in sent)


def test_app_server_windows_are_parsed_with_their_duration_and_reset(tmp_path: Path) -> None:
    script, _log = _fake(tmp_path)
    windows = collect_codex_usage(env=_env(script), fetch=lambda _request: pytest.fail("no HTTP"))
    by_kind = {window.window_kind: window for window in windows}
    assert by_kind["five_hour"].used_percent == 42.0
    assert by_kind["seven_day"].used_percent == 91.0
    assert by_kind["five_hour"].resets_at.startswith("2030-03-17")
    assert {window.source for window in windows} == {"codex_app_server"}


def test_a_reached_limit_marks_the_windows_rejected() -> None:
    payload = {"rateLimits": {**RATE_LIMITS["rateLimits"], "rateLimitReachedType": "rate_limit_reached"}}
    assert {window.status for window in parse_codex_usage(payload)} == {"rejected"}


def test_a_hanging_app_server_is_killed_at_the_deadline(tmp_path: Path) -> None:
    script, _log = _fake(tmp_path, "hang")
    started = time.monotonic()
    with pytest.raises(CodexAppServerError, match="in time"):
        read_codex_rate_limits(env=_env(script), timeout=1.0)
    assert time.monotonic() - started < 5


def test_the_timeout_is_capped_at_ten_seconds() -> None:
    assert codex_app_server.APP_SERVER_TIMEOUT_SECONDS <= 10


def test_any_app_server_failure_falls_back_to_the_http_source(tmp_path: Path) -> None:
    script, _log = _fake(tmp_path, "error")
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    (codex_home / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "tok", "account_id": "acc"}}), encoding="utf-8"
    )
    requests = []

    def fetch(request):
        requests.append(request)
        return {"rate_limit": {"primary_window": {"used_percent": 12, "limit_window_seconds": 18000}}}

    env = {**_env(script), "CODEX_HOME": str(codex_home)}
    windows = collect_codex_usage(env=env, fetch=fetch)
    assert len(requests) == 1 and windows[0].source == "codex_usage"
    # Sin ejecutable también cae al HTTP.
    missing = collect_codex_usage(env={"CODEX_HOME": str(codex_home)}, fetch=fetch)
    assert missing[0].used_percent == 12.0


def test_when_both_sources_fail_the_cause_names_both(tmp_path: Path) -> None:
    with pytest.raises(UsageUnavailableError, match=r"not readable.*app-server: codex executable not found"):
        collect_codex_usage(env={"CODEX_HOME": str(tmp_path / "none")}, fetch=lambda _request: {})


def test_the_command_comes_from_the_codex_env_override_or_the_given_path(tmp_path: Path) -> None:
    script, _log = _fake(tmp_path)
    assert resolve_codex_command({"AIDO_CODEX_COMMAND": str(script)}) == str(script)
    assert resolve_codex_command({"CODEX_CLI_PATH": str(script)}) == str(script)
    assert resolve_codex_command({"PATH": str(tmp_path), "AIDO_CODEX_COMMAND": script.name}) == str(script)
    # Sin PATH en el entorno dado no se busca en el del proceso.
    assert resolve_codex_command({}) is None


def test_the_app_server_never_runs_inside_a_sqlite_transaction(tmp_path: Path) -> None:
    script, log = _fake(tmp_path)
    with closing(open_sqlite_connection(tmp_path / "t.sqlite")) as connection:
        connection.execute("CREATE TABLE t (x)")
        connection.execute("BEGIN")
        connection.execute("INSERT INTO t VALUES (1)")
        context = ProcessExecutionContext(db_path=tmp_path / "t.sqlite", connection=connection)
        with execution_scope(context), pytest.raises(RuntimeError, match="transacci"):
            collect_codex_usage(env=_env(script), fetch=lambda _request: pytest.fail("no HTTP"))
        connection.execute("ROLLBACK")
    assert not log.exists()


def test_the_subprocess_boundary_is_a_fixed_argv_without_shell() -> None:
    source = Path(codex_app_server.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    popen = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "Popen"
    ]
    assert len(popen) == 1
    argv = popen[0].args[0]
    assert isinstance(argv, ast.List) and isinstance(argv.elts[1], ast.Constant)
    assert argv.elts[1].value == "app-server"
    shell = next(keyword for keyword in popen[0].keywords if keyword.arg == "shell")
    assert isinstance(shell.value, ast.Constant) and shell.value.value is False
