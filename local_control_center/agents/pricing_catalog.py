"""Estimates per-call model cost from the catalog and flags pricing staleness.

Reads `model_catalog` pricing (per-million-token rates, free-tier and source) to turn
token counts into a USD estimate, signalling when the price is unknown or stale. Pure
read path: it never writes and never executes a model call.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

STALE_AFTER_DAYS = 90


class PricingCatalog:
    """Read-only view over `model_catalog` pricing used to estimate model-call cost."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def estimate(
        self,
        *,
        provider_id: str,
        model: str,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> dict[str, Any]:
        """Estimate the USD cost of a call and report price provenance and freshness.

        Returns `priceKnown=False` when the model or its prices are absent; cached input
        tokens are billed at the cached rate and excluded from the standard input rate.
        """
        row = self.connection.execute(
            """
            SELECT input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
                   reasoning_price_per_mtok, free_tier, source, updated_at
            FROM model_catalog
            WHERE provider_id = ? AND model = ?
            """,
            (provider_id, model),
        ).fetchone()
        if not row:
            return {
                "estimatedCostUsd": None,
                "source": "unknown_model",
                "staleness": "unknown",
                "priceKnown": False,
                "freeTier": False,
            }
        source = str(row["source"] or "manual")
        free_tier = bool(row["free_tier"])
        staleness = self._staleness(source=source, updated_at=row["updated_at"])
        if free_tier:
            return {
                "estimatedCostUsd": 0.0,
                "source": f"{source}:free_tier",
                "staleness": staleness,
                "priceKnown": True,
                "freeTier": True,
            }
        prices = {
            "input": row["input_price_per_mtok"],
            "cached": row["cached_input_price_per_mtok"],
            "output": row["output_price_per_mtok"],
            "reasoning": row["reasoning_price_per_mtok"],
        }
        if all(value is None for value in prices.values()):
            return {
                "estimatedCostUsd": None,
                "source": "unknown_price",
                "staleness": "unknown",
                "priceKnown": False,
                "freeTier": False,
            }
        total = 0.0
        total += (float(prices["input"] or 0) * max(input_tokens - cached_input_tokens, 0)) / 1_000_000
        total += (float(prices["cached"] or 0) * cached_input_tokens) / 1_000_000
        total += (float(prices["output"] or 0) * output_tokens) / 1_000_000
        total += (float(prices["reasoning"] or 0) * reasoning_tokens) / 1_000_000
        return {
            "estimatedCostUsd": round(total, 6),
            "source": source,
            "staleness": staleness,
            "priceKnown": True,
            "freeTier": False,
        }

    def estimate_cost(
        self,
        *,
        provider_id: str,
        model: str,
        input_tokens: int = 0,
        cached_input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> tuple[float | None, str]:
        """Estimate cost as a `(cost_usd_or_none, source)` tuple for callers that skip metadata."""
        estimate = self.estimate(
            provider_id=provider_id,
            model=model,
            input_tokens=input_tokens,
            cached_input_tokens=cached_input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
        )
        return estimate["estimatedCostUsd"], estimate["source"]

    def _staleness(self, *, source: str, updated_at: str | None) -> str:
        if not updated_at or source == "manual_seed":
            return "unknown"
        try:
            normalized = updated_at.replace("Z", "+00:00")
            updated = datetime.fromisoformat(normalized)
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
        except ValueError:
            return "unknown"
        age = datetime.now(UTC) - updated.astimezone(UTC)
        return "stale" if age.days > STALE_AFTER_DAYS else "fresh"
