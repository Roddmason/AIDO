"""Normaliza el texto que devuelve un modelo antes de persistirlo o parsearlo como JSON.

Los servidores locales (llama.cpp, LM Studio, vLLM) y varios modelos de razonamiento emiten
bloques ``<think>...</think>`` dentro de ``content`` y envuelven el JSON en fences de Markdown.
Este módulo los retira con un recorrido lineal (``str.find`` que solo avanza) sobre una entrada
acotada por ``MAX_MODEL_OUTPUT_SCAN_CHARS``, sin expresiones regulares con retroceso, para que
una salida hostil o enorme no degrade al proceso. No redacta secretos: esa política vive en
``shared.redaction`` y se aplica al persistir.

@author Rodrigo Mason
"""

from __future__ import annotations

MAX_MODEL_OUTPUT_SCAN_CHARS = 16 * 1024 * 1024
THINK_OPEN_TAG = "<think>"
THINK_CLOSE_TAG = "</think>"
_FENCE = "```"


def strip_reasoning_blocks(text: str) -> tuple[str, int]:
    """Quita los bloques ``<think>`` y devuelve el texto limpio y los caracteres de razonamiento.

    Un cierre huérfano antes de cualquier apertura descarta todo lo previo (plantillas que abren
    el bloque en el prompt); un bloque sin cierre descarta hasta el final. Si se quitó algún
    bloque, el resultado se devuelve sin espacios envolventes; si no, intacto.
    """
    bounded = text[:MAX_MODEL_OUTPUT_SCAN_CHARS]
    reasoning_chars = 0
    removed = False
    cursor = 0
    first_close = bounded.find(THINK_CLOSE_TAG)
    first_open = bounded.find(THINK_OPEN_TAG)
    if first_close >= 0 and (first_open < 0 or first_close < first_open):
        reasoning_chars += len(bounded[:first_close].strip())
        cursor = first_close + len(THINK_CLOSE_TAG)
        removed = True
    pieces: list[str] = []
    while cursor < len(bounded):
        start = bounded.find(THINK_OPEN_TAG, cursor)
        if start < 0:
            pieces.append(bounded[cursor:])
            break
        removed = True
        pieces.append(bounded[cursor:start])
        body_start = start + len(THINK_OPEN_TAG)
        end = bounded.find(THINK_CLOSE_TAG, body_start)
        if end < 0:
            reasoning_chars += len(bounded[body_start:].strip())
            break
        reasoning_chars += len(bounded[body_start:end].strip())
        cursor = end + len(THINK_CLOSE_TAG)
    cleaned = "".join(pieces)
    return (cleaned.strip() if removed else cleaned), reasoning_chars


def strip_code_fences(text: str) -> str:
    """Retira un fence Markdown que envuelve todo el payload y deja intacto el resto."""
    candidate = text.strip()
    if not candidate.startswith(_FENCE):
        return candidate
    lines = candidate.splitlines()
    if len(lines) < 2 or lines[-1].strip() != _FENCE:
        return candidate
    return "\n".join(lines[1:-1]).strip()


def json_candidate_text(text: str) -> str:
    """Devuelve el texto listo para ``json.loads``: sin razonamiento, sin fences ni espacios envolventes."""
    cleaned, _ = strip_reasoning_blocks(text)
    return strip_code_fences(cleaned)
