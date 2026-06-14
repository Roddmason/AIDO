from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.time import utc_now

from .models import I18nCatalog


class I18nRepository:
    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection

    def has_catalog(self) -> bool:
        row = self.connection.execute("SELECT 1 FROM i18n_languages LIMIT 1").fetchone()
        return row is not None

    def get_catalog(self) -> dict[str, Any]:
        default_row = self.connection.execute(
            "SELECT value FROM i18n_settings WHERE key = 'default_language'"
        ).fetchone()
        default_language = str(default_row["value"]) if default_row else "en"
        languages = [
            {
                "code": row["code"],
                "name": row["name"],
                "nativeName": row["native_name"],
                "enabled": bool(row["enabled"]),
            }
            for row in self.connection.execute(
                """
                SELECT code, name, native_name, enabled
                FROM i18n_languages
                ORDER BY code
                """
            ).fetchall()
        ]
        translations: dict[str, dict[str, str]] = {}
        rows = self.connection.execute(
            """
            SELECT key, language_code, value
            FROM i18n_translations
            ORDER BY key, language_code
            """
        ).fetchall()
        for row in rows:
            translations.setdefault(row["key"], {})[row["language_code"]] = row["value"]
        return {
            "defaultLanguage": default_language,
            "languages": languages,
            "translations": translations,
        }

    def replace_catalog(self, catalog: I18nCatalog) -> dict[str, Any]:
        timestamp = utc_now()
        with immediate_transaction(self.connection):
            self.connection.execute("DELETE FROM i18n_settings")
            self.connection.execute("DELETE FROM i18n_translations")
            self.connection.execute("DELETE FROM i18n_languages")
            self.connection.execute(
                """
                INSERT INTO i18n_settings (key, value, updated_at)
                VALUES ('default_language', ?, ?)
                """,
                (catalog.default_language, timestamp),
            )
            for language in catalog.languages:
                self.connection.execute(
                    """
                    INSERT INTO i18n_languages
                        (code, name, native_name, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        language.code,
                        language.name,
                        language.native_name,
                        1 if language.enabled else 0,
                        timestamp,
                        timestamp,
                    ),
                )
            for key, values in sorted(catalog.translations.items()):
                for language_code, value in sorted(values.items()):
                    self.connection.execute(
                        """
                        INSERT INTO i18n_translations
                            (key, language_code, value, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (key, language_code, str(value), timestamp),
                    )
        return self.get_catalog()

    def ensure_seeded(self, catalog: I18nCatalog) -> dict[str, Any]:
        if not self.has_catalog():
            return self.replace_catalog(catalog)
        timestamp = utc_now()
        with immediate_transaction(self.connection):
            default_exists = self.connection.execute(
                "SELECT 1 FROM i18n_settings WHERE key = 'default_language'"
            ).fetchone()
            if default_exists is None:
                self.connection.execute(
                    """
                    INSERT INTO i18n_settings (key, value, updated_at)
                    VALUES ('default_language', ?, ?)
                    """,
                    (catalog.default_language, timestamp),
                )
            for language in catalog.languages:
                existing_language = self.connection.execute(
                    "SELECT 1 FROM i18n_languages WHERE code = ?",
                    (language.code,),
                ).fetchone()
                if existing_language is None:
                    self.connection.execute(
                        """
                        INSERT INTO i18n_languages
                            (code, name, native_name, enabled, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            language.code,
                            language.name,
                            language.native_name,
                            1 if language.enabled else 0,
                            timestamp,
                            timestamp,
                        ),
                    )
            for key, values in sorted(catalog.translations.items()):
                for language_code, value in sorted(values.items()):
                    existing_translation = self.connection.execute(
                        """
                        SELECT 1
                        FROM i18n_translations
                        WHERE key = ? AND language_code = ?
                        """,
                        (key, language_code),
                    ).fetchone()
                    if existing_translation is None:
                        self.connection.execute(
                            """
                            INSERT INTO i18n_translations
                                (key, language_code, value, updated_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (key, language_code, str(value), timestamp),
                        )
        return self.get_catalog()
