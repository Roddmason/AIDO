"""AIDO backend source module.

Copyright (c) AIDO.
Author: Roddmason.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import I18nCatalog
from .repository import I18nRepository


DEFAULT_CATALOG_PATH = Path(__file__).with_name("default_catalog.json")


def load_default_catalog() -> I18nCatalog:
    payload = json.loads(DEFAULT_CATALOG_PATH.read_text(encoding="utf-8"))
    return I18nCatalog.model_validate(payload)


def get_catalog(repository: I18nRepository) -> dict[str, Any]:
    return repository.ensure_seeded(load_default_catalog())


def replace_catalog(repository: I18nRepository, body: dict[str, Any]) -> dict[str, Any]:
    catalog = I18nCatalog.model_validate(body)
    return repository.replace_catalog(catalog)
