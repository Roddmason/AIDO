"""Canal en proceso para la salida cruda de un modelo, fuera de todo dict persistible.

El adapter de proveedor deja aquí el texto sin redactar (ya sin razonamiento) bajo la clave de
la tool call del broker (``agent-tool-call-<uuid>``); el agente que parsea ese resultado lo
retira una sola vez. Invariantes: ningún valor de este almacén forma parte del
``RuntimeExecutionResult`` ni del payload de ``agent_tool_calls``; cada entrada vence a los
``TRANSIENT_OUTPUT_TTL_SECONDS``; un texto mayor que ``TRANSIENT_OUTPUT_MAX_BYTES`` no se guarda
(el agente vuelve al artifact redactado) y se retienen a lo más ``TRANSIENT_OUTPUT_MAX_ENTRIES``
entradas, descartando las más antiguas. Solo vive en el proceso que ejecutó la llamada: un
runtime CLI u otro proceso nunca lo encuentran y usan el artifact.

@author Rodrigo Mason
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

TRANSIENT_OUTPUT_MAX_BYTES = 4 * 1024 * 1024
TRANSIENT_OUTPUT_TTL_SECONDS = 300.0
TRANSIENT_OUTPUT_MAX_ENTRIES = 64

_clock: Callable[[], float] = time.monotonic
_lock = threading.Lock()
_entries: OrderedDict[str, tuple[float, str]] = OrderedDict()


def _purge_expired(now: float) -> None:
    for key in [key for key, (expires_at, _) in _entries.items() if expires_at <= now]:
        del _entries[key]


def put_transient_output(key: str, text: str) -> None:
    """Guarda ``text`` bajo ``key`` para una única lectura posterior.

    Raises:
        ValueError: si ``key`` está vacía.
    """
    if not key:
        raise ValueError("transient output key is required")
    if len(text.encode("utf-8")) > TRANSIENT_OUTPUT_MAX_BYTES:
        return
    with _lock:
        now = _clock()
        _purge_expired(now)
        _entries[key] = (now + TRANSIENT_OUTPUT_TTL_SECONDS, text)
        _entries.move_to_end(key)
        while len(_entries) > TRANSIENT_OUTPUT_MAX_ENTRIES:
            _entries.popitem(last=False)


def take_transient_output(key: str) -> str | None:
    """Retira y devuelve el texto de ``key``; ``None`` si no existe, ya se leyó o venció."""
    if not key:
        return None
    with _lock:
        entry = _entries.pop(key, None)
        if entry is None or entry[0] <= _clock():
            return None
        return entry[1]


def prefer_transient_output(tool_call_id: str | None, read_artifact: Callable[[], str]) -> str:
    """Devuelve la salida cruda del canal en proceso o, si no está, el texto del artifact redactado."""
    if tool_call_id:
        raw = take_transient_output(str(tool_call_id))
        if raw is not None:
            return raw
    return read_artifact()
