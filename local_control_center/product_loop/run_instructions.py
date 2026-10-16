"""Texto de instrucción de un run: acotado del prompt del developer y mensaje de continuación.

Funciones puras sobre cadenas; el tope existe porque cada ronda de rework y cada failover reenvían la
instrucción completa.

@author Rodrigo Mason
"""

from __future__ import annotations

INSTRUCTION_PROMPT_LIMIT_CHARS = 20_000
"""Tope del texto de instrucción del developer (se reenvía por ronda de rework y por failover)."""

CHANGE_REQUEST_HEADER = "[Operator change request after delivery review]"
EARLIER_CHANGE_REQUEST_HEADER = (
    "[Earlier change request - handled in a previous review round; "
    "the latest change request takes precedence]"
)


def bounded_instruction(text: str, limit: int = INSTRUCTION_PROMPT_LIMIT_CHARS) -> str:
    """Acota la instrucción del developer cortando el MEDIO del texto con marcador.

    El inicio (qué se pide) y el final (últimos matices del operador) son las partes con más señal;
    un mensaje desbordado se re-paga por cada ronda de rework y por cada failover de runtime.
    """
    if len(text) <= limit:
        return text
    half = (limit - 40) // 2
    return f"{text[:half]}\n[... instruction truncated ...]\n{text[-half:]}"


def continuation_run_message(message: str, change_request: str) -> str:
    """Mensaje del run de continuación: el pedido nuevo al final y los anteriores marcados como revisados.

    Los pedidos se acumulan ronda a ronda en el mensaje del hilo. Con el mismo encabezado, el
    developer volvía a aplicar uno ya resuelto (visto en vivo: seguía razonando sobre una línea muerta
    que la ronda anterior ya había quitado). Sin pedido nuevo el mensaje queda igual.
    """
    if not change_request:
        return message
    earlier = message.replace(CHANGE_REQUEST_HEADER, EARLIER_CHANGE_REQUEST_HEADER)
    return f"{earlier}\n\n{change_request}"
