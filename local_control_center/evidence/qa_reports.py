from __future__ import annotations

from typing import Any


def build_markdown_report(
    *,
    evidence: dict[str, Any],
    test_results: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
) -> str:
    lines = [
        f"# QA Evidence Package: {evidence['taskId']}",
        "",
        f"- Evidence ID: `{evidence['id']}`",
        f"- Project ID: `{evidence['projectId']}`",
        f"- Workflow Run: `{evidence.get('workflowRunId') or 'none'}`",
        f"- Agent: `{evidence.get('agentId') or 'none'}`",
        f"- Verdict: {evidence['qaVerdict']}",
        f"- Source: {evidence.get('evidenceSource') or 'operator_attested'}",
        f"- Created: {evidence['createdAt']}",
        "",
        "## Test Plan",
        "",
        evidence.get("testPlan") or "No test plan recorded.",
        "",
        "## Acceptance Checklist",
        "",
    ]
    checklist = evidence.get("acceptanceChecklist") or []
    lines.extend(f"- {item}" for item in checklist) if checklist else lines.append("- No checklist items recorded.")
    lines.extend(["", "## Test Results", ""])
    if test_results:
        lines.extend(_test_result_line(result) for result in test_results)
    else:
        lines.append("- No normalized test results recorded.")
    lines.extend(["", "## Diff References", ""])
    diff_refs = evidence.get("diffRefs") or []
    if diff_refs:
        for diff_ref in diff_refs:
            kind = diff_ref.get("kind", "unknown")
            files = diff_ref.get("files") or diff_ref.get("changedFiles") or []
            lines.append(f"- {kind}: {len(files)} files")
    else:
        lines.append("- No diff references recorded.")
    lines.extend(["", "## Artifacts", ""])
    if artifacts:
        for artifact in artifacts:
            metadata = artifact.get("metadata") or {}
            name = metadata.get("name") or artifact["id"]
            size = metadata.get("sizeBytes", "unknown")
            lines.append(
                f"- `{artifact['id']}` {artifact['kind']} `{name}` hash `{artifact.get('hash') or 'none'}` size `{size}`"
            )
    else:
        lines.append("- No artifacts recorded.")
    lines.extend(["", "## Risk Notes", ""])
    risk_notes = evidence.get("riskNotes") or []
    lines.extend(f"- {note}" for note in risk_notes) if risk_notes else lines.append("- No risk notes recorded.")
    lines.append("")
    return "\n".join(lines)


def _test_result_line(result: dict[str, Any]) -> str:
    metadata = result.get("metadata") or {}
    counts = metadata.get("counts")
    counts_text = ""
    if isinstance(counts, dict):
        counts_text = " (" + ", ".join(f"{value} {key}" for key, value in sorted(counts.items())) + ")"
    duration = result.get("durationMs")
    duration_text = f", {duration}ms" if duration is not None else ""
    return f"- `{result.get('status', 'unknown')}` `{result.get('command', '')}`{counts_text}{duration_text}"
