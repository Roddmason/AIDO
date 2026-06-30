"""Clasificacion deterministica de intencion para el intake del Product Loop.

El clasificador convierte prompt, assessment, estado Git, archivos cambiados y modo del usuario en una
decision operativa trazable. No llama proveedores por defecto; el hook LLM opcional solo puede subir la
confianza cuando el resultado deterministico queda bajo el umbral, manteniendo la salida validada por las
taxonomias locales.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from local_control_center.shared.redaction import redact_secrets

INTENT_VALUES = (
    "bugfix",
    "feature",
    "refactor",
    "migration",
    "tests",
    "security",
    "docs",
    "architecture",
    "research",
    "cleanup",
)
RISK_VALUES = ("low", "medium", "high", "critical")
PLAN_MODE_VALUES = ("execute", "plan", "ask", "blocked")
LOW_CONFIDENCE_THRESHOLD = 0.55

KEYWORDS: dict[str, tuple[str, ...]] = {
    "bugfix": (
        "bug",
        "crash",
        "defect",
        "error",
        "exception",
        "failing",
        "failure",
        "fix",
        "hotfix",
        "regression",
        "broken",
    ),
    "feature": (
        "add",
        "build",
        "create",
        "implement",
        "new",
        "feature",
        "dashboard",
        "workflow",
        "endpoint",
        "screen",
    ),
    "refactor": (
        "refactor",
        "restructure",
        "rewrite",
        "extract",
        "simplify",
        "decouple",
        "rename",
        "reorganize",
        "modularize",
    ),
    "migration": ("migrate", "migration", "schema", "database", "sqlite", "alembic", "ddl"),
    "tests": ("test", "tests", "testing", "coverage", "pytest", "playwright", "regression"),
    "security": (
        "security",
        "pentest",
        "penetration",
        "harden",
        "vulnerability",
        "vuln",
        "xss",
        "csrf",
        "secret",
        "token",
        "auth",
        "exploit",
        "gitleaks",
        "semgrep",
    ),
    "docs": ("docs", "documentation", "readme", "guide", "manual", "runbook", "adr"),
    "architecture": (
        "architecture",
        "architectural",
        "adr",
        "contract",
        "boundary",
        "design",
        "module",
        "interface",
    ),
    "research": ("research", "investigate", "compare", "evaluate", "benchmark", "source", "cite"),
    "cleanup": ("cleanup", "clean up", "dead code", "unused", "remove obsolete", "tidy"),
}
SECURITY_CRITICAL_KEYWORDS = ("pentest", "penetration", "exploit", "vulnerability", "vuln")
FRONTEND_EXTENSIONS = (".tsx", ".jsx", ".css", ".scss", ".html")
FRONTEND_PATH_MARKERS = ("/web/", "/src/features/", "/frontend/", "/ui/", "local-control-center/web/")
BACKEND_EXTENSIONS = (".py", ".go", ".java", ".rs")
BACKEND_PATH_MARKERS = ("local_control_center/", "/backend/", "/api/", "/repository/", "/models/")
TEST_PATH_MARKERS = ("tests_py/", "tests_web/", "/test_", ".test.", ".spec.")
DOC_EXTENSIONS = (".md", ".rst", ".txt")
MIGRATION_PATH_MARKERS = ("migration", "migrations.py", "alembic", "schema")


@dataclass(frozen=True)
class IntentClassificationInput:
    """Entrada normalizada del clasificador de intencion."""

    prompt: str
    project_assessment: Mapping[str, Any] = field(default_factory=dict)
    changed_files: Sequence[str] = field(default_factory=tuple)
    git_state: Mapping[str, Any] = field(default_factory=dict)
    user_mode: str = "aido_decide"


@dataclass(frozen=True)
class IntentClassification:
    """Decision de intake que el Product Loop persiste y expone al operador."""

    intents: list[str]
    risk: str
    required_roles: list[str]
    required_gates: list[str]
    suggested_branch_name: str
    plan_mode: str
    confidence: float
    questions: list[str]
    user_mode: str

    def to_dict(self) -> dict[str, Any]:
        """Serializa la decision a la forma camelCase persistida en metadata."""
        return {
            "intents": list(self.intents),
            "risk": self.risk,
            "requiredRoles": list(self.required_roles),
            "requiredGates": list(self.required_gates),
            "suggestedBranchName": self.suggested_branch_name,
            "planMode": self.plan_mode,
            "confidence": self.confidence,
            "questions": list(self.questions),
            "userMode": self.user_mode,
            "source": "deterministic_intent_classifier",
        }


LlmClassifier = Callable[[IntentClassificationInput, IntentClassification], IntentClassification | None]


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _contains_keyword(text: str, keyword: str) -> bool:
    if " " in keyword:
        return keyword in text
    return re.search(rf"\b{re.escape(keyword)}\b", text) is not None


def _append_unique(items: list[str], *values: str) -> None:
    for value in values:
        if value and value not in items:
            items.append(value)


def _clean_path(value: str) -> str:
    return value.replace("\\", "/").strip().lower()


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48].strip("-") or "work"


def _has_executable_runtime(project_assessment: Mapping[str, Any]) -> bool | None:
    runtime_status = project_assessment.get("runtimeStatus")
    if not isinstance(runtime_status, Mapping):
        return None
    developer_agent = runtime_status.get("developerAgent")
    if isinstance(developer_agent, Mapping):
        return bool(developer_agent.get("executable"))
    providers = runtime_status.get("providers")
    if not isinstance(providers, Sequence) or isinstance(providers, (str, bytes)):
        return None
    if not providers:
        return False
    return any(
        bool(provider.get("executable") or provider.get("canEditWorkspace"))
        for provider in providers
        if isinstance(provider, Mapping)
    )


def _infer_file_signals(changed_files: Sequence[str]) -> dict[str, bool]:
    paths = [_clean_path(path) for path in changed_files]
    return {
        "frontend": any(
            path.endswith(FRONTEND_EXTENSIONS) or any(marker in path for marker in FRONTEND_PATH_MARKERS)
            for path in paths
        ),
        "backend": any(
            path.endswith(BACKEND_EXTENSIONS) or any(marker in path for marker in BACKEND_PATH_MARKERS)
            for path in paths
        ),
        "tests": any(any(marker in path for marker in TEST_PATH_MARKERS) for path in paths),
        "docs": any(path.endswith(DOC_EXTENSIONS) or "/docs/" in path for path in paths),
        "migration": any(any(marker in path for marker in MIGRATION_PATH_MARKERS) for path in paths),
    }


def _intent_scores(prompt: str, file_signals: Mapping[str, bool]) -> dict[str, int]:
    scores = dict.fromkeys(INTENT_VALUES, 0)
    for intent, keywords in KEYWORDS.items():
        scores[intent] += sum(1 for keyword in keywords if _contains_keyword(prompt, keyword))
    if file_signals["frontend"] or file_signals["backend"]:
        scores["feature"] += 1
    if file_signals["tests"]:
        scores["tests"] += 2
    if file_signals["docs"]:
        scores["docs"] += 2
    if file_signals["migration"]:
        scores["migration"] += 2
    return scores


def _risk_for(intents: Sequence[str], prompt: str, file_signals: Mapping[str, bool], git_state: Mapping[str, Any]) -> str:
    if "security" in intents and any(_contains_keyword(prompt, keyword) for keyword in SECURITY_CRITICAL_KEYWORDS):
        return "critical"
    if "migration" in intents or "architecture" in intents:
        return "high"
    if "security" in intents:
        return "high"
    if git_state.get("dirty") and (file_signals["backend"] or file_signals["migration"]):
        return "medium"
    if "bugfix" in intents or "refactor" in intents:
        return "medium"
    return "low"


def _roles_for(intents: Sequence[str], file_signals: Mapping[str, bool], risk: str) -> list[str]:
    roles: list[str] = []
    if file_signals["frontend"]:
        _append_unique(roles, "frontend_engineer")
    if file_signals["backend"] or "migration" in intents:
        _append_unique(roles, "backend_engineer")
    if "migration" in intents:
        _append_unique(roles, "database_engineer")
    if "architecture" in intents or "refactor" in intents or risk in {"high", "critical"}:
        _append_unique(roles, "software_architect")
    if "security" in intents:
        _append_unique(roles, "security_reviewer")
    if "security" in intents and risk == "critical":
        _append_unique(roles, "pentester")
    if "research" in intents:
        _append_unique(roles, "researcher")
    if "docs" in intents:
        _append_unique(roles, "technical_writer")
    if "bugfix" in intents or "tests" in intents or risk in {"medium", "high", "critical"}:
        _append_unique(roles, "qa_reviewer")
    if not roles:
        _append_unique(roles, "product_owner", "technical_lead")
    return roles


def _gates_for(intents: Sequence[str], file_signals: Mapping[str, bool], risk: str) -> list[str]:
    gates: list[str] = ["implementation_plan"]
    if file_signals["backend"] or "bugfix" in intents or "tests" in intents:
        _append_unique(gates, "pytest")
    if file_signals["frontend"]:
        _append_unique(gates, "typecheck_web", "build_web")
    if "bugfix" in intents:
        _append_unique(gates, "regression_tests")
    if "migration" in intents:
        _append_unique(gates, "migration_review")
    if "security" in intents:
        _append_unique(gates, "security_review", "gitleaks_scan")
    if "architecture" in intents or risk in {"high", "critical"}:
        _append_unique(gates, "architecture_review")
    if "docs" in intents:
        _append_unique(gates, "docs_review")
    if "research" in intents:
        _append_unique(gates, "source_citations")
    if risk in {"medium", "high", "critical"}:
        _append_unique(gates, "qa_review")
    return gates


def _confidence(scores: Mapping[str, int], intents: Sequence[str], prompt: str, runtime_available: bool | None) -> float:
    if runtime_available is False:
        return 0.95
    if not prompt:
        return 0.0
    if not intents:
        return 0.35
    strongest = max(scores.values() or [0])
    base = 0.45 + min(0.4, strongest * 0.12) + min(0.15, len(intents) * 0.03)
    return round(min(base, 0.95), 2)


def _plan_mode(confidence: float, runtime_available: bool | None, risk: str, user_mode: str) -> str:
    if runtime_available is False:
        return "blocked"
    if confidence < LOW_CONFIDENCE_THRESHOLD:
        return "ask"
    if user_mode in {"consulta", "consultation", "plan", "advisory"} or risk in {"high", "critical"}:
        return "plan"
    return "execute"


def _questions_for(plan_mode: str, intents: Sequence[str], runtime_available: bool | None) -> list[str]:
    questions: list[str] = []
    if runtime_available is False:
        questions.append("Configure an executable AIDO runtime before starting autonomous work.")
    if plan_mode == "ask":
        questions.append("What outcome should AIDO optimize for: diagnosis, implementation, or research?")
    if "migration" in intents and "refactor" in intents:
        questions.append("Should schema/data migration be shipped in the same change as the refactor?")
    return questions


class IntentClassifier:
    """Clasificador deterministico con hook LLM opcional para baja confianza."""

    def __init__(self, llm_classifier: LlmClassifier | None = None):
        self.llm_classifier = llm_classifier

    def classify(self, payload: IntentClassificationInput) -> IntentClassification:
        """Clasifica una entrada de Workbench en intencion, riesgo, roles, gates y modo."""
        prompt = _normalize_text(str(redact_secrets(payload.prompt or "")))
        user_mode = _normalize_text(payload.user_mode or "aido_decide").replace(" ", "_")
        file_signals = _infer_file_signals(payload.changed_files)
        scores = _intent_scores(prompt, file_signals)
        intents = [intent for intent in INTENT_VALUES if scores[intent] > 0]
        if not intents:
            intents = ["feature"] if prompt else []
        runtime_available = _has_executable_runtime(payload.project_assessment)
        risk = _risk_for(intents, prompt, file_signals, payload.git_state)
        confidence = _confidence(scores, intents, prompt, runtime_available)
        plan_mode = _plan_mode(confidence, runtime_available, risk, user_mode)
        gates = _gates_for(intents, file_signals, risk)
        if plan_mode == "blocked":
            _append_unique(gates, "runtime_configuration")
        decision = IntentClassification(
            intents=list(intents),
            risk=risk,
            required_roles=_roles_for(intents, file_signals, risk),
            required_gates=gates,
            suggested_branch_name=f"codex/{intents[0] if intents else 'task'}-{_slug(prompt)}",
            plan_mode=plan_mode,
            confidence=confidence,
            questions=_questions_for(plan_mode, intents, runtime_available),
            user_mode=user_mode,
        )
        if decision.plan_mode == "ask" and self.llm_classifier is not None:
            llm_decision = self.llm_classifier(payload, decision)
            if llm_decision is not None and llm_decision.confidence > decision.confidence:
                return self._validated(llm_decision)
        return decision

    def _validated(self, decision: IntentClassification) -> IntentClassification:
        intents = [intent for intent in decision.intents if intent in INTENT_VALUES]
        risk = decision.risk if decision.risk in RISK_VALUES else "medium"
        plan_mode = decision.plan_mode if decision.plan_mode in PLAN_MODE_VALUES else "ask"
        return IntentClassification(
            intents=intents or ["feature"],
            risk=risk,
            required_roles=list(dict.fromkeys(decision.required_roles)),
            required_gates=list(dict.fromkeys(decision.required_gates)),
            suggested_branch_name=decision.suggested_branch_name or "codex/task",
            plan_mode=plan_mode,
            confidence=max(0.0, min(1.0, float(decision.confidence))),
            questions=list(dict.fromkeys(decision.questions)),
            user_mode=decision.user_mode,
        )
