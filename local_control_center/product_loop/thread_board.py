"""Lectura del tablero por historia de un hilo: resuelve su loop planificado y proyecta el backlog.

Resuelve el loop más reciente del hilo con tareas planificadas, relee sus tareas e historias frescas
desde el backlog (los ids salen de ``durableRun.agentTasks`` porque las tareas reutilizadas entre loops
conservan el ``metadata.loopId`` de su loop de origen), todas las historias del output del PO del loop
(``durableRun.productOwner.productOwnerOutputId``, en orden de emisión e incluidas las que no tienen
tareas), los criterios de aceptación y el cursor ``durableRun.storyProgress``, y delega el mapeo a
columnas en ``backlog.board.build_board``. El payload
queda acotado al hilo (nunca el agregado del proyecto). Solo lectura: no abre transacciones.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from typing import Any

from local_control_center.backlog.board import build_board, empty_board
from local_control_center.backlog.repository import BacklogRepository
from local_control_center.threads.repository import ThreadsRepository

from .repository import ProductLoopRepository


class ThreadBoardService:
    """Arma el tablero de un hilo leyendo solo su loop planificado, sus historias y sus tareas."""

    def __init__(self, connection: sqlite3.Connection):
        self.threads = ThreadsRepository(connection)
        self.loops = ProductLoopRepository(connection)
        self.backlog = BacklogRepository(connection)

    def board(self, thread_id: str) -> dict[str, Any]:
        """Devuelve el tablero del hilo; sin loop planificado devuelve el tablero vacío en ``planning``.

        Raises:
            KeyError: si el hilo no existe.
        """
        thread = self.threads.get_thread(thread_id)
        loop = self.loops.latest_planned_loop_for_thread(thread_id, project_id=thread["projectId"])
        if loop is None:
            return empty_board()
        durable = dict((loop.get("context") or {}).get("durableRun") or {})
        tasks = self._fresh_tasks(durable.get("agentTasks") or [])
        output_id = str(((durable.get("productOwner") or {}).get("productOwnerOutputId")) or "")
        emitted = self.backlog.list_user_stories_for_output(loop["projectId"], output_id) if output_id else []
        stories = self._stories(tasks, {story["id"]: story for story in emitted})
        criteria = {
            story_id: [
                str(item.get("criterion") or "")
                for item in self.backlog.list_acceptance_criteria(story_id=story_id)
            ]
            for story_id in stories
        }
        progress = {
            str(entry.get("storyId") or ""): entry
            for entry in durable.get("storyProgress") or []
            if isinstance(entry, dict)
        }
        return build_board(
            loop_id=loop["id"],
            loop_state=loop["state"],
            agent_tasks=tasks,
            stories_by_id=stories,
            criteria_by_story=criteria,
            progress_by_story=progress,
            story_order=[story["id"] for story in emitted],
        )

    def _fresh_tasks(self, planned: list[Any]) -> list[dict[str, Any]]:
        tasks: list[dict[str, Any]] = []
        for item in planned:
            task_id = str((item or {}).get("id") or "").strip() if isinstance(item, dict) else ""
            if not task_id:
                continue
            try:
                tasks.append(self.backlog.get_agent_task(task_id))
            except KeyError:
                continue
        return tasks

    def _stories(
        self, tasks: list[dict[str, Any]], emitted: dict[str, dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        stories: dict[str, dict[str, Any]] = dict(emitted)
        for task in tasks:
            story_id = str(task.get("storyId") or "").strip()
            if not story_id or story_id in stories:
                continue
            try:
                stories[story_id] = self.backlog.get_user_story(story_id)
            except KeyError:
                continue
        return stories
