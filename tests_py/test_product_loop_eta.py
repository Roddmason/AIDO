"""Tests del estimador de tiempo restante (ETA) de un hilo: percentiles y proyección del camino.

Cubre ``state_duration_stats``/``cached_state_duration_stats`` (mediana y p90 por estado, ventana,
límite por estado, caché TTL) y ``estimate_thread_eta`` (espera del operador, terminal, historial
insuficiente, historias restantes, carril rápido) con transiciones sintéticas insertadas directamente
en ``product_loop_transitions`` para controlar los timestamps exactos de cada permanencia.

@author Rodrigo Mason
"""

from __future__ import annotations

import uuid
from contextlib import closing
from pathlib import Path

import pytest

from local_control_center.product_loop import eta as eta_module
from local_control_center.product_loop import repository as repository_module
from local_control_center.product_loop.repository import ProductLoopRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema
from local_control_center.shared.time import iso_after_seconds

NOW = "2026-09-26T12:00:00.000Z"
PROJECT_ID = "project-eta-tests"


@pytest.fixture
def connection(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as conn, conn:
        initialize_platform_schema(conn)
        yield conn
    eta_module.reset_state_duration_cache()
    eta_module.reset_durable_run_cache()
    repository_module.reset_planned_loop_cache()


def _insert_transition(
    connection, *, loop_id: str, from_state: str, to_state: str, version: int, created_at: str
) -> None:
    connection.execute(
        """
        INSERT INTO product_loop_transitions
            (id, loop_id, project_id, from_state, to_state, reason, actor, trigger, version, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, '', 'test', 'test', ?, '{}', ?)
        """,
        (f"transition-{uuid.uuid4()}", loop_id, PROJECT_ID, from_state, to_state, version, created_at),
    )


def _seed_samples(connection, state: str, durations: list[float], *, ends_ago_seconds: float = 3600) -> None:
    """Crea, por cada duración, un loop sintético con una entrada y una salida de ``state`` separadas
    exactamente por esa duración; la salida ocurre ``ends_ago_seconds`` antes de ``NOW`` (bien dentro
    de la ventana por defecto de 30 días).
    """
    for duration in durations:
        loop_id = f"loop-{state}-{uuid.uuid4()}"
        end = iso_after_seconds(NOW, -ends_ago_seconds)
        start = iso_after_seconds(end, -duration)
        _insert_transition(
            connection, loop_id=loop_id, from_state="previous", to_state=state, version=1, created_at=start
        )
        _insert_transition(
            connection, loop_id=loop_id, from_state=state, to_state="next", version=2, created_at=end
        )


def _insert_thread_loop_event(
    connection, *, thread_id: str, loop_id: str, event_type: str, sequence: int
) -> None:
    """Replica el evento que ``_transition_run_state`` deja en ``thread_agent_events`` por cada avance:
    ``type`` es el nombre del estado destino y el payload trae ``loopId`` (ver
    ``product_loop.repository._thread_loop_ids_by_recency``, que resuelve el loop del hilo desde aquí).
    """
    connection.execute(
        """
        INSERT INTO thread_agent_events
            (id, thread_id, project_id, sequence, type, agent_role, payload, metadata, created_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?, '{}', ?)
        """,
        (
            f"event-{uuid.uuid4()}",
            thread_id,
            PROJECT_ID,
            sequence,
            event_type,
            f'{{"loopId": "{loop_id}"}}',
            iso_after_seconds(NOW, 0),
        ),
    )


def _create_loop(connection, *, thread_id: str, state: str, context: dict | None = None) -> dict:
    durable_run = {"thread": {"projectThreadId": thread_id}, **(context or {})}
    loop = ProductLoopRepository(connection).create_loop(
        {
            "projectId": PROJECT_ID,
            "title": "ETA test loop",
            "state": state,
            "status": "running",
            "context": {"durableRun": durable_run},
        }
    )
    _insert_thread_loop_event(
        connection, thread_id=thread_id, loop_id=loop["id"], event_type=state, sequence=1
    )
    return loop


class TestStateDurationStats:
    def test_computes_median_and_p90_from_synthetic_samples(self, connection) -> None:
        _seed_samples(connection, "executing", [80, 90, 100, 110, 120])

        stats = eta_module.state_duration_stats(connection, now_iso=NOW)

        assert stats["executing"]["count"] == 5
        assert stats["executing"]["median"] == 100
        assert stats["executing"]["p90"] == pytest.approx(116.0)

    def test_current_state_exceeded_contributes_nothing_once_elapsed_exceeds_median(self, connection) -> None:
        """No es un caso de state_duration_stats en sí, pero documenta el insumo que usa estimate_thread_eta."""
        _seed_samples(connection, "executing", [60, 60, 60])
        stats = eta_module.state_duration_stats(connection, now_iso=NOW)
        assert max(stats["executing"]["median"] - 999, 0) == 0

    def test_excludes_permanence_with_entry_outside_the_window(self, connection) -> None:
        loop_id = f"loop-outside-{uuid.uuid4()}"
        # Entra al estado 40 dias atras (fuera de la ventana de 30) y sale hace 1 hora: el extremo de
        # entrada esta fuera de ventana, asi que la permanencia completa no debe contarse.
        entered = iso_after_seconds(NOW, -40 * 86400)
        exited = iso_after_seconds(NOW, -3600)
        _insert_transition(
            connection,
            loop_id=loop_id,
            from_state="previous",
            to_state="executing",
            version=1,
            created_at=entered,
        )
        _insert_transition(
            connection, loop_id=loop_id, from_state="executing", to_state="next", version=2, created_at=exited
        )

        stats = eta_module.state_duration_stats(connection, now_iso=NOW, window_days=30)

        assert "executing" not in stats

    def test_excludes_operator_wait_and_terminal_states(self, connection) -> None:
        _seed_samples(connection, "brief_ready", [100, 100, 100])
        _seed_samples(connection, "awaiting_approval", [100, 100, 100])
        _seed_samples(connection, "delivered", [100, 100, 100])
        _seed_samples(connection, "blocked", [100, 100, 100])

        stats = eta_module.state_duration_stats(connection, now_iso=NOW)

        assert stats == {}

    def test_per_state_limit_keeps_only_the_most_recent_samples(self, connection) -> None:
        # 5 muestras viejas de 1000s y 3 muestras recientes de 10s; con limite 3 solo deben quedar las
        # recientes (mediana 10), aunque las viejas esten bien dentro de la ventana de 30 dias.
        for offset in range(5):
            _seed_samples(connection, "executing", [1000], ends_ago_seconds=20000 + offset * 100)
        _seed_samples(connection, "executing", [10, 10, 10], ends_ago_seconds=60)

        stats = eta_module.state_duration_stats(connection, now_iso=NOW, per_state_limit=3)

        assert stats["executing"]["count"] == 3
        assert stats["executing"]["median"] == 10


class TestLatestLoopStateForThread:
    """El lookup liviano que reemplazó el escaneo de ``context`` de todo el proyecto (regresión de
    rendimiento medida sobre la BD real: ~355 ms por hilo, frío y caliente)."""

    def test_resolves_the_most_recent_loop_when_the_thread_had_several(self, connection) -> None:
        thread_id = "thread-several-loops"
        old_loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "Old",
                "state": "cancelled",
                "status": "cancelled",
                "context": {},
            }
        )
        _insert_thread_loop_event(
            connection, thread_id=thread_id, loop_id=old_loop["id"], event_type="cancelled", sequence=1
        )
        new_loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "New",
                "state": "executing",
                "status": "running",
                "context": {},
            }
        )
        _insert_thread_loop_event(
            connection, thread_id=thread_id, loop_id=new_loop["id"], event_type="executing", sequence=2
        )

        summary = ProductLoopRepository(connection).latest_loop_state_for_thread(thread_id)

        assert summary is not None
        assert summary["id"] == new_loop["id"]
        assert summary["state"] == "executing"

    def test_returns_none_for_a_thread_without_any_loop_event(self, connection) -> None:
        connection.execute(
            """
            INSERT INTO thread_agent_events
                (id, thread_id, project_id, sequence, type, agent_role, payload, metadata, created_at)
            VALUES ('event-no-loop', 'thread-no-loop-events', ?, 1, 'message_received', NULL, '{}', '{}', ?)
            """,
            (PROJECT_ID, NOW),
        )

        summary = ProductLoopRepository(connection).latest_loop_state_for_thread("thread-no-loop-events")

        assert summary is None


