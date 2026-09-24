"""Configuración del operador por (cuenta local, modelo): por defecto, orden y capacidades opt-in.

La tabla ``local_model_settings`` vive aparte de ``model_catalog`` porque el resync del catálogo
reescribe esas filas. ``upsert`` conserva lo no enviado, registra la procedencia por campo y, al marcar
un por defecto, desmarca el anterior en la misma transacción (índice único parcial por cuenta). Las
capacidades de código son opt-in por modelo: nunca se siembran por runtime.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass

from local_control_center.shared.db import immediate_transaction
from local_control_center.shared.serialization import json_dumps, json_loads
from local_control_center.shared.time import utc_now

BASE_LOCAL_MODEL_CAPABILITIES = frozenset({"chat"})
_FIELD_DEFAULTS: Mapping[str, bool | int] = {
    "is_default": False,
    "code_edit": False,
    "code_review": False,
    "operator_order": 0,
    "json_schema": False,
}
_COLUMNS = (
    "provider_id, model, is_default, code_edit, code_review, operator_order, json_schema, provenance_json"
)


@dataclass(frozen=True)
class LocalModelSetting:
    """Configuración vigente de un modelo local; ``provenance`` dice quién fijó cada campo."""

    provider_id: str
    model: str
    is_default: bool
    code_edit: bool
    code_review: bool
    operator_order: int
    provenance: Mapping[str, str]
    json_schema: bool = False


def setting_capabilities(setting: LocalModelSetting | None) -> frozenset[str]:
    """``chat`` más las capacidades de código que el operador habilitó para el modelo."""
    capabilities = set(BASE_LOCAL_MODEL_CAPABILITIES)
    if setting is not None and setting.code_edit:
        capabilities.add("code_edit")
    if setting is not None and setting.code_review:
        capabilities.add("code_review")
    return frozenset(capabilities)


def _row_to_setting(row: sqlite3.Row) -> LocalModelSetting:
    return LocalModelSetting(
        provider_id=str(row["provider_id"]),
        model=str(row["model"]),
        is_default=bool(row["is_default"]),
        code_edit=bool(row["code_edit"]),
        code_review=bool(row["code_review"]),
        operator_order=int(row["operator_order"]),
        provenance=json_loads(row["provenance_json"], {}),
        json_schema=bool(row["json_schema"]),
    )


class LocalModelSettingsRepository:
    """Lectura y escritura de ``local_model_settings``; cada escritura es una transacción atómica."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def list_for_account(self, provider_id: str) -> list[LocalModelSetting]:
        """Configuración de los modelos de la cuenta, ordenada por ``operator_order`` e id de modelo."""
        rows = self.connection.execute(
            f"SELECT {_COLUMNS} FROM local_model_settings WHERE provider_id = ? "
            "ORDER BY operator_order ASC, model ASC",
            (provider_id,),
        ).fetchall()
        return [_row_to_setting(row) for row in rows]

    def get(self, provider_id: str, model: str) -> LocalModelSetting | None:
        """Configuración de un modelo, o ``None`` si nadie la fijó todavía."""
        row = self.connection.execute(
            f"SELECT {_COLUMNS} FROM local_model_settings WHERE provider_id = ? AND model = ?",
            (provider_id, model),
        ).fetchone()
        return _row_to_setting(row) if row is not None else None

    def upsert(
        self,
        provider_id: str,
        model: str,
        *,
        actor: str,
        is_default: bool | None = None,
        code_edit: bool | None = None,
        code_review: bool | None = None,
        operator_order: int | None = None,
        json_schema: bool | None = None,
    ) -> LocalModelSetting:
        """Crea o actualiza la configuración del modelo; un argumento ``None`` conserva lo guardado.

        Marcar ``is_default`` desmarca el por defecto anterior de la cuenta en la misma transacción.

        Raises:
            ValueError: ``provider_id``, ``model`` o ``actor`` vacíos, u ``operator_order`` negativo.
        """
        provider_id, model, actor = provider_id.strip(), model.strip(), actor.strip()
        if not provider_id or not model or not actor:
            raise ValueError("Local model settings require providerId, model and actor.")
        if operator_order is not None and operator_order < 0:
            raise ValueError("operatorOrder must be zero or positive.")
        requested = {
            "is_default": is_default,
            "code_edit": code_edit,
            "code_review": code_review,
            "operator_order": operator_order,
            "json_schema": json_schema,
        }
        transaction = (
            nullcontext() if self.connection.in_transaction else immediate_transaction(self.connection)
        )
        with transaction:
            current = self.get(provider_id, model)
            values = {
                name: getattr(current, name) if current is not None else default
                for name, default in _FIELD_DEFAULTS.items()
            }
            provenance = dict(current.provenance) if current is not None else {}
            for name, value in requested.items():
                if value is not None:
                    values[name] = value
                    provenance[name] = actor
            now = utc_now()
            if is_default:
                self.connection.execute(
                    """UPDATE local_model_settings SET is_default = 0, updated_at = ?
                       WHERE provider_id = ? AND model <> ? AND is_default = 1""",
                    (now, provider_id, model),
                )
            self.connection.execute(
                """INSERT INTO local_model_settings
                       (provider_id, model, is_default, code_edit, code_review, operator_order,
                        json_schema, provenance_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(provider_id, model) DO UPDATE SET
                       is_default = excluded.is_default,
                       code_edit = excluded.code_edit,
                       code_review = excluded.code_review,
                       operator_order = excluded.operator_order,
                       json_schema = excluded.json_schema,
                       provenance_json = excluded.provenance_json,
                       updated_at = excluded.updated_at""",
                (
                    provider_id,
                    model,
                    int(bool(values["is_default"])),
                    int(bool(values["code_edit"])),
                    int(bool(values["code_review"])),
                    int(values["operator_order"]),
                    int(bool(values["json_schema"])),
                    json_dumps(provenance),
                    now,
                ),
            )
            row = self.connection.execute(
                f"SELECT {_COLUMNS} FROM local_model_settings WHERE provider_id = ? AND model = ?",
                (provider_id, model),
            ).fetchone()
        return _row_to_setting(row)

    def default_model(self, provider_id: str) -> str | None:
        """Modelo por defecto de la cuenta, o ``None`` si el operador no marcó ninguno."""
        row = self.connection.execute(
            "SELECT model FROM local_model_settings WHERE provider_id = ? AND is_default = 1",
            (provider_id,),
        ).fetchone()
        return str(row["model"]) if row is not None else None

    def capabilities_for(self, provider_id: str, model: str) -> frozenset[str]:
        """Capacidades del modelo: ``chat`` más sus opt-in de código."""
        return setting_capabilities(self.get(provider_id, model))

    def enabled_capabilities(self, provider_id: str) -> frozenset[str]:
        """Capacidades del runtime local: ``chat`` más los opt-in de sus modelos habilitados en el catálogo."""
        rows = self.connection.execute(
            """SELECT s.code_edit, s.code_review FROM local_model_settings AS s
               JOIN model_catalog AS m ON m.provider_id = s.provider_id AND m.model = s.model
               WHERE s.provider_id = ? AND m.enabled = 1""",
            (provider_id,),
        ).fetchall()
        capabilities = set(BASE_LOCAL_MODEL_CAPABILITIES)
        for row in rows:
            if row["code_edit"]:
                capabilities.add("code_edit")
            if row["code_review"]:
                capabilities.add("code_review")
        return frozenset(capabilities)

    def json_schema_enabled(self, provider_id: str, model: str) -> bool:
        """Indica si una validación real del modelo cumplió ``response_format`` json_schema."""
        setting = self.get(provider_id, model)
        return bool(setting is not None and setting.json_schema)

    def delete_for_account(self, provider_id: str) -> int:
        """Borra la configuración de todos los modelos de la cuenta y devuelve cuántas filas quitó.

        Reutiliza la transacción del llamador si hay una abierta (el tombstone del endpoint borra dentro
        de la suya); si no, abre una propia.
        """
        transaction = (
            nullcontext() if self.connection.in_transaction else immediate_transaction(self.connection)
        )
        with transaction:
            return self.connection.execute(
                "DELETE FROM local_model_settings WHERE provider_id = ?", (provider_id,)
            ).rowcount
