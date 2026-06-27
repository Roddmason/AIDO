"""Evalúa reglas de presupuesto por ámbito antes de autorizar una llamada de modelo.

Aplica primero el saldo restante y luego la regla de mayor especificidad (agent > workflow > provider
> role > global) que exceda costo o tokens estimados, traduciendo su acción (deny/fallback/require_approval/
warn) a una decisión allow/deny con motivo. No persiste nada: solo lee la tabla budget_rules.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BudgetResult:
    """Veredicto de presupuesto: si se permite, qué acción aplica y qué regla la disparó."""

    allowed: bool
    action: str
    reason: str
    matched_rule_id: str | None = None
    requires_approval: bool = False
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Serializa el veredicto a claves camelCase para la respuesta de la API."""
        return {
            "allowed": self.allowed,
            "action": self.action,
            "reason": self.reason,
            "matchedRuleId": self.matched_rule_id,
            "requiresApproval": self.requires_approval,
            "warnings": list(self.warnings),
        }


class BudgetRuleEvaluator:
    """Resuelve el veredicto de presupuesto consultando las reglas activas de la conexión SQLite."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def evaluate(
        self,
        *,
        role: str,
        provider_id: str | None,
        agent_id: str | None = None,
        workflow_run_id: str | None = None,
        estimated_cost_usd: float | None,
        estimated_tokens: int,
        budget_remaining_usd: float | None = None,
    ) -> BudgetResult:
        """Decide si una ejecución cabe en presupuesto aplicando saldo restante y la primera regla excedida.

        Returns:
            Veredicto allow por defecto ("within_budget") o el dictado por la regla de mayor especificidad
            cuyo costo/tokens estimados superen su límite.
        """
        if (
            budget_remaining_usd is not None
            and estimated_cost_usd is not None
            and estimated_cost_usd > budget_remaining_usd
        ):
            return BudgetResult(
                allowed=False,
                action="deny",
                reason="budget_remaining_exceeded",
            )
        for rule in self._matching_rules(
            role=role,
            provider_id=provider_id,
            agent_id=agent_id,
            workflow_run_id=workflow_run_id,
        ):
            cost_exceeded = (
                rule["max_cost_usd"] is not None
                and estimated_cost_usd is not None
                and float(estimated_cost_usd) > float(rule["max_cost_usd"])
            )
            tokens_exceeded = rule["max_tokens"] is not None and int(estimated_tokens or 0) > int(
                rule["max_tokens"]
            )
            if not cost_exceeded and not tokens_exceeded:
                continue
            action = str(rule["action_on_exceed"] or "require_approval")
            reason = "budget_denied" if action == "deny" else f"budget_{action}"
            if action == "deny":
                return BudgetResult(
                    allowed=False,
                    action=action,
                    reason=reason,
                    matched_rule_id=rule["id"],
                )
            if action == "fallback":
                return BudgetResult(
                    allowed=False,
                    action=action,
                    reason=reason,
                    matched_rule_id=rule["id"],
                )
            if action == "require_approval":
                return BudgetResult(
                    allowed=True,
                    action=action,
                    reason=reason,
                    matched_rule_id=rule["id"],
                    requires_approval=True,
                )
            if action == "warn":
                return BudgetResult(
                    allowed=True,
                    action=action,
                    reason=reason,
                    matched_rule_id=rule["id"],
                    warnings=("budget_rule_exceeded",),
                )
            return BudgetResult(
                allowed=False,
                action="deny",
                reason="budget_rule_action_unknown",
                matched_rule_id=rule["id"],
            )
        return BudgetResult(allowed=True, action="allow", reason="within_budget")

    def _matching_rules(
        self,
        *,
        role: str,
        provider_id: str | None,
        agent_id: str | None,
        workflow_run_id: str | None,
    ) -> list[sqlite3.Row]:
        rows = self.connection.execute(
            """
            SELECT *
            FROM budget_rules
            WHERE enabled = 1
              AND (
                (scope_type = 'agent' AND scope_id = ?)
                OR (scope_type = 'workflow' AND scope_id = ?)
                OR (scope_type = 'provider' AND scope_id = ?)
                OR (scope_type = 'role' AND scope_id = ?)
                OR (scope_type = 'global')
              )
            ORDER BY
              CASE scope_type
                WHEN 'agent' THEN 0
                WHEN 'workflow' THEN 1
                WHEN 'provider' THEN 2
                WHEN 'role' THEN 3
                ELSE 4
              END,
              period ASC,
              id ASC
            """,
            (agent_id, workflow_run_id, provider_id, role),
        ).fetchall()
        return list(rows)
