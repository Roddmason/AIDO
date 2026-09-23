"""Causas estables de fallo de runtimes de inferencia locales (llama.cpp, LM Studio, vLLM, Ollama).

Un solo `Literal` que comparten salud, sincronización, ejecución, concurrencia y selección, para que la UI
y las remediaciones lean un código y no un texto libre. `LocalRuntimeError` lanza (raises) siempre con una
causa de este conjunto y un mensaje ya libre de secretos.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Literal, cast, get_args

LocalRuntimeCause = Literal[
    "local_server_unreachable",
    "model_loading",
    "local_model_load_failed",
    "local_auth_required",
    "context_length_exceeded",
    "insecure_credential_transport",
    "local_endpoint_busy",
    "insufficient_time_for_model_load",
    "local_model_not_validated",
    "local_model_not_selected",
]
LOCAL_RUNTIME_CAUSES: frozenset[str] = frozenset(get_args(LocalRuntimeCause))


class LocalRuntimeError(RuntimeError):
    """Fallo de un runtime local con causa clasificada; `str(error)` empieza por la causa."""

    def __init__(self, cause: LocalRuntimeCause, message: str) -> None:
        """Valida la causa contra el conjunto cerrado.

        Raises:
            ValueError: si la causa no pertenece a `LocalRuntimeCause`.
        """
        if cause not in LOCAL_RUNTIME_CAUSES:
            raise ValueError(f"Unknown local runtime cause: {cause}")
        super().__init__(f"{cause}: {message}")
        self.cause: LocalRuntimeCause = cause


def local_runtime_cause_of(text: str | None) -> LocalRuntimeCause | None:
    """Causa con la que empieza un motivo (`"<causa>"` o `"<causa>: ..."`); None si no empieza por una."""
    head = str(text or "").strip().split(":", 1)[0].strip()
    return cast(LocalRuntimeCause, head) if head in LOCAL_RUNTIME_CAUSES else None
