"""Detección de memorias contradictorias dentro de un mismo scope de proyecto.

Un conflicto es un par de memory items vivos que comparten
``(project_id, scope, scope_id, kind)``, tienen contenido distinto (hash distinto) y una similitud
coseno que es un *outlier* de la distribución del propio corpus del proyecto. El detector sólo
registra y emite el hallazgo: nunca sobrescribe contenido ni marca ``supersedes_id``, porque la
supersesión sigue siendo una decisión explícita de una persona o de un agente con evidencia.

El umbral no es una constante inventada. Se deriva del corpus con un estimador robusto
(mediana + k·MAD): a diferencia de un percentil, que por construcción siempre marca el N% superior
y por lo tanto nunca puede afirmar "no hay conflictos", la valla robusta no marca nada sobre un
corpus limpio. Ver ``docs/adr/ADR-003-memory-conflict-detection.md`` para la calibración medida y
su riesgo residual.

Sin embeddings reales, con dimensionalidad demasiado baja o con un corpus demasiado chico, devuelve
un estado degradado explícito en vez de adivinar, igual que hace ``index.py``.

@author Rodrigo Mason
"""

from __future__ import annotations

import itertools
from typing import Any

import numpy as np

from local_control_center.shared.event_bus import EventBus

from .repository import MemoryRepository

DETECTOR_NAME = "cosine-mad-v1"
DEFAULT_MAD_MULTIPLIER = 5.0
MAD_TO_SIGMA = 1.4826
MIN_BACKGROUND_PAIRS = 15
MIN_DIMENSIONS = 128
SCORE_DECIMALS = 9

CONFIGURATION_REQUIRED_REASON = (
    "No persisted real memory embeddings are available for this project. Configure a real embedding "
    "provider and write embeddings before running conflict detection."
)
INSUFFICIENT_CORPUS_REASON = (
    "The project corpus has {pairs} comparable pairs; at least {minimum} are required before a "
    "corpus-derived threshold is meaningful."
)
LOW_DIMENSION_REASON = (
    "Embeddings have {dimensions} dimensions; the robust threshold is only calibrated at {minimum} "
    "or more, because below that the random cosine spread reaches the top of the cosine range."
)
MIXED_DIMENSION_REASON = (
    "Memory embeddings have inconsistent dimensions; conflict detection requires one embedding model."
)
DEGENERATE_REASON = (
    "The corpus similarity distribution has zero dispersion, so no outlier threshold can be derived."
)


def _degraded(status: str, reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "detector": DETECTOR_NAME,
        "threshold": 0.0,
        "thresholdSource": "unavailable",
        "comparedPairs": 0,
        "conflicts": [],
        **extra,
    }