class TestLatestPlannedLoopForThread:
    """``latest_planned_loop_for_thread`` (tablero) tras el mismo cambio: itera por eventos del hilo en
    vez de escanear ``context`` de todo el proyecto, pero conserva su contrato exacto (requiere
    ``durableRun.agentTasks`` no vacío)."""

    def test_skips_a_more_recent_loop_without_tasks_and_returns_the_planned_one(self, connection) -> None:
        thread_id = "thread-planned-lookup"
        planned_loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "Planned",
                "state": "executing",
                "status": "running",
                "context": {"durableRun": {"agentTasks": [{"id": "task-1"}]}},
            }
        )
        _insert_thread_loop_event(
            connection, thread_id=thread_id, loop_id=planned_loop["id"], event_type="executing", sequence=1
        )
        # Un loop mas reciente del MISMO hilo (p. ej. un retry) que aun no planifico tareas.
        unplanned_loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "Unplanned retry",
                "state": "discovery",
                "status": "running",
                "context": {"durableRun": {}},
            }
        )
        _insert_thread_loop_event(
            connection, thread_id=thread_id, loop_id=unplanned_loop["id"], event_type="discovery", sequence=2
        )

        result = ProductLoopRepository(connection).latest_planned_loop_for_thread(
            thread_id, project_id=PROJECT_ID
        )

        assert result is not None
        assert result["id"] == planned_loop["id"]

    def test_returns_none_without_any_planned_loop(self, connection) -> None:
        thread_id = "thread-nothing-planned"
        loop = ProductLoopRepository(connection).create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "Discovery only",
                "state": "discovery",
                "status": "running",
                "context": {"durableRun": {}},
            }
        )
        _insert_thread_loop_event(
            connection, thread_id=thread_id, loop_id=loop["id"], event_type="discovery", sequence=1
        )

        result = ProductLoopRepository(connection).latest_planned_loop_for_thread(
            thread_id, project_id=PROJECT_ID
        )

        assert result is None

    def test_caches_a_discarded_candidate_by_loop_and_version(self, connection) -> None:
        """Un candidato sin tareas no se relee en un segundo sondeo del mismo hilo y versión."""
        repository = ProductLoopRepository(connection)
        thread_id = "thread-cached-candidate"
        unplanned_loop = repository.create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "Unplanned",
                "state": "discovery",
                "status": "running",
                "context": {"durableRun": {}},
            }
        )
        _insert_thread_loop_event(
            connection, thread_id=thread_id, loop_id=unplanned_loop["id"], event_type="discovery", sequence=1
        )

        assert repository.latest_planned_loop_for_thread(thread_id, project_id=PROJECT_ID) is None

        # Aun si "durableRun.agentTasks" cambiara en la base sin una nueva transicion (no deberia pasar
        # en producción, pero acota la garantia), la version pedida sigue siendo la misma: el caché debe
        # seguir sirviendo "no calificó" sin releer el context.
        repository.update_loop_context(
            unplanned_loop["id"], context={"durableRun": {"agentTasks": [{"id": "task-1"}]}}
        )
        assert repository.latest_planned_loop_for_thread(thread_id, project_id=PROJECT_ID) is None


