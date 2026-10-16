"""Pure helpers that project model catalog entries and overlay observed performance onto them.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

MODEL_CATALOG_BASELINE_EVIDENCE_KIND = "model_catalog_baseline"


def performance_evidence_summary(evidence: list[Any]) -> tuple[str, int]:
    """Classify performance evidence as observed, unobserved prior or configured prior."""
    outcome_count = sum(
        1 for item in evidence if isinstance(item, dict) and str(item.get("kind") or "") == "outcome"
    )
    if outcome_count:
        return "observed", outcome_count
    if any(
        isinstance(item, dict) and str(item.get("kind") or "") == MODEL_CATALOG_BASELINE_EVIDENCE_KIND
        for item in evidence
    ):
        return "unobserved_prior", 0
    return "configured_prior", 0


def model_profile_key(model: dict[str, Any]) -> tuple[str, str, str]:
    """Return the provider, model and runtime identity key of a model."""
    return (
        str(model.get("providerId") or ""),
        str(model.get("model") or ""),
        str(model.get("runtime") or ""),
    )


def catalog_capabilities(
    catalog_model: dict[str, Any],
    runtime_status: dict[str, Any] | None,
    *,
    model_capabilities: frozenset[str] = frozenset(),
) -> list[str]:
    """Capacidades del perfil: las del runtime más, en cuentas locales, las opt-in de ese modelo."""
    runtime_capabilities = {
        str(item).strip().lower()
        for item in (runtime_status or {}).get("capabilities") or []
        if str(item).strip()
    } | {str(item).strip().lower() for item in model_capabilities if str(item).strip()}
    capabilities = runtime_capabilities & {"chat", "search"}
    if runtime_capabilities & {"code_edit", "issue_to_patch", "code"}:
        capabilities.add("code")
    if runtime_capabilities & {"code_review", "review"}:
        capabilities.add("review")
    end_to_end_capabilities = (
        ("supportsTools", "tools", {"tools", "issue_to_patch"}),
        ("supportsJson", "json", {"json"}),
        ("supportsVision", "vision", {"vision"}),
        ("supportsEmbeddings", "embeddings", {"embeddings"}),
        ("supportsRerank", "rerank", {"rerank"}),
        ("supportsReasoning", "reasoning", {"reasoning"}),
        ("supportsThinking", "reasoning", {"reasoning"}),
    )
    capabilities.update(
        capability
        for flag, capability, runtime_signals in end_to_end_capabilities
        if bool(catalog_model.get(flag)) and runtime_capabilities & runtime_signals
    )
    return sorted(capabilities)


def overlay_catalog_performance(
    catalog_profile: dict[str, Any],
    performance: dict[str, Any],
) -> dict[str, Any]:
    """Overlay observed performance on a catalog model profile."""
    evidence = list(catalog_profile["evidence"])
    evidence_ids = {str(item.get("id") or "") for item in evidence if isinstance(item, dict)}
    evidence.extend(
        item
        for item in performance.get("evidence") or []
        if not isinstance(item, dict)
        or not str(item.get("id") or "")
        or str(item.get("id") or "") not in evidence_ids
    )
    performance_status, performance_sample_count = performance_evidence_summary(
        list(performance.get("evidence") or [])
    )
    catalog_backed = performance.get("profileSource") == "model_catalog"
    legacy_overrides = (
        {}
        if catalog_backed
        else {
            key: performance.get(key)
            for key in (
                "capabilities",
                "contextWindow",
                "maxOutputTokens",
                "inputPricePerMtok",
                "cachedInputPricePerMtok",
                "outputPricePerMtok",
                "reasoningPricePerMtok",
            )
        }
    )
    return {
        **catalog_profile,
        **legacy_overrides,
        "id": performance["id"],
        "observedLatencyMs": performance.get("observedLatencyMs"),
        "observedSuccessRate": performance.get("observedSuccessRate"),
        "reworkRate": performance.get("reworkRate"),
        "totalObservedTokens": performance.get("totalObservedTokens"),
        "qualityScore": performance.get("qualityScore"),
        "evidence": evidence,
        "createdAt": performance.get("createdAt"),
        "performanceStatus": performance_status,
        "performanceSampleCount": performance_sample_count,
    }
