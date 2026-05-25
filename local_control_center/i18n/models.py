from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


LANGUAGE_CODE_RE = re.compile(r"^[a-z]{2}(?:-[a-z0-9]{2,8})?$")


class I18nLanguage(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    code: str
    name: str
    native_name: str = Field(alias="nativeName")
    enabled: bool = True

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not LANGUAGE_CODE_RE.match(normalized):
            raise ValueError("Language code must be a BCP-47 style code such as en, es or pt-br.")
        return normalized

    @field_validator("name", "native_name")
    @classmethod
    def validate_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Language labels cannot be empty.")
        return normalized


class I18nCatalog(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    default_language: str = Field(alias="defaultLanguage")
    languages: list[I18nLanguage]
    translations: dict[str, dict[str, str]]

    @field_validator("default_language")
    @classmethod
    def validate_default_language(cls, value: str) -> str:
        return I18nLanguage.validate_code(value)

    @field_validator("translations")
    @classmethod
    def validate_translation_keys(cls, value: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
        if not value:
            raise ValueError("Translation catalog cannot be empty.")
        for key, translations in value.items():
            if not key.strip() or " " in key:
                raise ValueError(f"Invalid translation key: {key!r}")
            if not translations:
                raise ValueError(f"Translation key {key!r} has no values.")
        return value

    @model_validator(mode="after")
    def validate_catalog_completeness(self) -> "I18nCatalog":
        language_codes = [language.code for language in self.languages if language.enabled]
        if self.default_language not in {language.code for language in self.languages}:
            raise ValueError("Default language must exist in languages.")
        if len({language.code for language in self.languages}) != len(self.languages):
            raise ValueError("Language codes must be unique.")
        missing: list[str] = []
        for key, values in self.translations.items():
            for code in language_codes:
                if not str(values.get(code, "")).strip():
                    missing.append(f"{key}.{code}")
        if missing:
            raise ValueError(f"Missing translation values: {', '.join(missing[:20])}")
        return self


class I18nCatalogResponse(I18nCatalog):
    pass
