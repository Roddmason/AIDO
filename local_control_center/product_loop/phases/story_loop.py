"""Fase de ejecución por historia: el developer trabaja una historia a la vez, cada una con su QA.

Fase del pipeline spec-driven (docs/superpowers/specs/2026-09-22-per-story-execution-board-design.md
§3): agrupa las ``agent_tasks`` por historia en el orden de ``backlog.board.order_story_batches`` y,
por cada historia pendiente, corre developer → evidencia → gate QA con su propio presupuesto de
rework (la arista ``qa_running → executing`` con trigger ``next_story`` reinicia ``reworkRounds``).
Cada cambio de estado se persiste en ``user_stories``/``agent_tasks`` (nunca en
``agent_assignments``: sus estados de inicio disparan gates de handoff), en el cursor durable
``durableRun.storyProgress`` y como evento de hilo ``story_progress``. Una historia sin cambios queda
``done`` con ``metadata.outcome=noop``. Los helpers del coordinator se importan de forma diferida para
evitar el ciclo de imports (el coordinator importa este módulo al cargar).

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from local_control_center.backlog.board import (
    STORY_STATUS_BLOCKED,
    STORY_STATUS_DONE,
    STORY_STATUS_IN_PROGRESS,
    STORY_STATUS_QA,
    STORY_STATUS_TODO,
    order_story_batches,
    story_batch_is_done,
    story_fingerprint,
    story_progress_entry,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.time import utc_now

if TYPE_CHECKING:
    from local_control_center.product_loop.coordinator import ProductLoopCoordinator, _UserMessageRun

__all__ = ["run_story_batches"]

STORY_EVENT_TYPE = "story_progress"
NOOP_OUTCOME = "noop"


def run_story_batches(coordinator: ProductLoopCoordinator, run: _UserMessageRun) -> dict[str, Any] | None:
    """Ejecuta las historias pendientes en orden: developer → evidencia → QA con rework por historia.

    Devuelve el resultado terminal cuando una historia se bloquea (la historia queda ``blocked`` y el
    cursor lo registra), cuando una historia del output del PO sigue sin tareas incluso tras el respaldo
    determinista del Technical Lead (bloqueo ``technical_lead``: N historias exigen N runs) o ``None`` cuando todas quedaron ``done``: el loop
    termina en ``qa_running`` con la QA y la evidencia de cada historia en ``run.qa_results`` /
    ``run.evidence_ids``, listo para el diff acumulado y Security. Si ninguna historia está pendiente
    no se re-ejecuta el developer: el loop pasa directo a ``qa_running`` (``stories_already_done``).
    """
    stories_by_id, story_order = _backlog_stories(coordinator, run)
    batches = order_story_batches(run.agent_tasks, stories_by_id, story_order=story_order)
    uncovered = [batch for batch in batches if batch["story"] is not None and not batch["tasks"]]
    if uncovered:
        return _block_uncovered_stories(coordinator, run, uncovered)
    fingerprints = {batch["storyId"]: _batch_fingerprint(coordinator, batch) for batch in batches}
    pending = [batch for batch in batches if not story_batch_is_done(batch)]
    positions = {batch["storyId"]: index for index, batch in enumerate(batches, start=1)}
    _initialize_progress(coordinator, run, batches, pending=pending, fingerprints=fingerprints)
    root_task_id = run.base_task_id
    run.story_qa_results = []
    run.story_evidence_ids = []
    if not pending:
        _skip_finished_backlog(coordinator, run, total=len(batches))
    for order, batch in enumerate(pending):
        result = _run_story(
            coordinator,
            run,
            batch,
            index=positions[batch["storyId"]],
            total=len(batches),
            root_task_id=root_task_id,
            first=order == 0,
        )
        if result is not None:
            return result
    run.active_story_tasks = None
    run.task_id = root_task_id
    run.base_task_id = root_task_id
    run.qa_results = list(run.story_qa_results)
    run.evidence_ids = list(dict.fromkeys(run.story_evidence_ids))
    return None


def _skip_finished_backlog(coordinator: ProductLoopCoordinator, run: _UserMessageRun, *, total: int) -> None:
    run.loop = coordinator._transition_run_state(
        run.loop,
        to_state="qa_running",
        reason=f"All {total} stories are already done; the DeveloperAgent has nothing left to execute.",
        trigger="stories_already_done",
        actor=run.actor,
        context_patch=coordinator._durable_run_patch(run.loop, {"status": "qa_running"}),
        thread_id=run.thread_id,
    )


def _block_uncovered_stories(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, uncovered: list[dict[str, Any]]
) -> dict[str, Any]:
    titles = ", ".join(_batch_title(batch) or batch["storyId"] for batch in uncovered)
    reason = (
        "TechnicalLead and the deterministic TechnicalLeadPlanner fallback did not plan agent_tasks "
        f"for every user story; uncovered: {titles}."
    )
    return coordinator._block_run(
        run.loop,
        stage="technical_lead",
        reason=reason,
        actor=run.actor,
        details={
            "reason": reason,
            "uncoveredStoryIds": [batch["storyId"] for batch in uncovered],
            "productOwnerOutputId": (run.product_owner_output_record or {}).get("id"),
            "agentTaskIds": [task["id"] for task in run.agent_tasks],
        },
        thread_id=run.thread_id,
    )


def _run_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    *,
    index: int,
    total: int,
    root_task_id: str,
    first: bool,
) -> dict[str, Any] | None:
    if not first:
        run.loop = coordinator._transition_run_state(
            run.loop,
            to_state="executing",
            reason=f"Story {index} of {total}: executing the DeveloperAgent for the next story.",
            trigger="next_story",
            actor=run.actor,
            context_patch=coordinator._durable_run_patch(run.loop, {"status": "executing"}),
            thread_id=run.thread_id,
            fsm_patch={"usage": {"reworkRounds": 0}},
        )
    run.active_story_tasks = batch["tasks"]
    run.rework_round = 0
    run.rework_feedback = None
    run.base_task_id = f"{root_task_id}:s{index}"
    run.task_id = run.base_task_id
    _mark_story(coordinator, run, batch, STORY_STATUS_IN_PROGRESS, index=index, total=total)
    runs = 0
    while True:
        runs += 1
        run.story_noop = False
        result = coordinator._execute_developer_phase(run)
        if result is None:
            result = coordinator._capture_review_evidence(run)
        if result is not None:
            return _block_story(coordinator, run, batch, result, index=index, total=total, runs=runs)
        run.story_reviews.append(run.review)
        if run.story_noop:
            _finish_noop_story(coordinator, run, batch, index=index, total=total, runs=runs)
            return None
        _mark_story(coordinator, run, batch, STORY_STATUS_QA, index=index, total=total, runs=runs)
        result = coordinator._evaluate_qa_gate(run)
        if result is not None:
            return _block_story(coordinator, run, batch, result, index=index, total=total, runs=runs)
        if not run.should_rework:
            run.story_qa_results.extend(run.qa_results)
            run.story_evidence_ids.extend(run.evidence_ids)
            _mark_story(
                coordinator,
                run,
                batch,
                STORY_STATUS_DONE,
                index=index,
                total=total,
                runs=runs,
                qa_verdict=run.qa_verdict,
                commit=_review_commit(run.review),
            )
            return None
        _mark_story(coordinator, run, batch, STORY_STATUS_IN_PROGRESS, index=index, total=total, runs=runs)


def _finish_noop_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    *,
    index: int,
    total: int,
    runs: int,
) -> None:
    run.loop = coordinator._transition_run_state(
        run.loop,
        to_state="qa_running",
        reason=f"Story {index} of {total} produced no changes; there is nothing for QA to verify.",
        trigger="story_noop",
        actor=run.actor,
        context_patch=coordinator._durable_run_patch(
            run.loop, {"status": "qa_running", "runtimeResult": run.runtime_result}
        ),
        thread_id=run.thread_id,
    )
    run.story_evidence_ids.extend(run.evidence_ids)
    _mark_story(
        coordinator, run, batch, STORY_STATUS_DONE, index=index, total=total, runs=runs, outcome=NOOP_OUTCOME
    )


def _block_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    result: dict[str, Any],
    *,
    index: int,
    total: int,
    runs: int,
) -> dict[str, Any]:
    reason = str(result.get("reason") or "")
    _mark_story(
        coordinator, run, batch, STORY_STATUS_BLOCKED, index=index, total=total, runs=runs, reason=reason
    )
    return {**result, "loop": coordinator.repository.get_loop(run.loop["id"])}


def _mark_story(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batch: dict[str, Any],
    status: str,
    *,
    index: int,
    total: int,
    runs: int = 0,
    outcome: str | None = None,
    reason: str | None = None,
    qa_verdict: str | None = None,
    commit: str | None = None,
) -> None:
    clean_reason = str(redact_secrets(reason)) if reason else None
    _write_statuses(coordinator, batch, status, outcome=outcome, reason=clean_reason)
    _update_progress(
        coordinator,
        run,
        batch["storyId"],
        {
            "status": status,
            "runs": runs,
            "runtime": _runtime_id(run),
            "outcome": outcome,
            "reason": clean_reason,
            "qaVerdict": qa_verdict,
            "commit": commit,
        },
    )
    payload: dict[str, Any] = {
        "loopId": run.loop["id"],
        "storyId": batch["storyId"],
        "title": _batch_title(batch),
        "status": status,
        "index": index,
        "total": total,
    }
    if outcome:
        payload["outcome"] = outcome
    coordinator._record_thread_event(
        thread_id=run.thread_id, event_type=STORY_EVENT_TYPE, agent_role="developer", payload=payload
    )


def _write_statuses(
    coordinator: ProductLoopCoordinator,
    batch: dict[str, Any],
    status: str,
    *,
    outcome: str | None = None,
    reason: str | None = None,
) -> None:
    story = batch.get("story")
    if story is not None:
        metadata = {
            key: value
            for key, value in (story.get("metadata") or {}).items()
            if key not in {"outcome", "blockedReason"}
        }
        if outcome:
            metadata["outcome"] = outcome
        if reason and status == STORY_STATUS_BLOCKED:
            metadata["blockedReason"] = reason
        updated = coordinator.backlog.update_user_story(story["id"], {"status": status, "metadata": metadata})
        story["status"] = updated["status"]
        story["metadata"] = updated["metadata"]
    for task in batch.get("tasks") or []:
        task["status"] = coordinator.backlog.update_agent_task(task["id"], {"status": status})["status"]


def _initialize_progress(
    coordinator: ProductLoopCoordinator,
    run: _UserMessageRun,
    batches: list[dict[str, Any]],
    *,
    pending: list[dict[str, Any]],
    fingerprints: dict[str, str],
) -> None:
    pending_ids = {batch["storyId"] for batch in pending}
    entries: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, start=1):
        if batch["storyId"] in pending_ids:
            _write_statuses(coordinator, batch, STORY_STATUS_TODO)
            status, outcome = STORY_STATUS_TODO, None
        else:
            status = STORY_STATUS_DONE
            outcome = ((batch.get("story") or {}).get("metadata") or {}).get("outcome")
        entries.append(
            story_progress_entry(
                story_id=batch["storyId"],
                index=index,
                title=_batch_title(batch),
                status=status,
                fingerprint=fingerprints.get(batch["storyId"], ""),
                outcome=outcome,
            )
        )
    _save_progress(coordinator, run, entries)


def _update_progress(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, story_id: str, changes: dict[str, Any]
) -> None:
    loop = coordinator.repository.get_loop(run.loop["id"])
    entries = [
        dict(entry)
        for entry in coordinator._durable_run_context(loop).get("storyProgress") or []
        if isinstance(entry, dict)
    ]
    for entry in entries:
        if entry.get("storyId") == story_id:
            entry.update({key: value for key, value in changes.items() if value is not None})
    _save_progress(coordinator, run, entries)


def _save_progress(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun, entries: list[dict[str, Any]]
) -> None:
    loop = coordinator.repository.get_loop(run.loop["id"])
    durable = coordinator._durable_run_context(loop)
    run.loop = coordinator.repository.update_loop_context(
        loop["id"],
        context={
            **loop["context"],
            "durableRun": {**durable, "storyProgress": entries, "updatedAt": utc_now()},
        },
    )


def _backlog_stories(
    coordinator: ProductLoopCoordinator, run: _UserMessageRun
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    output_id = str((run.product_owner_output_record or {}).get("id") or "")
    emitted = coordinator.backlog.list_user_stories_for_output(run.project_id, output_id) if output_id else []
    records: dict[str, dict[str, Any]] = {story["id"]: story for story in emitted}
    for task in run.agent_tasks or []:
        story_id = str(task.get("storyId") or "").strip()
        if not story_id or story_id in records:
            continue
        try:
            records[story_id] = coordinator.backlog.get_user_story(story_id)
        except KeyError:
            continue
    return records, [story["id"] for story in emitted]


def _batch_fingerprint(coordinator: ProductLoopCoordinator, batch: dict[str, Any]) -> str:
    story = batch.get("story")
    if story is None:
        return ""
    criteria = [
        str(criterion.get("criterion") or "")
        for criterion in coordinator.backlog.list_acceptance_criteria(story_id=story["id"])
    ]
    return story_fingerprint(story, criteria)


def _batch_title(batch: dict[str, Any]) -> str:
    return str((batch.get("story") or {}).get("title") or "")


def _runtime_id(run: _UserMessageRun) -> str | None:
    used = (run.runtime_result or {}).get("runtime")
    used_id = str(used.get("id") or "") if isinstance(used, dict) else ""
    return (
        used_id
        or str((run.readiness or {}).get("selectedRuntimeId") or run.effective_preferred_runtime or "")
        or None
    )


def _review_commit(review: dict[str, Any]) -> str | None:
    commit = (review or {}).get("commit")
    if not isinstance(commit, dict):
        return None
    return str(commit.get("commit") or "") or None
