"""Casos de uso del catálogo i18n: media entre la API y el repositorio.

Carga y valida el catálogo semilla empaquetado, garantiza el sembrado en la primera
lectura y valida el cuerpo entrante antes de persistir un reemplazo. Aquí se aplica la
validación Pydantic; la atomicidad de la escritura vive en el repositorio.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import I18nCatalog
from .repository import I18nRepository

DEFAULT_CATALOG_PATH = Path(__file__).with_name("default_catalog.json")


def load_default_catalog() -> I18nCatalog:
    """Lee y valida el catálogo semilla bilingüe empaquetado junto al módulo."""
    payload = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    return I18nCatalog.model_validate(payload)


def get_catalog(repository: I18nRepository) -> dict[str, Any]:
    """Devuelve el catálogo persistido, sembrando los valores por defecto si falta alguno."""
    return repository.ensure_seeded(load_default_catalog())


def replace_catalog(repository: I18nRepository, body: dict[str, Any]) -> dict[str, Any]:
    """Valida el cuerpo entrante contra el esquema y reemplaza el catálogo completo."""
    catalog = I18nCatalog.model_validate(body)
    return repository.replace_catalog(catalog)
