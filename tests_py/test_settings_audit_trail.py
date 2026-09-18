"""Tests de la traza de auditoría de los cambios de preferencias.

Descubierto operando la instalación real: `runtime.cli.enabled` aparecía en `false` y la única
pista de quién y cuándo era la columna `updated_at`. Las preferencias gobiernan cosas sensibles —
umbrales del gobernador de recursos, si los runtimes CLI están habilitados, si un proyecto corre en
un contenedor con el workspace escribible — y cambiarlas no dejaba ninguna entrada de auditoría.

Se registra la clave, el alcance y el valor **nuevo**. El valor anterior no se registra: una
preferencia puede contener una ruta u otro dato del operador, y la auditoría no es el lugar para
duplicarlo.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from local_control_center.app import create_app
from local_control_center.control_plane.runtime import ControlCenterRuntime


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import sys

    sys.modules["faiss"] = None
    monkeypatch.setenv("LOCAL_CONTROL_CENTER_DB", str(tmp_path / "platform.sqlite"))
    runtime = ControlCenterRuntime(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    runtime.init()
    app = create_app(runtime=runtime, static_dir=None)
    try:
        with TestClient(app) as test_client:
            token = test_client.get("/api/v1/security/handshake").json()["token"]
            yield (
                test_client,
                runtime,
                {
                    "X-Local-Control-Token": token,
                    "Origin": "http://127.0.0.1",
                },
            )
    finally:
        runtime.close()


def _audit(runtime, action: str) -> list[dict]:
    rows = runtime.connection.execute(
        "SELECT action, target, payload, actor FROM audit_events WHERE action = ?", (action,)
    ).fetchall()
    return [dict(row) for row in rows]


def test_setting_a_preference_leaves_an_audit_entry(client) -> None:
    """Cambiar un umbral del gobernador tiene que quedar registrado con quién y qué."""
    test_client, runtime, headers = client

    response = test_client.put(
        "/api/v1/settings/resources.maxCpuPercent",
        headers=headers,
        json={"value": 85, "scope": "general"},
    )

    assert response.status_code == 204, response.text
    entries = _audit(runtime, "settings.value_set")
    assert len(entries) == 1, entries
    assert entries[0]["target"] == "resources.maxCpuPercent"
    assert entries[0]["actor"] == "operator"
    assert "85" in str(entries[0]["payload"])
    assert "general" in str(entries[0]["payload"])


def test_clearing_a_preference_leaves_its_own_entry(client) -> None:
    """Volver al valor heredado es un cambio tanto como fijarlo, y se audita igual."""
    test_client, runtime, headers = client
    test_client.put(
        "/api/v1/settings/resources.maxCpuPercent",
        headers=headers,
        json={"value": 85, "scope": "general"},
    )

    response = test_client.delete("/api/v1/settings/resources.maxCpuPercent?scope=general", headers=headers)

    assert response.status_code == 204, response.text
    assert len(_audit(runtime, "settings.value_cleared")) == 1


def test_a_rejected_write_is_not_audited_as_a_change(client) -> None:
    """Un valor invalido no cambia nada: auditarlo ensuciaria la traza con ruido."""
    test_client, runtime, headers = client

    response = test_client.put(
        "/api/v1/settings/resources.maxCpuPercent",
        headers=headers,
        json={"value": 9999, "scope": "general"},
    )

    assert response.status_code == 422, response.text
    assert _audit(runtime, "settings.value_set") == []
