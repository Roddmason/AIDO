"""Fachada estable del worker de jobs: re-exporta ConcurrentWorker y execute_job.

Mantiene la ruta de import `local_control_center.worker` que esperan tests y entrypoints,
delegando la implementación real en `jobs_approvals.worker`.
"""

from .jobs_approvals.worker import ConcurrentWorker, execute_job

__all__ = ["ConcurrentWorker", "execute_job"]
