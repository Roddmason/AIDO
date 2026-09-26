"""Corte por bucle de repetición: el modelo local deja de generar apenas repite, no al tope de tokens.

Visto en vivo: gemma-4 repitió cientos de veces el mismo comentario dentro del JSON del patch del
DeveloperAgent (~10k tokens, 3,5 min por llamada) y la salida terminaba inválida igual.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import closing, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from local_control_center.agents.providers.base import ModelRequest
from local_control_center.agents.providers.openai_compatible import (
    REPETITION_LOOP_FINISH_REASON,
    OpenAICompatibleProvider,
)
from local_control_center.agents.providers.repetition_guard import (
    CHECK_EVERY_CHARS,
    MIN_REPEATS,
    RepetitionGuard,
    has_repetition_loop,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from tests_py.fakes.local_llm_servers import ScriptedChatReply, reasoning_llm_server
from tests_py.test_local_model_execution_path import execute_local, register_local_account

MESSAGES = [{"role": "user", "content": "Return the JSON patch."}]
DEGENERATE_LINE = "\\n    # It says: `re.sub(r'[^\\\\w\\\\s]', '', ...)`? No."
LEGIT_PATCH = json.dumps(
    {
        "summary": "Sort ties alphabetically",
        "files": [
            {
                "path": "src/textkit/utils.py",
                "content": "\n".join(
                    [
                        "import re",
                        "from collections import Counter",
                        "",
                        "",
                        "def top_words(text: str, n: int = 3) -> list[tuple[str, int]]:",
                        '    words = re.findall(r"\\w+", text.lower())',
                        "    counts = Counter(words)",
                        "    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))",
                        "    return ordered[:n]",
                        "",
                    ]
                ),
            },
            {
                "path": "tests/test_utils.py",
                "content": "\n".join(
                    [
                        "from textkit.utils import top_words",
                        "",
                        "",
                        "def test_ties_are_alphabetical():",
                        "    assert top_words('zeta alpha beta', 1) == [('alpha', 1)]",
                        "",
                        "",
                        "def test_counts_first():",
                        "    assert top_words('b a b', 1) == [('b', 2)]",
                        "",
                    ]
                ),
            },
        ],
        "tests": ["uv run pytest tests -q"],
        "risks": [],
    }
)


def local_provider(base_url: str) -> OpenAICompatibleProvider:
    provider = OpenAICompatibleProvider(
        provider_id="llama-local",
        base_url=base_url,
        credential_ref="",
        credential_required=False,
        use_legacy_fallbacks=False,
    )
    provider.send_output_limit = True
    return provider


def test_a_repeated_line_is_a_loop_and_a_real_patch_is_not() -> None:
    assert has_repetition_loop('{"files": [{"content": "import re' + DEGENERATE_LINE * 12)
    assert not has_repetition_loop(LEGIT_PATCH)
    assert not has_repetition_loop(DEGENERATE_LINE * 3)
    assert not has_repetition_loop("")


def test_the_guard_checks_in_batches_and_reports_the_accumulated_text() -> None:
    guard = RepetitionGuard()
    fed = 1
    while not guard.feed(DEGENERATE_LINE):
        fed += 1
        assert fed < 40

    # Hace falta la repetición mínima y la revisión corre por lotes, no en cada fragmento.
    assert fed >= MIN_REPEATS
    assert fed * len(DEGENERATE_LINE) >= CHECK_EVERY_CHARS
    assert guard.text == DEGENERATE_LINE * fed


class _StreamServer:
    def __init__(self, chunks: list[str], *, usage: dict[str, Any] | None) -> None:
        self.chunks = chunks
        self.usage = usage
        self.base_url = ""
        self.bodies: list[dict[str, Any]] = []
        self.written = 0
        self.disconnected = threading.Event()


@contextmanager
def streaming_server(chunks: list[str], *, usage: dict[str, Any] | None = None) -> Iterator[Any]:
    state = _StreamServer(chunks, usage=usage)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def _event(self, payload: Any) -> None:
            data = payload if isinstance(payload, str) else json.dumps(payload)
            self.wfile.write(f"data: {data}\n\n".encode())
            self.wfile.flush()

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            state.bodies.append(json.loads(self.rfile.read(length) or b"{}"))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            try:
                self._event(
                    {"choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]}
                )
                for chunk in state.chunks:
                    self._event(
                        {"choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]}
                    )
                    state.written += 1
                self._event({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
                if state.usage is not None:
                    self._event({"choices": [], "usage": state.usage})
                self._event("[DONE]")
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                state.disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_a_streamed_answer_is_assembled_with_its_usage() -> None:
    usage = {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52}
    pieces = [LEGIT_PATCH[index : index + 50] for index in range(0, len(LEGIT_PATCH), 50)]
    with streaming_server(pieces, usage=usage) as server:
        response = local_provider(server.base_url).chat_completion(
            ModelRequest(model="gemma", messages=MESSAGES, stream=True, timeoutSeconds=30)
        )

    assert response.content == LEGIT_PATCH
    assert response.finish_reason == "stop"
    assert response.usage.output_tokens == 12
    assert response.usage.raw_usage["usage_source"] == "provider"
    assert server.bodies[0]["stream"] is True
    assert server.bodies[0]["stream_options"] == {"include_usage": True}


def test_a_looping_stream_is_cut_long_before_the_token_cap() -> None:
    chunks = ['{"summary": "fix", "files": [{"path": "a.py", "content": "import re'] + [
        DEGENERATE_LINE
    ] * 5000
    with streaming_server(chunks) as server:
        response = local_provider(server.base_url).chat_completion(
            ModelRequest(model="gemma", messages=MESSAGES, stream=True, timeoutSeconds=30)
        )
        # Cerrar la conexión es lo que detiene la generación en el servidor.
        assert server.disconnected.wait(timeout=10)

    assert response.finish_reason == REPETITION_LOOP_FINISH_REASON
    assert len(response.content) < 20 * CHECK_EVERY_CHARS
    assert server.written < len(chunks)


def test_a_server_that_ignores_stream_is_read_as_a_normal_answer() -> None:
    with reasoning_llm_server([ScriptedChatReply(content='{"ok": true}')]) as server:
        response = local_provider(server.base_url).chat_completion(
            ModelRequest(model="qwen3-reasoner", messages=MESSAGES, stream=True, timeoutSeconds=30)
        )

    assert response.content == '{"ok": true}'
    assert server.requests[-1]["body"]["stream"] is True


def test_a_remote_account_never_streams() -> None:
    remote = OpenAICompatibleProvider(
        provider_id="remote",
        base_url="http://unused",
        credential_ref="",
        credential_required=False,
        use_legacy_fallbacks=False,
    )
    with reasoning_llm_server([ScriptedChatReply(content="ok")]) as server:
        remote.base_url = server.base_url
        remote.chat_completion(ModelRequest(model="qwen3-reasoner", messages=MESSAGES, stream=True))

    assert server.requests[-1]["body"]["stream"] is False


def test_only_a_call_that_asks_for_it_streams_through_the_runtime_adapter(tmp_path: Path) -> None:
    with closing(open_sqlite_connection(tmp_path / "lane.sqlite")) as connection:
        initialize_platform_schema(connection)
        with reasoning_llm_server(
            [ScriptedChatReply(content='{"ok": true}'), ScriptedChatReply(content='{"ok": true}')]
        ) as server:
            register_local_account(connection, server.base_url)
            streamed = execute_local(connection, tmp_path, streamOutput=True)
            plain = execute_local(connection, tmp_path)

    assert streamed.status == plain.status == "completed"
    assert [item["body"]["stream"] for item in server.requests] == [True, False]
