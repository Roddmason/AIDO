"""Configuración shadow resuelta por settings con precedencia proyecto/general/default.

@author Rodrigo Mason
"""

from __future__ import annotations

import os
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from local_control_center.settings.resolver import resolve_setting_value

from .models import Probability, Risk, fingerprint


def real_jev_calls_enabled() -> bool:
    """Jev-only authorization does not enable generative providers or CLI runtimes."""
    if any(
        os.environ.get(key)
        for key in ("AIDO_QUALITY_INVOCATION_ID", "AIDO_QUALITY_DB_PATH", "PYTEST_CURRENT_TEST")
    ):
        return False
    return any(
        os.environ.get(key, "false").lower() == "true"
        for key in ("AIDO_ENABLE_JEV_CALLS", "AIDO_ENABLE_REAL_PROVIDER_CALLS")
    )


class DecisionConfig(BaseModel):
    """Explicit ranking mode; execution authority always remains in AIDO."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False, validate_default=True)
    enabled: bool = True
    provider: Literal["jev", "deterministic"] = "jev"
    mode: Literal["disabled", "shadow", "runtime_selection"] = "shadow"
    shadow_enabled: bool = True
    jev_enabled: bool = True
    endpoint: str = "https://api.typesafe.ai/v1/systemone"
    api_key_reference: str = "env:TYPESAFE_API_KEY"
    model: str = "jev-1.13.0"
    version: str = "1.13.0"
    timeout_seconds: float = Field(default=5.0, ge=0.01, le=5, allow_inf_nan=False)
    confidence_threshold: Probability = 0.85
    margin_threshold: Probability = 0.20
    max_risk: Risk = "high"
    probability_tolerance: float = Field(default=0.000001, ge=0, le=0.001, allow_inf_nan=False)
    circuit_failure_threshold: int = Field(default=3, ge=1, le=20)
    circuit_cooldown_seconds: float = Field(default=30, ge=1, le=300, allow_inf_nan=False)

    @model_validator(mode="after")
    def pinned_model(self) -> DecisionConfig:
        """Evita alias móviles, cambios de modelo y secretos embebidos en endpoint/config."""
        import re

        if not re.fullmatch(r"\d+\.\d+\.\d+", self.version) or self.model != f"jev-{self.version}":
            raise ValueError("invalid_model_version")
        if self.endpoint != "https://api.typesafe.ai/v1/systemone":
            raise ValueError("invalid_endpoint")
        if not re.fullmatch(r"env:[A-Z_][A-Z0-9_]*", self.api_key_reference):
            raise ValueError("invalid_credential_reference")
        return self

    @property
    def active(self) -> bool:
        """Indica observación habilitada sin activar llamadas cuando el rollback está aplicado."""
        return self.enabled and (
            self.mode == "runtime_selection" or (self.mode == "shadow" and self.shadow_enabled)
        )

    @property
    def selects_runtime(self) -> bool:
        """La selección activa es explícita y nunca convierte shadow en autoridad."""
        return self.active and self.mode == "runtime_selection"

    @property
    def configuration_fingerprint(self) -> str:
        """Captura configuración efectiva sin almacenar referencias ni secretos en receipts."""
        return fingerprint(self.model_dump())


SETTING_NAMES = {name: name for name in DecisionConfig.model_fields}
SETTING_NAMES.update(shadow_enabled="shadow.enabled", jev_enabled="jev.enabled")


def resolve_config(connection: sqlite3.Connection, project_id: str | None) -> DecisionConfig:
    """Reutiliza el resolver de AIDO en vez de implementar precedencia paralela."""
    return DecisionConfig(
        **{
            field: resolve_setting_value(
                connection=connection, key=f"decision_engine.{key}", project_id=project_id
            )
            for field, key in SETTING_NAMES.items()
        }
    )
