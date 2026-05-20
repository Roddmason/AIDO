from __future__ import annotations

import sqlite3


class PricingCatalog:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

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
        row = self.connection.execute(
            """
            SELECT input_price_per_mtok, cached_input_price_per_mtok, output_price_per_mtok,
                   reasoning_price_per_mtok, free_tier, source
            FROM model_catalog
            WHERE provider_id = ? AND model = ?
            """,
            (provider_id, model),
        ).fetchone()
        if not row:
            return None, "unknown_model"
        if row["free_tier"]:
            return 0.0, f"{row['source']}:free_tier"
        prices = {
            "input": row["input_price_per_mtok"],
            "cached": row["cached_input_price_per_mtok"],
            "output": row["output_price_per_mtok"],
            "reasoning": row["reasoning_price_per_mtok"],
        }
        if all(value is None for value in prices.values()):
            return None, "unknown_price"
        total = 0.0
        total += (float(prices["input"] or 0) * max(input_tokens - cached_input_tokens, 0)) / 1_000_000
        total += (float(prices["cached"] or 0) * cached_input_tokens) / 1_000_000
        total += (float(prices["output"] or 0) * output_tokens) / 1_000_000
        total += (float(prices["reasoning"] or 0) * reasoning_tokens) / 1_000_000
        return round(total, 6), str(row["source"] or "manual")
