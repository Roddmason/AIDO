"""JSON determinista y hashing estable para columnas y deduplicación.

Serializa con claves ordenadas y sin escapar Unicode para que el mismo objeto
produzca siempre el mismo texto, condición necesaria para hashes reproducibles
y para comparar payloads. La carga es tolerante a fallos: nunca propaga errores de parseo.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def json_dumps(value: Any) -> str:
    """Serializa a JSON determinista (claves ordenadas, sin escapar Unicode); ``None`` se vuelve ``{}``."""
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def prompt_json_dumps(value: Any) -> str:
    """Serializa JSON compacto para bloques de prompt: mismo determinismo, sin espacios de relleno.

    Solo para texto que viaja a un modelo (ahorra ~8% del bloque). Las columnas de BD, hashes y
    claves de dedup siguen usando ``json_dumps``: cambiar su formato rompería la deduplicación
    contra filas ya persistidas.
    """
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def json_loads(value: str | None, fallback: Any = None) -> Any:
    """Parsea JSON devolviendo ``fallback`` (o ``{}``) ante texto vacío, ``None`` o inválido."""
    if value in (None, ""):
        return {} if fallback is None else fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {} if fallback is None else fallback


def stable_hash(value: Any) -> str:
    """Calcula un SHA-256 reproducible del valor; los no-str se serializan a JSON ordenado primero."""
    payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