class MemoryConflictDetector:
    """Detecta y registra pares de memoria contradictorios sin mutar la memoria existente."""

    def __init__(self, memory: MemoryRepository, events: EventBus):
        self.memory = memory
        self.events = events

    def detect(
        self,
        *,
        project_id: str,
        multiplier: float = DEFAULT_MAD_MULTIPLIER,
        threshold: float | None = None,
    ) -> dict[str, Any]:
        """Recorre el corpus del proyecto y registra los pares que superan la valla.

        ``threshold`` explícito sobreescribe el umbral derivado del corpus; ``multiplier`` ajusta
        cuán lejos de la mediana debe estar un par para considerarse outlier.
        """
        rows = self.memory.list_conflict_candidates(project_id)
        vectors = self._vectors(rows)
        if isinstance(vectors, dict):
            return vectors
        if not rows:
            return _degraded("configuration_required", CONFIGURATION_REQUIRED_REASON)

        dimensions = len(vectors[0])
        if dimensions < MIN_DIMENSIONS and threshold is None:
            return _degraded(
                "low_dimensionality",
                LOW_DIMENSION_REASON.format(dimensions=dimensions, minimum=MIN_DIMENSIONS),
                dimensions=dimensions,
            )

        scored = self._scored_pairs(rows, vectors)
        if threshold is None:
            derived = self._derive_threshold([score for score, _, _ in scored], multiplier)
            if isinstance(derived, dict):
                return derived
            effective, source = derived, f"corpus_median_plus_{multiplier:g}_mad"
        else:
            effective, source = float(threshold), "explicit"

        conflicts = [
            self._record(project_id, rows[left], rows[right], score, effective)
            for score, left, right in scored
            if self._is_candidate(rows[left], rows[right]) and score > effective
        ]
        return {
            "status": "available",
            "reason": "",
            "detector": DETECTOR_NAME,
            "threshold": effective,
            "thresholdSource": source,
            "comparedPairs": len(scored),
            "dimensions": dimensions,
            "conflicts": conflicts,
        }

    def _vectors(self, rows: list[dict[str, Any]]) -> list[np.ndarray] | dict[str, Any]:
        """Convierte embeddings persistidos a vectores float64 usables, o devuelve el estado degradado."""
        vectors: list[np.ndarray] = []
        expected: int | None = None
        for row in rows:
            embedding = row["embedding"]
            if not isinstance(embedding, list) or not embedding:
                return _degraded("configuration_required", CONFIGURATION_REQUIRED_REASON)
            vector = np.asarray(embedding, dtype="float64")
            if not np.all(np.isfinite(vector)) or float(np.linalg.norm(vector)) == 0.0:
                return _degraded(
                    "blocked",
                    f"Invalid embedding payload for memory item {row['memoryItemId']}.",
                )
            expected = expected or len(vector)
            if len(vector) != expected:
                return _degraded("blocked", MIXED_DIMENSION_REASON)
            vectors.append(vector / np.linalg.norm(vector))
        return vectors

    def _scored_pairs(
        self, rows: list[dict[str, Any]], vectors: list[np.ndarray]
    ) -> list[tuple[float, int, int]]:
        """Calcula el coseno de cada par con contenido distinto, redondeado para ser reproducible.

        Los pares de hash idéntico quedan fuera: son duplicados, no contradicciones, y dejarlos
        dentro arrastraría la distribución hacia 1.0 enmascarando los conflictos reales.
        """
        scored: list[tuple[float, int, int]] = []
        for left, right in itertools.combinations(range(len(rows)), 2):
            if rows[left]["hash"] == rows[right]["hash"]:
                continue
            score = round(float(np.dot(vectors[left], vectors[right])), SCORE_DECIMALS)
            scored.append((score, left, right))
        return scored

    def _derive_threshold(self, scores: list[float], multiplier: float) -> float | dict[str, Any]:
        """Deriva la valla robusta mediana + k·MAD, o el estado degradado si el corpus no alcanza."""
        if len(scores) < MIN_BACKGROUND_PAIRS:
            return _degraded(
                "insufficient_corpus",
                INSUFFICIENT_CORPUS_REASON.format(pairs=len(scores), minimum=MIN_BACKGROUND_PAIRS),
                comparedPairs=len(scores),
            )
        values = np.asarray(sorted(scores), dtype="float64")
        median = float(np.median(values))
        deviation = float(np.median(np.abs(values - median))) * MAD_TO_SIGMA
        if deviation <= 0.0:
            return _degraded("degenerate_distribution", DEGENERATE_REASON, comparedPairs=len(scores))
        return round(median + multiplier * deviation, SCORE_DECIMALS)

    def _is_candidate(self, left: dict[str, Any], right: dict[str, Any]) -> bool:
        """Sólo un par del mismo scope, scope_id y kind puede ser una contradicción."""
        return (left["scope"], left["scopeId"], left["kind"]) == (
            right["scope"],
            right["scopeId"],
            right["kind"],
        )

    def _record(
        self,
        project_id: str,
        left: dict[str, Any],
        right: dict[str, Any],
        score: float,
        threshold: float,
    ) -> dict[str, Any]:
        """Guarda el conflicto y emite su evento, sin tocar los memory items involucrados."""
        conflict = self.memory.record_memory_conflict(
            project_id=project_id,
            left_memory_item_id=left["memoryItemId"],
            right_memory_item_id=right["memoryItemId"],
            scope=left["scope"],
            scope_id=left["scopeId"],
            kind=left["kind"],
            score=score,
            threshold=threshold,
            detector=DETECTOR_NAME,
        )
        self.events.record_event(
            project_id=project_id,
            event_type="memory.conflict_detected",
            payload={
                "memoryConflictId": conflict["id"],
                "leftMemoryItemId": conflict["leftMemoryItemId"],
                "rightMemoryItemId": conflict["rightMemoryItemId"],
                "score": conflict["score"],
                "threshold": conflict["threshold"],
                "detector": DETECTOR_NAME,
            },
        )
        return conflict
