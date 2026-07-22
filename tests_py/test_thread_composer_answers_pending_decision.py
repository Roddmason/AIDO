"""Un mensaje del composer que matchea una opción pendiente resuelve la decisión, no re-pregunta.

Bug observado en vivo (votacionenlinea): el clasificador de intake pregunta el outcome con
confianza baja; el usuario responde "implementation" por el composer y el mensaje se re-clasifica
desde cero → misma confianza → la MISMA pregunta otra vez, con decisiones duplicadas acumulándose
(loop infinito de decisión). El composer debe consumir la respuesta como resolución del batch
pendiente y reanudar el run, igual que el botón de la UI.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.coordinator import ThreadCoordinator
from local_control_center.threads.repository import ThreadsRepository

RUNTIME_UNAVAILABLE = {
    "runtimeStatus": {
        "providers": [{"id": "codex_cli", "executable": False, "available": False, "canEditWorkspace": False}]
    }
}


def _thread(connection, tmp_path: Path) -> dict:
    project = ProjectsRepository(connection).create_project(
        name="composer-decision",
        path=tmp_path / "composer-decision",
        template_id="other",
        create_directory=True,
        source="runtime",
    )
    return ThreadsRepository(connection).create_thread(
        project_id=project["id"],
        owner_type="workspace",
        owner_id=project["id"],
        title="Composer answers pending decision",
    )


def test_composer_reply_matching_an_option_resolves_instead_of_reasking(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        blocked = coordinator.post_message(
            thread_id=thread["id"],
            content="Implement the onboarding dashboard.",
            project_assessment=RUNTIME_UNAVAILABLE,
        )
        assert blocked["thread"]["status"] == "waiting_decision"
        first_decision = blocked["decision"]
        assert first_decision is not None and first_decision["status"] == "pending"
        option = first_decision["options"][0]

        answered = coordinator.post_message(
            thread_id=thread["id"],
            content=f"{option}. Sigue adelante con eso por favor.",
            project_assessment=RUNTIME_UNAVAILABLE,
        )

        repo = ThreadsRepository(connection)
        decisions = repo.list_decisions(thread["id"])
        pending = [d for d in decisions if d["status"] == "pending"]
        resolved = [d for d in decisions if d["status"] == "resolved"]
        # La respuesta consumió la decisión: nada pendiente duplicado y la original quedó resuelta.
        assert pending == [], [d["prompt"] for d in pending]
        assert len(resolved) == 1
        assert answered["thread"]["status"] != "waiting_decision"
        # No se registró una segunda pregunta idéntica.
        prompts = [d["prompt"] for d in decisions]
        assert len(prompts) == len(set(prompts))


def test_composer_reply_that_does_not_match_any_option_still_reclassifies(tmp_path: Path) -> None:
    """Un mensaje que NO responde la pregunta sigue el flujo normal (nueva clasificación)."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread = _thread(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        blocked = coordinator.post_message(
            thread_id=thread["id"],
            content="Implement the onboarding dashboard.",
            project_assessment=RUNTIME_UNAVAILABLE,
        )
        assert blocked["thread"]["status"] == "waiting_decision"

        coordinator.post_message(
            thread_id=thread["id"],
            content="Cambiando de tema: agrega ademas un modo oscuro al dashboard.",
            project_assessment=RUNTIME_UNAVAILABLE,
        )

        decisions = ThreadsRepository(connection).list_decisions(thread["id"])
        assert any(d["status"] == "pending" for d in decisions)
