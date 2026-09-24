"""Dobles HTTP de servidores de inferencia locales para pruebas (escuchan en 127.0.0.1:0).

El doble de llama.cpp reproduce el router real observado el 2026-09-23 (build b11078-e0dff5847, router mode):
`/health`, `/props`, `/v1/models` y `/models` con `status.value`, y `/v1/chat/completions` con `usage`. De la
captura solo se conservan campos no sensibles (id, object, owned_by, created, status.value, aliases, tags); se
descartan `status.args` y `status.preset` porque traen rutas locales. Desde el mismo día el router expone `local`
como alias de `gemma-4-26b-a4b` (lista `aliases` del modelo canónico) y no como id propio: el doble lista tres
ids y acepta pedidos por id o por alias. Como llama-server, `/health` es público aunque el router exija api key.
Un chat a un modelo `unloaded` simula el autoload del router: pasa por `loading` durante `autoload_delay_s`,
queda `loaded` y, con `max_instances=1`, desaloja al modelo cargado anterior.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

LLAMA_ROUTER_CREATED = 1790187967
LLAMA_ROUTER_MODELS: tuple[dict[str, Any], ...] = tuple(
    {
        "id": model_id,
        "object": "model",
        "owned_by": "llamacpp",
        "created": LLAMA_ROUTER_CREATED,
        "status": {"value": state},
        "aliases": list(aliases),
        "tags": [],
    }
    for model_id, state, aliases in (
        ("gemma-4-26b-a4b", "loaded", ("local",)),
        ("gpt-oss-20b", "unloaded", ()),
        ("qwen3.8-27b", "unloaded", ()),
    )
)
LLAMA_ROUTER_PROPS: dict[str, Any] = {"role": "router", "max_instances": 1, "models_autoload": True}


@dataclass
class LlamaRouterState:
    """Estado mutable del doble: respuestas configuradas y registro de lo recibido."""

    health_status: int = 200
    api_key: str | None = None
    chat_responses: list[str] = field(default_factory=list)
    default_chat_content: str = "OK"
    models: list[dict[str, Any]] = field(default_factory=lambda: copy.deepcopy(list(LLAMA_ROUTER_MODELS)))
    requests: list[tuple[str, str, str | None]] = field(default_factory=list)
    chat_bodies: list[dict[str, Any]] = field(default_factory=list)
    autoload_delay_s: float = 0.0
    models_body: str | None = None
    """Cuerpo crudo de `/v1/models` en vez de la lista (simula un servidor que responde basura)."""


class _LlamaRouterHandler(BaseHTTPRequestHandler):
    state: LlamaRouterState

    def log_message(self, *_args: object) -> None:
        return

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_raw(self, status: int, text: str) -> None:
        body = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, message: str, error_type: str) -> None:
        self._send_json(status, {"error": {"code": status, "message": message, "type": error_type}})

    def _authorized(self) -> bool:
        expected = self.state.api_key
        return not expected or self.headers.get("Authorization") == f"Bearer {expected}"

    def _record(self) -> None:
        self.state.requests.append((self.command, self.path, self.headers.get("Authorization")))

    def _autoload(self, requested: str) -> None:
        """Autoload del router: un modelo `unloaded` pasa por `loading` y queda `loaded` (max_instances=1)."""
        target = next(
            (item for item in self.state.models if requested in {item["id"], *item.get("aliases", [])}), None
        )
        if target is None or target["status"]["value"] != "unloaded":
            return
        if LLAMA_ROUTER_PROPS["max_instances"] == 1:
            for item in self.state.models:
                if item is not target and item["status"]["value"] == "loaded":
                    item["status"]["value"] = "unloaded"
        target["status"]["value"] = "loading"
        time.sleep(self.state.autoload_delay_s)
        target["status"]["value"] = "loaded"

    def do_GET(self) -> None:
        self._record()
        if self.path == "/health":
            if self.state.health_status == 200:
                self._send_json(200, {"status": "ok"})
            else:
                self._error(self.state.health_status, "Loading model", "unavailable_error")
            return
        if not self._authorized():
            self._error(401, "Invalid API Key", "authentication_error")
        elif self.path == "/props":
            self._send_json(200, LLAMA_ROUTER_PROPS)
        elif self.path in {"/v1/models", "/models"} and self.state.models_body is not None:
            self._send_raw(200, self.state.models_body)
        elif self.path in {"/v1/models", "/models"}:
            self._send_json(200, {"object": "list", "data": self.state.models})
        else:
            self._error(404, "File Not Found", "not_found_error")

    def do_POST(self) -> None:
        self._record()
        length = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(length) if length else b"{}"
        if not self._authorized():
            self._error(401, "Invalid API Key", "authentication_error")
            return
        if self.path != "/v1/chat/completions":
            self._error(404, "File Not Found", "not_found_error")
            return
        body = json.loads(raw.decode("utf-8") or "{}")
        self.state.chat_bodies.append(body)
        self._autoload(str(body.get("model") or ""))
        content = (
            self.state.chat_responses.pop(0) if self.state.chat_responses else self.state.default_chat_content
        )
        self._send_json(
            200,
            {
                "id": "chatcmpl-local-double",
                "object": "chat.completion",
                "created": LLAMA_ROUTER_CREATED,
                "model": str(body.get("model") or ""),
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": content},
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15},
            },
        )


@contextmanager
def serve_http_double(
    handler_class: type[BaseHTTPRequestHandler], *, name: str = "local-llm-double"
) -> Iterator[str]:
    """Constructor único de los dobles de este módulo: sirve `handler_class` en 127.0.0.1:0 y entrega la raíz.

    La raíz es `http://127.0.0.1:<puerto>` (sin `/v1`). Al salir cierra siempre con `shutdown()` y
    `server_close()` y espera el hilo, porque pytest corre con `filterwarnings = error` y un socket abierto es un
    `ResourceWarning`. Los dobles que agregan S2 (razonamiento) y S4 (LM Studio, vLLM, genérico) lo reutilizan en
    vez de levantar su propio `ThreadingHTTPServer`.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_class)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name=name, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def running_llama_router(
    *,
    health_status: int = 200,
    api_key: str | None = None,
    chat_responses: list[str] | None = None,
    autoload_delay_s: float = 0.0,
) -> Iterator[tuple[str, LlamaRouterState]]:
    """Levanta el doble del router llama.cpp y entrega (raíz sin `/v1`, estado); lo cierra al salir."""
    state = LlamaRouterState(
        health_status=health_status,
        api_key=api_key,
        chat_responses=list(chat_responses or []),
        autoload_delay_s=autoload_delay_s,
    )
    handler = type("LlamaRouterHandler", (_LlamaRouterHandler,), {"state": state})
    with serve_http_double(handler, name="llama-router-double") as root:
        yield root, state