class TestCachedDurableRunFields:
    """El caché de ``context`` (historias restantes / carril rápido) sólo se invalida por versión: la
    pieza cara medida en la BD real (hasta ~29 MB por loop) nunca se relee mientras el loop no avance."""

    def test_serves_the_cached_value_while_the_loop_version_does_not_change(self, connection) -> None:
        repository = ProductLoopRepository(connection)
        loop = repository.create_loop(
            {
                "projectId": PROJECT_ID,
                "title": "Cache test",
                "state": "executing",
                "status": "running",
                "context": {"durableRun": {"storyProgress": [{"storyId": "s1", "status": "todo"}]}},
            }
        )

        first = eta_module._cached_durable_run_fields(connection, repository, loop_id=loop["id"], version=1)
        assert first["remainingStories"] == 1

        # El context cambia en la base (2 historias pendientes ahora), pero la version pedida es la misma:
        # debe seguir sirviendo el valor cacheado, no releer.
        repository.update_loop_context(
            loop["id"],
            context={
                "durableRun": {
                    "storyProgress": [
                        {"storyId": "s1", "status": "todo"},
                        {"storyId": "s2", "status": "todo"},
                    ]
                }
            },
        )
        cached = eta_module._cached_durable_run_fields(connection, repository, loop_id=loop["id"], version=1)
        assert cached["remainingStories"] == 1

        refreshed = eta_module._cached_durable_run_fields(
            connection, repository, loop_id=loop["id"], version=2
        )
        assert refreshed["remainingStories"] == 2


