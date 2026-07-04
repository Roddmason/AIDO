"""Servicio de costo/rendimiento por hilo: agrega, de forma honesta, el gasto real de un thread.

Un thread no tiene columna propia en ``usage_ledger``/``routing_decisions``; su ejecución se ancla por el
prefijo de ``task_id`` que el ``ProductLoopCoordinator`` deriva del ``loopId`` (``product-loop-<12>`` y sus
hermanos ``product-owner-``/``product-loop-research-``). Este servicio resuelve esos prefijos desde los
eventos del hilo y proyecta las siete señales accionables del inspector: presupuesto usado, estimado vs
actual, tokens conocidos/desconocidos, modelo elegido, razón, alternativa más barata y calidad/retrabajo.

Invariante central (alineado con el motor y con el gate de front): un costo o token desconocido se modela
como ``None``, nunca como ``0``. Sumar solo conoce valores presentes; si no hay ninguno, el total es
``None`` y el estado es ``unknown``. Solo lee: no ejecuta rutas ni muta estado.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.agents.model_benchmarks import ModelBenchmarkStore
from local_control_center.agents.routing_profiles import RoutingProfileStore
from local_control_center.agents.usage_ledger import UsageLedger
from local_control_center.product_loop.repository import ProductLoopRepository

from .repository import ThreadsRepository

DEVELOPER_ROLE = "developer"


def _task_prefixes_for_loop(loop_id: str) -> list[str]:
    """Deriva los prefijos de ``task_id`` que el coordinator asigna a un ``loopId``.

    Espeja ``ProductLoopCoordinator``: la ejecución del developer usa ``product-loop-<12>`` (y subtareas
    como ``.review_diff`` que comparten ese prefijo), el product owner ``product-owner-<12>`` y el research
    ``product-loop-research-<12>``. El slice de 12 caracteres se toma del id sin el prefijo ``product-loop-``.
    """
    derived = loop_id.replace("product-loop-", "")[:12]
    return [
        f"product-loop-{derived}",
        f"product-owner-{derived}",
        f"product-loop-research-{derived}",
    ]


class ThreadCostPerformanceService:
    """Agrega el costo/rendimiento real de un hilo desde el ledger, el ruteo y el loop, sin fabricar datos."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.threads = ThreadsRepository(connection)
        self.usage = UsageLedger(connection)
        self.routing = RoutingProfileStore(connection)
        self.loops = ProductLoopRepository(connection)
        self.benchmarks = ModelBenchmarkStore(connection)

    def snapshot(self, thread_id: str, *, developer_role: str = DEVELOPER_ROLE) -> dict[str, Any]:
        """Devuelve el snapshot de costo/rendimiento del hilo.

        Raises:
            KeyError: si el hilo no existe.
        """
        self.threads.get_thread(thread_id)
        loop_ids = self._loop_ids(thread_id)
        prefixes = self._task_prefixes(loop_ids)
        usage_rows = self.usage.list_usage_for_task_prefixes(prefixes)
        decisions = self.routing.list_routing_decisions_for_task_prefixes(prefixes)
        model_chosen, reason, cheaper = self._routing_view(decisions)
        return {
            "threadId": thread_id,
            "loopIds": loop_ids,
            "hasData": bool(usage_rows) or bool(decisions),
            "budgetUsed": self._budget_used(usage_rows, developer_role),
            "cost": self._cost_summary(usage_rows),
            "tokens": self._token_summary(usage_rows),
            "modelChosen": model_chosen,
            "reasonSelected": reason,
            "cheaperAlternative": cheaper,
            "qualityRework": self._quality_rework(loop_ids, model_chosen),
        }

    def _loop_ids(self, thread_id: str) -> list[str]:
        """Colecciona los ``loopId`` distintos del hilo desde sus eventos, del más antiguo al más reciente."""
        ordered: list[str] = []
        for event in self.threads.list_events(thread_id):
            payload = event.get("payload") or {}
            loop_id = payload.get("loopId")
            if isinstance(loop_id, str) and loop_id and loop_id not in ordered:
                ordered.append(loop_id)
        return ordered

    def _task_prefixes(self, loop_ids: list[str]) -> list[str]:
        prefixes: list[str] = []
        for loop_id in loop_ids:
            for prefix in _task_prefixes_for_loop(loop_id):
                if prefix not in prefixes:
                    prefixes.append(prefix)
        return prefixes

    def _budget_used(self, usage_rows: list[dict[str, Any]], developer_role: str) -> dict[str, Any]:
        """Gasto acumulado del hilo (actual si se conoce, si no estimado) contra el cap por-run efectivo."""
        used_values: list[float] = []
        has_actual = False
        has_estimated = False
        for row in usage_rows:
            actual = row.get("actualCostUsd")
            estimated = row.get("estimatedCostUsd")
            if actual is not None:
                used_values.append(float(actual))
                has_actual = True
            elif estimated is not None:
                used_values.append(float(estimated))
                has_estimated = True
        used_usd = sum(used_values) if used_values else None
        if not used_values:
            cost_status = "unknown"
        elif has_actual and has_estimated:
            cost_status = "mixed"
        elif has_actual:
            cost_status = "actual"
        else:
            cost_status = "estimated"
        cap_usd, cap_scope = self._per_run_cap(developer_role)
        return {
            "usedUsd": used_usd,
            "costStatus": cost_status,
            "callCount": len(usage_rows),
            "perRunCapUsd": cap_usd,
            "capScope": cap_scope,
        }

    def _per_run_cap(self, developer_role: str) -> tuple[float | None, str | None]:
        """Cap de costo por-run efectivo del rol ejecutor (política de rol), honesto ``None`` si no aplica."""
        try:
            policy = self.routing.get_role_policy(developer_role)
        except KeyError:
            return None, None
        cap = policy.get("maxCostPerTaskUsd")
        if cap is None or float(cap) <= 0:
            return None, None
        return float(cap), f"role:{developer_role}"

    def _cost_summary(self, usage_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Estimado vs actual como sumas honestas: ``None`` cuando ninguna fila aporta el valor conocido."""
        estimated = [
            float(row["estimatedCostUsd"]) for row in usage_rows if row.get("estimatedCostUsd") is not None
        ]
        actual = [float(row["actualCostUsd"]) for row in usage_rows if row.get("actualCostUsd") is not None]
        return {
            "estimatedCostUsd": sum(estimated) if estimated else None,
            "actualCostUsd": sum(actual) if actual else None,
        }

    def _token_summary(self, usage_rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Tokens totales y el desglose conocidos/desconocidos por llamada según ``tokenStatus``."""
        total_tokens = sum(int(row.get("totalTokens") or 0) for row in usage_rows)
        known_calls = sum(1 for row in usage_rows if row.get("tokenStatus") == "actual")
        unknown_calls = len(usage_rows) - known_calls
        if not usage_rows:
            token_status = "none"
        elif unknown_calls == 0:
            token_status = "actual"
        elif known_calls == 0:
            token_status = "unknown"
        else:
            token_status = "partial"
        return {
            "totalTokens": total_tokens,
            "knownCalls": known_calls,
            "unknownCalls": unknown_calls,
            "tokenStatus": token_status,
            "callCount": len(usage_rows),
        }

    def _routing_view(
        self, decisions: list[dict[str, Any]]
    ) -> tuple[dict[str, Any] | None, str | None, dict[str, Any] | None]:
        """Modelo elegido, razón y alternativa más barata desde la decisión de ruteo más reciente del hilo."""
        if not decisions:
            return None, None, None
        latest = decisions[0]
        selected_model = latest.get("selectedModel")
        model_chosen = (
            {
                "provider": latest.get("selectedProvider"),
                "model": selected_model,
                "runtime": latest.get("selectedRuntime"),
                "effort": latest.get("selectedEffort"),
                "mode": latest.get("mode"),
            }
            if selected_model
            else None
        )
        reason = latest.get("decisionReason") or None
        cheaper = self._cheaper_alternative(latest)
        return model_chosen, reason, cheaper

    def _cheaper_alternative(self, decision: dict[str, Any]) -> dict[str, Any] | None:
        """Candidato con precio conocido estrictamente más barato que el elegido; ``None`` si no lo hay.

        Si el costo del elegido es desconocido no se puede comparar honestamente y no se propone alternativa.
        """
        selected_cost = decision.get("estimatedCostUsd")
        if selected_cost is None:
            return None
        selected_key = (decision.get("selectedProvider"), decision.get("selectedModel"))
        best: dict[str, Any] | None = None
        best_cost = float(selected_cost)
        for candidate in decision.get("candidates") or []:
            if not candidate.get("priceKnown"):
                continue
            cost = candidate.get("estimatedCostUsd")
            if cost is None:
                continue
            if (candidate.get("provider"), candidate.get("model")) == selected_key:
                continue
            if float(cost) < best_cost:
                best = candidate
                best_cost = float(cost)
        if best is None:
            return None
        return {
            "provider": best.get("provider"),
            "model": best.get("model"),
            "runtime": best.get("runtime"),
            "estimatedCostUsd": float(best_cost),
            "deltaUsd": float(selected_cost) - float(best_cost),
            "priceKnown": True,
        }

    def _quality_rework(self, loop_ids: list[str], model_chosen: dict[str, Any] | None) -> dict[str, Any]:
        """Retrabajo real del hilo (FSM) + calidad del modelo elegido (benchmark), rotulada y honesta."""
        rework_rounds, max_rework = self._rework_counter(loop_ids)
        benchmark = self._model_benchmark(model_chosen)
        return {
            "reworkRounds": rework_rounds,
            "maxReworkRounds": max_rework,
            "modelSuccessRate": benchmark.get("successRate") if benchmark else None,
            "modelQaPassRate": benchmark.get("qaPassRate") if benchmark else None,
            "modelReworkRate": benchmark.get("reworkRate") if benchmark else None,
            "benchmarkInsufficientData": bool(benchmark.get("insufficientData")) if benchmark else True,
            "modelLabel": self._model_label(model_chosen),
        }

    def _rework_counter(self, loop_ids: list[str]) -> tuple[int | None, int | None]:
        if not loop_ids:
            return None, None
        try:
            loop = self.loops.get_loop(loop_ids[-1])
        except KeyError:
            return None, None
        fsm = (loop.get("context") or {}).get("fsm") or {}
        usage = fsm.get("usage") or {}
        policy = fsm.get("policy") or {}
        rounds = usage.get("reworkRounds")
        max_rounds = policy.get("maxReworkRounds")
        return (
            int(rounds) if isinstance(rounds, (int, float)) else None,
            int(max_rounds) if isinstance(max_rounds, (int, float)) else None,
        )

    def _model_benchmark(self, model_chosen: dict[str, Any] | None) -> dict[str, Any] | None:
        if not model_chosen or not model_chosen.get("model"):
            return None
        provider = model_chosen.get("provider")
        model = model_chosen.get("model")
        matches = [
            row
            for row in self.benchmarks.list_benchmarks()
            if row.get("providerId") == provider and row.get("model") == model
        ]
        if not matches:
            return None
        role_match = next((row for row in matches if row.get("role") == DEVELOPER_ROLE), None)
        wildcard = next((row for row in matches if row.get("role") in (None, "*")), None)
        return role_match or wildcard or matches[0]

    def _model_label(self, model_chosen: dict[str, Any] | None) -> str | None:
        if not model_chosen or not model_chosen.get("model"):
            return None
        provider = model_chosen.get("provider")
        model = model_chosen.get("model")
        return f"{provider}/{model}" if provider else str(model)
