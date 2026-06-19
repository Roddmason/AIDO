"""Esquema y reglas de validez del catálogo de traducciones de la UI.

Define los modelos Pydantic de idioma y catálogo, normaliza códigos BCP-47 y
garantiza la invariante de completitud: cada clave debe tener un valor no vacío
para todo idioma habilitado, evitando que se persista un catálogo con huecos.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{2,8})?$")


class I18nLanguage(BaseModel):
    """Un idioma del catálogo con su etiqueta nativa y bandera de habilitación."""

    model_config = ConfigDict(populate_by_name=True)

    code: str
    name: str
    native_name: str = Field(alias="nativeName")
    enabled: bool = True

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        """Normaliza a minúsculas y exige un código tipo BCP-47 (en, es, pt-br)."""
        normalized = value.strip().lower()
        if not LANGUAGE_CODE_RE.match(normalized):
            raise ValueError("Language code must be a BCP-47 style code such as en, es or pt-br.")
        return normalized

    @field_validator("name", "native_name")
    @classmethod
    def validate_label(cls, value: str) -> str:
        """Recorta los espacios de la etiqueta y rechaza valores vacíos."""
        normalized = value.strip()
        if not normalized:
            raise ValueError("Language labels cannot be empty.")
        return normalized


class I18nCatalog(BaseModel):
    """Catálogo completo: idioma por defecto, idiomas soportados y traducciones por clave."""

    model_config = ConfigDict(populate_by_name=True)

    default_language: str = Field(alias="defaultLanguage")
    languages: list[I18nLanguage]
    translations: dict[str, dict[str, str]]

    @field_validator("default_language")
    @classmethod
    def validate_default_language(cls, value: str) -> str:
        """Aplica la misma normalización BCP-47 que los códigos de idioma."""
        return I18nLanguage.validate_code(value)

    @field_validator("translations")
    @classmethod
    def validate_translation_keys(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        """Exige catálogo no vacío y claves sin espacios, cada una con al menos un valor."""
        if not value:
            raise ValueError("Translation catalog cannot be empty.")
        for key, translations in value.items():
            if not key.strip() or " " in key:
                raise ValueError(f"Invalid translation key: {key!r}")
            if not translations:
                raise ValueError(f"Translation key {key!r} has no values.")
        return value

    @model_validator(mode="after")
    def validate_catalog_completeness(self) -> I18nCatalog:
        """Verifica idioma por defecto presente, códigos únicos y cobertura total por idioma habilitado.

        Raises:
            ValueError: si el idioma por defecto no existe, hay códigos duplicados o
                alguna clave carece de valor no vacío para un idioma habilitado.
        """
        language_codes = [language.code for language in self.languages if language.enabled]
        if self.default_language not in {language.code for language in self.languages}:
            raise ValueError("Default language must exist in languages.")
        if len({language.code for language in self.languages}) != len(self.languages):
            raise ValueError("Language codes must be unique.")
        missing: list[str] = [
            f"{key}.{code}"
            for key, values in self.translations.items()
            for code in language_codes
            if not str(values.get(code, "")).strip()
        ]
        if missing:
            raise ValueError(f"Missing translation values: {', '.join(missing[:20])}")
        return self


class I18nCatalogResponse(I18nCatalog):
    """Misma forma del catálogo, usada como response_model explícito de la API."""
