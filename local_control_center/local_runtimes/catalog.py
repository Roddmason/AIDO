"""Identidad de catálogo de las cuentas locales: qué cuentas son endpoints locales y con qué perfil.

@author Rodrigo Mason
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from local_control_center.agents.endpoint_locality import catalog_entry_for_account
from local_control_center.agents.provider_catalog import ProviderCatalogEntry


def local_endpoint_entry(account: Mapping[str, Any]) -> ProviderCatalogEntry | None:
    """Entrada local del catálogo (Ollama incluido) de una cuenta con ``baseUrl``; ``None`` si no aplica."""
    if not str(account.get("baseUrl") or "").strip():
        return None
    entry = catalog_entry_for_account(account)
    return entry if entry is not None and entry.provider_type == "local" else None


def local_profile_entry(account: Mapping[str, Any]) -> ProviderCatalogEntry | None:
    """Entrada local con ``LocalRuntimeProfile`` (llama.cpp, LM Studio, vLLM, genérico); ``None`` para Ollama."""
    entry = local_endpoint_entry(account)
    return entry if entry is not None and entry.local_profile is not None else None
