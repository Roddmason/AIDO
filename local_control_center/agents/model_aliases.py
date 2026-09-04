"""Resuelve intención semántica contra el catálogo habilitado, sin nombres de modelo inferidos.

Los catálogos seed son documentación de compatibilidad, no evidencia de disponibilidad actual.
Una selección manual o un discovery explícito habilitado sí puede participar en la resolución.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3

MODEL_ALIASES = frozenset(
    {"fast_analysis", "deep_analysis", "fast_coding", "deep_coding", "architecture", "review", "low_cost"}
)
LEGACY_PROFILE_ALIASES = {
    "codex_gpt55_developer": "deep_coding",
    "codex_gpt55_reviewer": "review",
    "codex_gpt55_xhigh_architect": "architecture",
    "codex_mini_analyst": "fast_analysis",
    "claude_sonnet_developer": "fast_coding",
    "claude_sonnet_qa": "review",
    "claude_opus_planner": "deep_analysis",
    "claude_opus_xhigh_architect": "architecture",
    "claude_opus_max_requires_approval": "deep_coding",
    "claude_opusplan_if_supported": "deep_analysis",
}


def resolve_model_alias(connection: sqlite3.Connection | None, *, provider_id: str, alias: str) -> str:
    """Elige sólo un modelo presente y habilitado; sin candidato devuelve un bloqueo explícito."""
    alias = LEGACY_PROFILE_ALIASES.get(alias, alias)
    if alias not in MODEL_ALIASES:
        raise ValueError("Unknown semantic model alias.")
    if connection is None:
        raise ValueError("model_configuration_required: a current enabled catalog is required.")
    rows = connection.execute(
        """SELECT * FROM model_catalog WHERE provider_id=? AND enabled=1
        AND (source IN ('manual', 'operator_override', 'provider', 'discovered')
             OR source LIKE 'provider_account_sync:%') ORDER BY model""",
        (provider_id,),
    ).fetchall()
    deep = alias in {"deep_analysis", "deep_coding", "architecture", "review"}
    if deep:
        rows = [row for row in rows if row["supports_reasoning"]]
    if alias == "low_cost":
        rows = [
            row
            for row in rows
            if row["input_price_per_mtok"] is not None and row["output_price_per_mtok"] is not None
        ]
    if not rows:
        raise ValueError(f"model_configuration_required: no enabled {provider_id} model supports {alias}.")

    def rank(row):
        price = (row["input_price_per_mtok"] or 0) + (row["output_price_per_mtok"] or 0)
        unknown_price = row["input_price_per_mtok"] is None or row["output_price_per_mtok"] is None
        if deep:
            return (-row["context_window"], unknown_price, price, row["model"])
        return (unknown_price, price, -row["context_window"], row["model"])

    return str(min(rows, key=rank)["model"])
