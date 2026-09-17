"""Ensamblado del slice de contexto de memoria: convierte resultados sueltos en un bloque acotado.

``search`` devuelve items rankeados; el briefing los ordena, los redacta y los ajusta a un
presupuesto explícito de caracteres que nunca se excede, dejando un bloque listo para consumir.

**Orden**, de más a menos significativo, y totalmente determinista:

1. ``kind`` por rango (``lesson`` antes que ``note``): una lección vale más que una nota suelta.
2. Similitud coseno descendente, redondeada a 9 decimales para que dos máquinas coincidan.
3. ``createdAt`` descendente: ante igual relevancia, primero lo más reciente.
4. ``id`` descendente como desempate total. Es obligatorio: ``createdAt`` tiene resolución de
   milisegundos y empata con facilidad, y el ``id`` es la clave primaria, así que no quedan empates.

No se reutiliza el orden de ``index.search_embedding``: ese ranking usa producto interno crudo en
float32 y ``np.argsort`` por defecto, que no es estable. El briefing recalcula el coseno en float64
y ordena en Python con clave total.

**Corte**: por item completo, nunca a media palabra. Se recorre la lista ya ordenada y se acumula
mientras el bloque siga cabiendo; el primer item que no cabe entero detiene el ensamblado. Detener
(en vez de saltar y seguir) es lo que hace el resultado monótono en el presupuesto: un presupuesto
mayor nunca produce un conjunto distinto, sólo uno mayor.

La redacción ocurre **antes** de medir, porque ``redact_secrets`` cambia el largo del texto: medir
primero y redactar después podría reventar el presupuesto y, peor, cortar un patrón de secreto
justo donde deja de ser reconocible.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

import numpy as np

from local_control_center.shared.redaction import redact_secrets

from .index import RetrievalIndex
from .repository import MemoryRepository

DEFAULT_BRIEFING_BUDGET_CHARS = 4000
MAX_BRIEFING_BUDGET_CHARS = 40000
KIND_RANK = {"lesson": 0, "note": 1}
UNKNOWN_KIND_RANK = 99
SCORE_DECIMALS = 9
ENTRY_SEPARATOR = "\n"

EMPTY_CORPUS_REASON = (
    "The project has no live memory item with a persisted embedding from a real provider account, "
    "so there is nothing to assemble a briefing from."
)


def _empty(status: str, reason: str, budget_chars: int) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "context": "",
        "entries": [],
        "budgetChars": budget_chars,
        "usedChars": 0,
        "includedCount": 0,
        "omittedCount": 0,
    }


def _render(entry: dict[str, Any]) -> str:
    """Renderiza una entrada del briefing en una línea estable y legible."""
    return f"- ({entry['kind']}) {entry['content']}"


class MemoryBriefingService:
    """Arma un bloque de contexto acotado y determinista a partir de la memoria del proyecto."""

    def __init__(self, memory: MemoryRepository, index: RetrievalIndex):
        self.memory = memory
        self.index = index

    def build(
        self,
        *,
        project_id: str,
        scope: str,
        query: str,
        budget_chars: int = DEFAULT_BRIEFING_BUDGET_CHARS,
        scope_id: str | None = None,
    ) -> dict[str, Any]:
        """Ensambla el briefing del scope pedido, o devuelve el estado degradado correspondiente.

        Sin proveedor real de embeddings no se entrega un briefing parcial disfrazado de completo:
        se devuelve el mismo vocabulario de estados que ya usa el índice.
        """
        budget = max(0, min(int(budget_chars), MAX_BRIEFING_BUDGET_CHARS))
        provider_status = self.index.embedding_provider.status()
        if provider_status.get("status") != "available":
            return _empty(
                str(provider_status.get("status") or "configuration_required"),
                str(provider_status.get("reason") or ""),
                budget,
            )
        try:
            query_embedding = self.index.embedding_provider.embed_text(query)
        except Exception as error:
            return _empty("unavailable", f"Query embedding failed: {error}", budget)

        candidates = [
            row
            for row in self.memory.list_embedded_memory_items(project_id)
            if row["scope"] == scope and (scope_id is None or row["scopeId"] == scope_id)
        ]
        if not candidates:
            return _empty("empty_corpus", EMPTY_CORPUS_REASON, budget)

        scored = self._scored(candidates, query_embedding)
        if scored is None:
            return _empty(
                "blocked",
                "Memory embeddings have inconsistent dimensions; the briefing requires one model.",
                budget,
            )
        return self._assemble(self._ordered(scored), budget)

    def _scored(
        self, candidates: list[dict[str, Any]], query_embedding: list[float]
    ) -> list[dict[str, Any]] | None:
        """Calcula el coseno de cada candidato contra la consulta, en float64 y redondeado."""
        query_vector = np.asarray(query_embedding, dtype="float64")
        query_norm = float(np.linalg.norm(query_vector))
        if query_norm == 0.0:
            return None
        entries: list[dict[str, Any]] = []
        for row in candidates:
            vector = np.asarray(row["embedding"], dtype="float64")
            if len(vector) != len(query_vector) or not np.all(np.isfinite(vector)):
                return None
            norm = float(np.linalg.norm(vector))
            if norm == 0.0:
                return None
            cosine = round(float(np.dot(vector, query_vector) / (norm * query_norm)), SCORE_DECIMALS)
            entries.append(
                {
                    "id": row["memoryItemId"],
                    "kind": row["kind"],
                    "scope": row["scope"],
                    "scopeId": row["scopeId"],
                    "content": str(redact_secrets(row["content"] or "")),
                    "cosine": cosine,
                    "createdAt": row["createdAt"],
                }
            )
        return entries

    def _ordered(self, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Aplica el orden documentado con dos pasadas estables, sin empates posibles al final."""
        by_recency = sorted(entries, key=lambda entry: (entry["createdAt"], entry["id"]), reverse=True)
        return sorted(
            by_recency,
            key=lambda entry: (
                KIND_RANK.get(entry["kind"], UNKNOWN_KIND_RANK),
                -entry["cosine"],
            ),
        )

    def _assemble(self, ordered: list[dict[str, Any]], budget: int) -> dict[str, Any]:
        """Acumula entradas completas hasta el primer item que no cabe, sin exceder el presupuesto."""
        included: list[dict[str, Any]] = []
        rendered: list[str] = []
        used = 0
        for entry in ordered:
            line = _render(entry)
            projected = used + len(line) + (len(ENTRY_SEPARATOR) if rendered else 0)
            if projected > budget:
                break
            rendered.append(line)
            included.append(entry)
            used = projected
        context = ENTRY_SEPARATOR.join(rendered)
        return {
            "status": "available",
            "reason": "",
            "context": context,
            "entries": included,
            "budgetChars": budget,
            "usedChars": len(context),
            "includedCount": len(included),
            "omittedCount": len(ordered) - len(included),
        }
