"""Detecta, mientras un modelo local genera por streaming, que entró en un bucle de repetición.

Un modelo local en bucle repite el mismo fragmento hasta el tope de tokens del servidor. Visto en
vivo: gemma-4 escribió cientos de veces el mismo comentario dentro del JSON del patch del
DeveloperAgent (~10k tokens, 3,5 min por llamada) y la salida terminaba inválida igual. Cortar el
stream al detectarlo ahorra ese tiempo; el llamador la trata como salida inválida (el DeveloperAgent
y el ProductOwnerAgent la reparan una vez).

La señal es una cola periódica: la misma unidad de al menos ``MIN_PERIOD_CHARS`` caracteres repetida
``MIN_REPEATS`` veces seguidas. Cubre la línea repetida (con salto real o escapado dentro de un
string JSON) y la frase repetida sin saltos; código legítimo no repite ocho veces seguidas un
bloque idéntico de ese largo.

@author Rodrigo Mason
"""

from __future__ import annotations

__all__ = ["MIN_REPEATS", "RepetitionGuard", "has_repetition_loop"]

MIN_PERIOD_CHARS = 24
MAX_PERIOD_CHARS = 400
MIN_REPEATS = 8
CHECK_EVERY_CHARS = 256


def has_repetition_loop(text: str) -> bool:
    """Indica si el final de ``text`` es una misma unidad repetida ``MIN_REPEATS`` veces seguidas."""
    tail = text[-MAX_PERIOD_CHARS * MIN_REPEATS :]
    for period in range(MIN_PERIOD_CHARS, MAX_PERIOD_CHARS + 1):
        span = period * MIN_REPEATS
        if span > len(tail):
            return False
        if tail[-span:] == tail[-period:] * MIN_REPEATS:
            return True
    return False


class RepetitionGuard:
    """Acumula el texto de un stream y lo revisa cada ``CHECK_EVERY_CHARS`` caracteres nuevos."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._length = 0
        self._checked_at = 0

    def feed(self, fragment: str) -> bool:
        """Agrega ``fragment`` y devuelve ``True`` cuando la salida ya entró en un bucle."""
        if not fragment:
            return False
        self._parts.append(fragment)
        self._length += len(fragment)
        if self._length - self._checked_at < CHECK_EVERY_CHARS:
            return False
        self._checked_at = self._length
        return has_repetition_loop(self.text)

    @property
    def text(self) -> str:
        """Texto acumulado hasta ahora."""
        if len(self._parts) > 1:
            self._parts = ["".join(self._parts)]
        return self._parts[0] if self._parts else ""
