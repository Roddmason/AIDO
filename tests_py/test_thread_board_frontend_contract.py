"""Contrato fuente del tablero por historia en el frontend: cliente, modelo, etapa y layout.

Fija lo que la UI necesita del slice (spec §4-§5) sin depender de un navegador: la lectura
generada del tablero, los eventos que lo refrescan, la señal de etapa derivada del mismo pipeline
del panel de ejecución (``MILESTONE_INDEX`` sigue literal por ``test_product_loop_result_taxonomy``)
y los tripwires de clases del layout.

@author Rodrigo Mason
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "local-control-center" / "web" / "src"
SHELL = WEB / "features" / "shell"
APP = WEB / "app"
CLIENT = WEB / "api" / "client.ts"
LAYOUT_CSS = WEB / "design-system" / "layout.css"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_client_exposes_the_thread_board_read() -> None:
    source = _read(CLIENT)
    assert "export function getThreadBoard(threadId: string, signal?: AbortSignal)" in source
    assert "'get_thread_board_api_v1_threads__thread_id__board_get'" in source


def test_board_refreshes_on_story_progress_and_delivery_milestones() -> None:
    source = _read(SHELL / "threadBoardModel.ts")
    for event_type in (
        "story_progress",
        "executing",
        "qa_running",
        "security_running",
        "awaiting_approval",
        "delivered",
    ):
        assert f"'{event_type}'" in source, event_type
    assert "export function latestBoardRefreshSequence(" in source


def test_stage_signal_reuses_the_execution_panel_pipeline() -> None:
    panel = _read(SHELL / "ThreadExecutionPanel.tsx")
    stage = _read(SHELL / "useThreadStage.ts")
    assert "export function derivePipeline(" in panel
    assert "const MILESTONE_INDEX: Record<string, number> = {" in panel
    assert "blocked: isBlocked," in panel
    assert "data-presentation={presentation}" in panel
    assert "derivePipeline(events, threadStatus" in stage
    assert "inDevelopment" in stage


def test_board_hook_reads_the_thread_board() -> None:
    source = _read(SHELL / "useThreadBoard.ts")
    assert "getThreadBoard(threadId, controller.signal)" in source


def test_a_successful_remediation_wakes_the_idle_event_stream() -> None:
    stream = _read(SHELL / "useThreadEventStream.ts")
    remediations = _read(SHELL / "useThreadRemediations.ts")
    assert "export function wakeThreadEventStream(threadId: string): void" in stream
    assert "const streamWakers = new Map<string, Set<() => void>>();" in stream
    assert "wakeThreadEventStream(threadId);" in remediations


def test_thread_board_component_never_reuses_review_board_classes() -> None:
    source = _read(SHELL / "ThreadBoard.tsx")
    assert 'className="thread-board"' in source
    assert "review-board" not in source
    assert "review-column" not in source
    css = _read(LAYOUT_CSS)
    assert "@container thread-board" in css
    assert ".thread-board-column-scroll" in css


def test_thread_conversation_switches_into_board_mode() -> None:
    source = _read(SHELL / "ThreadConversation.tsx")
    assert "data-mode={boardMode}" in source
    assert "<ThreadBoard" in source
    assert "presentation={boardMode === 'board' ? 'strip' : 'column'}" in source
    shell = _read(APP / "AppShell.tsx")
    assert "<ShellInspectorContext.Provider value={shellInspectorContext}>" in shell
    css = _read(LAYOUT_CSS)
    assert '.thread-live-body[data-mode="board"]' in css
    assert "@container thread-live" in css
    assert ".thread-transcript-pane" in css
    assert ".thread-live-scroll" in css
    assert ".thread-composer-dock" in css
    assert ".thread-execution-pane" in css
    assert ".thread-pipeline-step" in css