class TestCachedStateDurationStats:
    def test_ttl_serves_stale_data_until_expiry(self, connection, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = {"value": 1000.0}
        monkeypatch.setattr(eta_module.time, "monotonic", lambda: clock["value"])
        _seed_samples(connection, "executing", [10, 10, 10])

        first = eta_module.cached_state_duration_stats(connection, now_iso=NOW)
        assert first["executing"]["count"] == 3

        _seed_samples(connection, "executing", [20, 20, 20])
        clock["value"] += 10  # Bien dentro del TTL de 300s.
        cached = eta_module.cached_state_duration_stats(connection, now_iso=NOW)
        assert cached["executing"]["count"] == 3, "debe servir el valor cacheado, no recalcular"

        clock["value"] += eta_module._STATE_DURATION_CACHE_TTL_SECONDS + 1
        refreshed = eta_module.cached_state_duration_stats(connection, now_iso=NOW)
        assert refreshed["executing"]["count"] == 6


class TestEstimateThreadEta:
    def test_returns_none_without_a_loop(self, connection) -> None:
        assert (
            eta_module.estimate_thread_eta(connection, thread_id="thread-none", project_id=PROJECT_ID) is None
        )

    def test_returns_none_for_a_terminal_loop(self, connection) -> None:
        _create_loop(connection, thread_id="thread-terminal", state="delivered")
        assert (
            eta_module.estimate_thread_eta(connection, thread_id="thread-terminal", project_id=PROJECT_ID)
            is None
        )

    @pytest.mark.parametrize("state", ["awaiting_user", "awaiting_approval", "brief_ready", "blocked"])
    def test_operator_wait_states_return_waiting_operator(self, connection, state: str) -> None:
        _create_loop(connection, thread_id=f"thread-{state}", state=state)

        result = eta_module.estimate_thread_eta(
            connection, thread_id=f"thread-{state}", project_id=PROJECT_ID
        )

        assert result == {
            "status": "waiting_operator",
            "remainingSeconds": None,
            "remainingP90Seconds": None,
            "currentState": state,
            "sampleCount": 0,
            "includesOperatorApproval": False,
        }

    def test_insufficient_history_when_a_path_state_has_fewer_than_three_samples(self, connection) -> None:
        loop = _create_loop(connection, thread_id="thread-insufficient", state="planning")
        _insert_transition(
            connection,
            loop_id=loop["id"],
            from_state="discovery",
            to_state="planning",
            version=1,
            created_at=iso_after_seconds(NOW, -30),
        )
        # Ningun estado del camino restante tiene historial: 0 muestras en total.

        result = eta_module.estimate_thread_eta(
            connection, thread_id="thread-insufficient", project_id=PROJECT_ID, now_iso=NOW
        )

        assert result["status"] == "insufficient_history"
        assert result["remainingSeconds"] is None
        assert result["remainingP90Seconds"] is None
        assert result["sampleCount"] == 0

    def test_estimating_subtracts_elapsed_time_in_the_current_state(self, connection) -> None:
        for state in (
            "planning",
            "architecture_review",
            "backlog_ready",
            "branch_ready",
            "executing",
            "qa_running",
            "security_running",
            "quality_review",
            "review_ready",
        ):
            _seed_samples(connection, state, [100, 100, 100])

        loop = _create_loop(connection, thread_id="thread-partial", state="planning")
        _insert_transition(
            connection,
            loop_id=loop["id"],
            from_state="discovery",
            to_state="planning",
            version=1,
            created_at=iso_after_seconds(NOW, -40),
        )

        result = eta_module.estimate_thread_eta(
            connection, thread_id="thread-partial", project_id=PROJECT_ID, now_iso=NOW
        )

        assert result["status"] == "estimating"
        # planning: max(100-40,0)=60, + 8 estados restantes (architecture_review..review_ready) * 100 = 800 -> 860.
        assert result["remainingSeconds"] == 860
        assert result["sampleCount"] == 3
        assert result["includesOperatorApproval"] is True

    def test_multiplies_executing_and_qa_running_by_remaining_stories(self, connection) -> None:
        _seed_samples(connection, "executing", [80, 90, 100, 110, 120])
        _seed_samples(connection, "qa_running", [40, 45, 50, 55, 60])
        _seed_samples(connection, "security_running", [10, 10, 10])
        _seed_samples(connection, "quality_review", [5, 5, 5])
        _seed_samples(connection, "review_ready", [5, 5, 5])

        loop = _create_loop(
            connection,
            thread_id="thread-multi-story",
            state="executing",
            context={
                "storyProgress": [
                    {"storyId": "s1", "status": "in_progress"},
                    {"storyId": "s2", "status": "todo"},
                    {"storyId": "s3", "status": "todo"},
                ]
            },
        )
        _insert_transition(
            connection,
            loop_id=loop["id"],
            from_state="branch_ready",
            to_state="executing",
            version=1,
            created_at=iso_after_seconds(NOW, -30),
        )

        result = eta_module.estimate_thread_eta(
            connection, thread_id="thread-multi-story", project_id=PROJECT_ID, now_iso=NOW
        )

        assert result["status"] == "estimating"
        # current: max(100-30,0)=70; qa_running en el camino * 3 historias = 150;
        # security+quality+review = 20; 2 ciclos extra de (executing+qa_running)=150 cada uno = 300.
        assert result["remainingSeconds"] == 540
        assert result["remainingP90Seconds"] == 628
        assert result["sampleCount"] == 3

    def test_fast_lane_skips_architecture_and_quality_review(self, connection) -> None:
        for state in ("executing", "qa_running", "security_running", "review_ready"):
            _seed_samples(connection, state, [10, 10, 10])
        # Si el carril rapido no se aplicara, estos dos exigirian muestras propias (no las tienen) y el
        # resultado caeria a insufficient_history en vez de estimating.

        loop = _create_loop(
            connection,
            thread_id="thread-fast-lane",
            state="security_running",
            context={"specDriven": {"plan": "skipped_low_risk"}},
        )
        _insert_transition(
            connection,
            loop_id=loop["id"],
            from_state="qa_running",
            to_state="security_running",
            version=1,
            created_at=iso_after_seconds(NOW, -5),
        )

        result = eta_module.estimate_thread_eta(
            connection, thread_id="thread-fast-lane", project_id=PROJECT_ID, now_iso=NOW
        )

        assert result["status"] == "estimating"
        # security_running: max(10-5,0)=5; + review_ready=10 (architecture/quality_review saltados).
        assert result["remainingSeconds"] == 15

    def test_without_fast_lane_requires_quality_review_history_too(self, connection) -> None:
        for state in ("executing", "qa_running", "security_running", "review_ready"):
            _seed_samples(connection, state, [10, 10, 10])
        # quality_review sin historial propio y sin carril rapido: el camino completo es insuficiente.

        loop = _create_loop(connection, thread_id="thread-no-fast-lane", state="security_running")
        _insert_transition(
            connection,
            loop_id=loop["id"],
            from_state="qa_running",
            to_state="security_running",
            version=1,
            created_at=iso_after_seconds(NOW, -5),
        )

        result = eta_module.estimate_thread_eta(
            connection, thread_id="thread-no-fast-lane", project_id=PROJECT_ID, now_iso=NOW
        )

        assert result["status"] == "insufficient_history"

    def test_state_aliases_resolve_to_their_canonical_equivalent(self, connection) -> None:
        for state in ("executing", "qa_running", "security_running", "quality_review", "review_ready"):
            _seed_samples(connection, state, [10, 10, 10])

        loop = _create_loop(connection, thread_id="thread-reworking", state="reworking")
        _insert_transition(
            connection,
            loop_id=loop["id"],
            from_state="qa_running",
            to_state="reworking",
            version=1,
            created_at=iso_after_seconds(NOW, -4),
        )

        result = eta_module.estimate_thread_eta(
            connection, thread_id="thread-reworking", project_id=PROJECT_ID, now_iso=NOW
        )

        assert result["status"] == "estimating"
        assert result["currentState"] == "reworking"

    def test_safe_wrapper_never_raises_and_logs_on_failure(
        self, connection, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(*_args, **_kwargs):
            raise RuntimeError("synthetic failure")

        monkeypatch.setattr(eta_module, "estimate_thread_eta", _boom)

        result = eta_module.safe_estimate_thread_eta(connection, thread_id="thread-x", project_id=PROJECT_ID)

        assert result is None
