"""El status por proveedor expone un blockerType normalizado y accionable para el cliente.

La UI necesita un chip consistente ("qué falla") entre el bloqueo de un loop y la alerta global de
salud de runtimes. En vez de duplicar la derivación de causa en el frontend (que no tiene runner de
unit-tests), el backend clasifica cada status de proveedor a un ``blockerType`` reutilizando el
vocabulario de ``remediations`` (runtime_auth_missing / runtime_not_executable), o ``None`` cuando el
proveedor está sano o simplemente no configurado (no es un problema, solo no se usa).

@author Rodrigo Mason
"""

from __future__ import annotations

import pytest

from local_control_center.agents.runtime_status import _runtime_blocker_type


def test_executable_provider_has_no_blocker() -> None:
    assert (
        _runtime_blocker_type(
            kind="cli",
            detected=True,
            configured=True,
            executable=True,
            authenticated=True,
            requires_approval=False,
        )
        is None
    )


def test_unconfigured_undetected_provider_is_not_a_blocker() -> None:
    # Nunca se configuró: no se usa, no es un problema que reportar al cliente.
    assert (
        _runtime_blocker_type(
            kind="api",
            detected=False,
            configured=False,
            executable=False,
            authenticated=False,
            requires_approval=False,
        )
        is None
    )


def test_manual_approval_provider_is_never_a_blocker() -> None:
    assert (
        _runtime_blocker_type(
            kind="manual",
            detected=True,
            configured=True,
            executable=False,
            authenticated=False,
            requires_approval=True,
        )
        is None
    )


@pytest.mark.parametrize("kind", ["cli", "api", "gateway"])
def test_detected_but_unauthenticated_credential_provider_is_auth_missing(kind: str) -> None:
    # Un CLI instalado con token vencido / una API sin key válida: falta autenticación.
    assert (
        _runtime_blocker_type(
            kind=kind,
            detected=True,
            configured=True,
            executable=False,
            authenticated=False,
            requires_approval=False,
        )
        == "runtime_auth_missing"
    )


def test_configured_but_not_executable_is_not_executable() -> None:
    # Autenticado pero inejecutable (p.ej. cuota agotada, versión no soportada): no ejecutable.
    assert (
        _runtime_blocker_type(
            kind="cli",
            detected=True,
            configured=True,
            executable=False,
            authenticated=True,
            requires_approval=False,
        )
        == "runtime_not_executable"
    )


def test_local_provider_down_is_not_executable_not_auth() -> None:
    # Ollama caído no es un problema de credenciales: no ejecutable, no auth.
    assert (
        _runtime_blocker_type(
            kind="local",
            detected=True,
            configured=True,
            executable=False,
            authenticated=False,
            requires_approval=False,
        )
        == "runtime_not_executable"
    )
