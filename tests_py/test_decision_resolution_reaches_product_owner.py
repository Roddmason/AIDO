"""Resolver la decisión del hilo debe cerrar también la decisión de producto que la originó.

Bug observado en vivo (2026-07-23, votacionenlinea): el ProductOwnerAgent retuvo el backlog con
"3 blocking decision(s) remain unresolved" mientras las 9 decisiones del hilo estaban resueltas.
La causa: el PO lee ``product_decisions`` (``status == 'proposed'`` + ``metadata.blocking``),
pero la UI, el composer y la API resuelven ``thread_decisions``. El enlace existe —cada
``thread_decision`` guarda ``metadata.productDecisionId``— pero la resolución nunca se propagaba,
así que el cliente respondía y el agente le volvía a preguntar indefinidamente.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

from local_control_center.product_discovery.repository import ProductDiscoveryRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.threads.coordinator import ThreadCoordinator
from local_control_center.threads.repository import ThreadsRepository


def _fixture(connection, tmp_path: Path) -> tuple[dict, dict]:
    """Crea proyecto+hilo y una decisión de producto bloqueante enlazada a una del hilo."""
    project = ProjectsRepository(connection).create_project(
        name="decision-propagation",
        path=tmp_path / "decision-propagation",
        template_id="other",
        create_directory=True,
        source="runtime",
    )
    discovery = ProductDiscoveryRepository(connection)
    initiative = discovery.create_initiative(
        {"projectId": project["id"], "title": "Documentar convenciones", "summary": "CONTRIBUTING.md"}
    )
    product_decision = discovery.create_product_decision(
        {
            "projectId": project["id"],
            "initiativeId": initiative["id"],
            "title": "Plataforma CI/CD a documentar",
            "status": "proposed",
            "metadata": {"blocking": True},
        }
    )
    threads = ThreadsRepository(connection)
    thread = threads.create_thread(
        project_id=project["id"],
        owner_type="workspace",
        owner_id=project["id"],
        title="Decision propagation",
    )
    thread_decision = threads.create_decision(
        thread_id=thread["id"],
        title="Plataforma CI/CD a documentar",
        prompt="Que plataforma CI/CD debe documentar el CONTRIBUTING.md?",
        options=["GitHub Actions", "Sin CI/CD por ahora"],
        metadata={"productDecisionId": product_decision["id"], "source": "product_owner_agent"},
    )
    return thread_decision, product_decision


def test_resolving_thread_decision_settles_the_linked_product_decision(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        thread_decision, product_decision = _fixture(connection, tmp_path)
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        coordinator.resolve_decision(
            thread_id=thread_decision["threadId"],
            decision_id=thread_decision["id"],
            resolution="Sin CI/CD por ahora",
            decided_by="operator",
        )

        settled = ProductDiscoveryRepository(connection).get_product_decision(product_decision["id"])

    # El PO cuenta como bloqueante toda decision 'proposed' con metadata.blocking: ya no debe serlo.
    assert settled["status"] == "resolved", settled["status"]
    # La respuesta del cliente queda registrada como la decision tomada, no solo el cambio de estado.
    assert settled["decision"] == "Sin CI/CD por ahora"
    assert settled["decidedBy"] == "operator"


def test_resolution_without_linked_product_decision_is_a_no_op(tmp_path: Path) -> None:
    """Una decisión de hilo sin ``productDecisionId`` se resuelve igual, sin tocar discovery."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="plain-decision",
            path=tmp_path / "plain-decision",
            template_id="other",
            create_directory=True,
            source="runtime",
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Plain",
        )
        decision = threads.create_decision(
            thread_id=thread["id"], title="Sin enlace", prompt="Seguimos?", options=["Si", "No"]
        )
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        resolved = coordinator.resolve_decision(
            thread_id=thread["id"],
            decision_id=decision["id"],
            resolution="Si",
            decided_by="operator",
        )

    assert resolved["decision"]["status"] == "resolved"


def test_stale_product_decision_id_does_not_break_resolution(tmp_path: Path) -> None:
    """Un ``productDecisionId`` que ya no existe no puede tumbar la resolución del cliente."""
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="stale-link",
            path=tmp_path / "stale-link",
            template_id="other",
            create_directory=True,
            source="runtime",
        )
        threads = ThreadsRepository(connection)
        thread = threads.create_thread(
            project_id=project["id"],
            owner_type="workspace",
            owner_id=project["id"],
            title="Stale",
        )
        decision = threads.create_decision(
            thread_id=thread["id"],
            title="Enlace roto",
            prompt="Seguimos?",
            options=["Si", "No"],
            metadata={"productDecisionId": "product-decision-does-not-exist"},
        )
        coordinator = ThreadCoordinator(connection, root=tmp_path)

        resolved = coordinator.resolve_decision(
            thread_id=thread["id"], decision_id=decision["id"], resolution="Si", decided_by="operator"
        )

    assert resolved["decision"]["status"] == "resolved"
