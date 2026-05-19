from __future__ import annotations


SENSITIVE_JOB_KINDS = {
    "pipeline.start",
    "pipeline.retry",
    "pipeline.stage.retry",
}


JOB_STATUSES = {
    "queued",
    "running",
    "approval_required",
    "completed",
    "failed",
    "cancelled",
}
