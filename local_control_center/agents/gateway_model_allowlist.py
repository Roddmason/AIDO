"""Allowlist curada de modelos por gateway de auto-ruteo (OmniRoute), compartida por el sync y el script.

Un gateway como OmniRoute anuncia en ``/models`` todos los modelos upstream que sabe enrutar, también
los de providers donde el operador no tiene cuenta: habilitarlos todos deja al runtime validando o
ejecutando modelos que el gateway rechaza. El sync de modelos preselecciona solo los de la allowlist
(``scripts/omniroute_models.json``, la misma que aplica ``scripts/setup_omniroute.py``) y el operador
puede habilitar otros a mano; sin allowlist legible el sync conserva el comportamiento anterior.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
OMNIROUTE_MODELS_FILE = REPO_ROOT / "scripts" / "omniroute_models.json"
#: Archivo de allowlist por id de catálogo de gateway.
GATEWAY_MODEL_ALLOWLIST_FILES: Mapping[str, Path] = {"omniroute": OMNIROUTE_MODELS_FILE}
#: Metadatos de la allowlist que el sync copia a la fila del catálogo (el gateway no los informa).
_ALLOWLIST_METADATA_FIELDS = ("supportsTools", "supportsJson", "supportsReasoning")
_ALLOWLIST_LIMIT_FIELDS = ("contextWindow", "maxOutputTokens")


def parse_model_allowlist(document: Any) -> list[dict[str, Any]]:
    """Valida el documento de allowlist y devuelve sus entradas con ``model`` no vacío.

    Raises:
        ValueError: el documento no declara una lista ``models`` no vacía de objetos con ``model``.
    """
    models = document.get("models") if isinstance(document, dict) else None
    if not isinstance(models, list) or not models:
        raise ValueError("the allowlist declares no model in 'models'")
    entries: list[dict[str, Any]] = []
    for entry in models:
        if not isinstance(entry, dict) or not str(entry.get("model") or "").strip():
            raise ValueError("every allowlist entry needs a non-empty 'model'")
        entries.append({**entry, "model": str(entry["model"]).strip()})
    return entries


def load_model_allowlist(path: Path) -> list[dict[str, Any]]:
    """Lee y valida una allowlist JSON.

    Raises:
        FileNotFoundError: el archivo no existe.
        ValueError: el JSON es inválido o no declara modelos.
    """
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"invalid JSON: {error}") from error
    return parse_model_allowlist(document)


def gateway_model_allowlist(catalog_id: str) -> dict[str, dict[str, Any]] | None:
    """Entradas de la allowlist del gateway por modelo, o ``None`` si no tiene una legible.

    Una allowlist ausente o inválida no rompe el sync: se registra y el sync habilita lo anunciado.
    """
    path = GATEWAY_MODEL_ALLOWLIST_FILES.get(catalog_id)
    if path is None:
        return None
    try:
        entries = load_model_allowlist(path)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as error:
        logger.warning(
            "gateway_model_allowlist.unreadable", extra={"catalogId": catalog_id, "error": str(error)}
        )
        return None
    return {entry["model"]: entry for entry in entries}


def apply_allowlist_entry(model: dict[str, Any], entry: Mapping[str, Any] | None) -> dict[str, Any]:
    """Fila de modelo sincronizada con los metadatos declarados en su entrada de allowlist (si hay)."""
    if entry is None:
        return model
    merged = dict(model)
    for field in _ALLOWLIST_METADATA_FIELDS:
        if field in entry:
            merged[field] = bool(entry[field])
    for field in _ALLOWLIST_LIMIT_FIELDS:
        if entry.get(field):
            merged[field] = int(entry[field])
    return merged
