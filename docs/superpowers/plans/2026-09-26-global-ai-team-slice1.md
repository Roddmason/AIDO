# Equipo de IA global — rebanada 1 (switches, asignación por rol, ruteo) — plan de implementación

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un switch por proveedor en *Providers & CLI*, un equipo de IA global por rol (general > proyecto > automático) que reemplaza el confinamiento al proveedor del PO, failover al siguiente proveedor activo del orden del rol, y la UI que lo muestra (panel de equipo, chip del intake, inspector, barra de estado).

**Architecture:** La asignación vive en settings `team.role.<rol>` (`string_list`, scopes general/project). `runtime_team/global_team.py` la resuelve contra los proveedores activos y elegibles (`load_runtime_facts`) y produce un snapshot que `seal_thread_runtime_team` sella en la metadata del run como `globalRuntimeTeam` cuando el hilo no tiene override. `role_allowlist` devuelve el orden del rol (asignado + fallbacks); el coordinator lo pasa como allowlist y como preferencia de ruteo, y el failover existente recorre el orden. El equipo por hilo (`runtimeTeam`) sigue igual y tiene prioridad.

**Tech Stack:** Python 3.13 / FastAPI / SQLite (backend `local_control_center/`), React 19 + TypeScript + Biome (web `local-control-center/web/src/`), pytest, Playwright.

**Spec:** `docs/superpowers/specs/2026-09-26-global-ai-team-and-provider-sessions-design.md` (§4.1, §4.2, §4.6, §4.7, §4.8, §4.10; rebanada 1 de §5).

## Global Constraints

- Encabezado de cada archivo productivo nuevo: docstring/JSDoc en español con `@author Rodrigo Mason` (gate `test:py`).
- Copy de UI siempre por `t('clave', 'fallback en inglés')` y clave registrada en `local_control_center/i18n/default_catalog.json` con `en` y `es` distintos (gate i18n). Fallback en inglés = texto `en` del catálogo, exacto.
- Archivos nuevos y editados terminan en LF (nunca CRLF). Tras un `Path.write_text` o edición multilínea, verificar con `python -c "import sys; print(b'\r\n' in open(sys.argv[1],'rb').read())" <archivo>` → `False`.
- Nuevo contrato de respuesta HTTP ⇒ `response_model` tipado en Pydantic + `corepack pnpm@10.24.0 run openapi:generate` (gate de drift del cliente generado).
- Comando pytest (faiss revienta al importar en este equipo; el wrapper lo desactiva):
  `PYTEST="uv run python -c \"import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(sys.argv[1:]))\""` — invocar como `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly', '<test>']))"`.
- Lint/format Python: `uv run --extra dev ruff format <archivos>` y `uv run --extra dev ruff check <archivos>` (el pre-commit los exige).
- Web: `corepack pnpm@10.24.0 run typecheck:web` y `corepack pnpm@10.24.0 run check:web` (Biome; solo ERRORS rompen).
- Playwright de un spec: `PLAYWRIGHT_DASHBOARD_PORT=4995 node scripts/run-web-tests.mjs tests_web/<spec>.js` (puerto propio por corrida; construye el bundle solo).
- Commits: `Tipo (Ámbito): mensaje` (`Feature`, `Fix`, `Test`, `Docs`, `Refactor`) y última línea `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Commit + push a `dev` por tarea terminada (solo en este repo).
- No editar la BD viva (`~/.claude/local-control-center/platform.sqlite`) a mano. No modificar `.gitleaks.toml`.
- Todo bug que aparezca en el camino (propio o ajeno) se corrige en su propio commit y se anota en la sección "Bugs encontrados" del cierre.

---

### Task 1: Settings `team.role.<rol>` (registro + i18n)

**Files:**
- Modify: `local_control_center/settings/registry.py` (después del cierre `]` de `REGISTRY`, antes de `REGISTRY.extend(` de `decision_engine`)
- Modify: `local_control_center/i18n/default_catalog.json` (6 claves nuevas)
- Test: `tests_py/test_global_team.py` (nuevo)

**Interfaces:**
- Produces: `GLOBAL_TEAM_ROLE_KEYS: tuple[str, ...]` en `settings/registry.py` = `("product_owner", "developer", "architect", "security", "technical_lead", "researcher")`; descriptores `team.role.<rol>` (`type="string_list"`, `section="team"`, `project_section="team"`, `default=[]`, `label_key=f"app.settings.team.role.{rol}"`).

- [ ] **Step 1: Test que falla**

```python
# tests_py/test_global_team.py
"""Equipo de IA global: settings por rol, resolución sobre proveedores activos y sellado.

@author Rodrigo Mason
"""

from __future__ import annotations

import json
from pathlib import Path

from local_control_center.settings.registry import GLOBAL_TEAM_ROLE_KEYS, descriptor_for, validate_value

CATALOG = Path("local_control_center/i18n/default_catalog.json")


def test_every_global_team_role_has_a_project_overridable_string_list_setting() -> None:
    assert GLOBAL_TEAM_ROLE_KEYS == (
        "product_owner",
        "developer",
        "architect",
        "security",
        "technical_lead",
        "researcher",
    )
    translations = json.loads(CATALOG.read_text(encoding="utf-8"))["translations"]
    for role in GLOBAL_TEAM_ROLE_KEYS:
        descriptor = descriptor_for(f"team.role.{role}")
        assert descriptor is not None
        assert descriptor.type == "string_list"
        assert descriptor.section == "team"
        assert descriptor.project_section == "team"
        assert descriptor.default == []
        assert validate_value(descriptor, ["claude_code_cli", "llama_cpp"]) == ["claude_code_cli", "llama_cpp"]
        assert translations[descriptor.label_key]["en"] != translations[descriptor.label_key]["es"]
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_global_team.py']))"`
Expected: FAIL con `ImportError: cannot import name 'GLOBAL_TEAM_ROLE_KEYS'`.

- [ ] **Step 3: Registrar los descriptores**

En `local_control_center/settings/registry.py`, justo después de la línea `]` que cierra la lista literal `REGISTRY` (la que sigue al descriptor `project.git.workBranchPrefix`) y antes del `REGISTRY.extend(` de `decision_engine`, agregar:

```python
GLOBAL_TEAM_ROLE_KEYS: tuple[str, ...] = (
    "product_owner",
    "developer",
    "architect",
    "security",
    "technical_lead",
    "researcher",
)
"""Roles del equipo de IA global con asignación propia (`team.role.<rol>`).

Los cuatro primeros son los roles del equipo por hilo; `technical_lead` y `researcher` heredan del
PO cuando su lista queda vacía (`runtime_team/global_team.py`). QA, DevOps y aido_lead no usan modelo.
"""

REGISTRY.extend(
    SettingDescriptor(
        key=f"team.role.{role}",
        section="team",
        project_section="team",
        type="string_list",
        default=[],
        label_key=f"app.settings.team.role.{role}",
    )
    for role in GLOBAL_TEAM_ROLE_KEYS
)
```

- [ ] **Step 4: Registrar las 6 claves i18n**

Agregar dentro de `"translations"` de `local_control_center/i18n/default_catalog.json` (cualquier posición; mantener indentación de 2 espacios y LF), usando este script desde la raíz del repo:

```bash
uv run python - <<'PY'
import json
from pathlib import Path

path = Path("local_control_center/i18n/default_catalog.json")
catalog = json.loads(path.read_text(encoding="utf-8"))
entries = {
    "app.settings.team.role.product_owner": {"en": "Product Owner runtimes (in order)", "es": "Runtimes del Product Owner (en orden)"},
    "app.settings.team.role.developer": {"en": "Developer runtimes (in order)", "es": "Runtimes del Developer (en orden)"},
    "app.settings.team.role.architect": {"en": "Architect runtimes (in order)", "es": "Runtimes del Arquitecto (en orden)"},
    "app.settings.team.role.security": {"en": "Security runtimes (in order)", "es": "Runtimes de Seguridad (en orden)"},
    "app.settings.team.role.technical_lead": {"en": "Technical Lead runtimes (in order; empty inherits the Product Owner)", "es": "Runtimes del Technical Lead (en orden; vacío hereda del Product Owner)"},
    "app.settings.team.role.researcher": {"en": "Researcher runtimes (in order; empty inherits the Product Owner)", "es": "Runtimes del Researcher (en orden; vacío hereda del Product Owner)"},
}
for key, value in entries.items():
    assert key not in catalog["translations"], key
    catalog["translations"][key] = value
path.write_bytes((json.dumps(catalog, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print("added", len(entries))
PY
```

Verificar que el archivo no cambió de formato más allá de las claves nuevas: `git diff --stat local_control_center/i18n/default_catalog.json` debe mostrar ~24 líneas agregadas y 0 eliminadas (si el diff reescribe todo el archivo, el dump cambió el formato: revisar `indent`/`ensure_ascii` antes de seguir).

- [ ] **Step 5: Correr el test en verde y los gates de settings/i18n**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_global_team.py','tests_py/test_i18n_platform.py','tests_py/test_settings_api.py']))"`
Expected: PASS (si `tests_py/test_settings_api.py` no existe con ese nombre, usar `ls tests_py | grep -i settings` y correr el que cubre `/api/v1/settings`).

- [ ] **Step 6: Commit**

```bash
uv run --extra dev ruff format local_control_center/settings/registry.py tests_py/test_global_team.py && uv run --extra dev ruff check local_control_center/settings/registry.py tests_py/test_global_team.py
git add local_control_center/settings/registry.py local_control_center/i18n/default_catalog.json tests_py/test_global_team.py
git commit -m "Feature (Settings): asignacion de runtimes por rol del equipo de IA global, general con override por proyecto" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: `runtime_team/global_team.py` — resolución del equipo global

**Files:**
- Create: `local_control_center/runtime_team/global_team.py`
- Test: `tests_py/test_global_team.py` (ampliar)

**Interfaces:**
- Consumes: `load_runtime_facts(connection, project_id=...)` (`runtime_team/facts.py`), `auto_assign_roles(runtimes, runtime_order)` y `TEAM_ROLES`/`REQUIRED_TEAM_ROLES` (`runtime_team/roles.py`), `RuntimeConfigRepository.get_preferences()` (`runtimeOrder`), `SettingsRepository.get_value(key, scope, scope_id)` + `UNSET`, `resolve_setting_value(key="project.runtime.allowedProviders")`.
- Produces:
  - `GLOBAL_TEAM_ROLES`, `DERIVED_TEAM_ROLES = ("technical_lead", "researcher")`, `TEAM_ROLE_SOURCES = ("project", "general", "automatic", "inherited")`.
  - `@dataclass(frozen=True) RoleAssignment(role, configured: tuple[str, ...], effective: tuple[str, ...], source: str, invalid: tuple[str, ...])` con propiedad `assigned -> str | None`.
  - `@dataclass(frozen=True) GlobalTeam(roles: Mapping[str, RoleAssignment], allowed_runtimes: tuple[str, ...])` con `role_order(role) -> list[str]` y `sealed() -> dict[str, Any]`.
  - `resolve_global_team(connection, *, project_id: str | None, facts: Mapping[str, RuntimeFacts] | None = None) -> GlobalTeam`.
  - `describe_global_team(connection, *, project_id: str | None) -> dict[str, Any]` (cuerpo de `GET /api/v1/runtime/team`, Task 5).

- [ ] **Step 1: Tests que fallan**

Agregar a `tests_py/test_global_team.py`:

```python
from contextlib import closing

import pytest

from local_control_center.agents.provider_accounts import ProviderAccountStore
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.runtime_team.global_team import (
    DERIVED_TEAM_ROLES,
    GLOBAL_TEAM_ROLES,
    resolve_global_team,
)
from local_control_center.runtime_team.roles import RuntimeFacts
from local_control_center.settings.repository import SettingsRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

CLAUDE = RuntimeFacts("claude_code_cli", "Claude Code CLI", "cli", ("product_owner", "developer", "architect"))
CODEX = RuntimeFacts("codex_cli", "Codex CLI", "cli", ("product_owner", "developer"))
LLAMA = RuntimeFacts("llama_cpp", "llama.cpp", "local", ("product_owner", "developer", "architect", "security"))
DENIED = RuntimeFacts("gemini", "Gemini", "api", ("product_owner",), policy_denied_reason="project.runtime.allowedProviders")
FACTS = {item.provider_id: item for item in (CLAUDE, CODEX, LLAMA, DENIED)}


@pytest.fixture
def lane(tmp_path: Path):
    with closing(open_sqlite_connection(tmp_path / "platform.sqlite")) as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="Global team", path=tmp_path / "project", template_id="other"
        )
        yield connection, project


def _set(connection, key: str, value: list[str], *, project_id: str | None = None) -> None:
    scope = "project" if project_id else "general"
    SettingsRepository(connection).set_value(key, scope, project_id, value)


def test_roles_are_the_registered_setting_keys() -> None:
    assert GLOBAL_TEAM_ROLES == GLOBAL_TEAM_ROLE_KEYS
    assert DERIVED_TEAM_ROLES == ("technical_lead", "researcher")


def test_without_configuration_the_team_is_the_automatic_split_with_fallbacks(lane) -> None:
    connection, project = lane
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)

    # Reparto automático (CLI primero, luego id): developer=claude, PO=codex; el resto del orden
    # elegible queda como fallback. Gemini está vetado por la política y nunca aparece.
    assert team.roles["developer"].source == "automatic"
    assert team.roles["developer"].effective == ("claude_code_cli", "codex_cli", "llama_cpp")
    assert team.roles["product_owner"].effective == ("codex_cli", "claude_code_cli", "llama_cpp")
    assert team.roles["security"].effective == ("llama_cpp",)
    assert team.roles["technical_lead"].source == "inherited"
    assert team.roles["technical_lead"].effective == team.roles["product_owner"].effective
    assert "gemini" not in team.allowed_runtimes


def test_general_configuration_orders_the_role_and_project_overrides_it(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.developer", ["llama_cpp", "claude_code_cli"])
    _set(connection, "team.role.developer", ["codex_cli"], project_id=project["id"])
    _set(connection, "team.role.product_owner", ["llama_cpp"])

    general = resolve_global_team(connection, project_id=None, facts=FACTS)
    assert general.roles["developer"].source == "general"
    assert general.roles["developer"].effective == ("llama_cpp", "claude_code_cli")
    assert general.roles["developer"].assigned == "llama_cpp"

    project_team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert project_team.roles["developer"].source == "project"
    assert project_team.roles["developer"].effective == ("codex_cli",)
    assert project_team.roles["product_owner"].source == "general"
    assert project_team.allowed_runtimes == ("codex_cli", "llama_cpp")


def test_invalid_ids_are_reported_and_dropped_and_an_empty_list_means_automatic(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.security", ["claude_code_cli", "ghost", "llama_cpp"])
    _set(connection, "team.role.developer", [], project_id=project["id"])
    _set(connection, "team.role.developer", ["codex_cli"])

    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    # Claude no es elegible para security y ghost no existe: quedan reportados, no rompen.
    assert team.roles["security"].effective == ("llama_cpp",)
    assert team.roles["security"].invalid == ("claude_code_cli", "ghost")
    # La lista vacía del proyecto no es un override: hereda la general.
    assert team.roles["developer"].source == "general"
    assert team.roles["developer"].effective == ("codex_cli",)


def test_the_sealed_form_carries_orders_sources_and_the_assigned_runtime(lane) -> None:
    connection, project = lane
    _set(connection, "team.role.developer", ["codex_cli", "llama_cpp"])
    sealed = resolve_global_team(connection, project_id=project["id"], facts=FACTS).sealed()
    assert sealed["roleRuntimeOrder"]["developer"] == ["codex_cli", "llama_cpp"]
    assert sealed["roleRuntimes"]["developer"] == "codex_cli"
    assert sealed["source"]["developer"] == "general"
    assert sealed["source"]["researcher"] == "inherited"
    assert set(sealed) == {"roleRuntimeOrder", "roleRuntimes", "source", "allowedRuntimes"}


def test_the_project_runtime_allowlist_narrows_the_global_team(lane) -> None:
    connection, project = lane
    _set(connection, "project.runtime.allowedProviders", ["llama_cpp"], project_id=project["id"])
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert team.allowed_runtimes == ("llama_cpp",)
    assert team.roles["developer"].effective == ("llama_cpp",)


def test_the_operator_runtime_order_breaks_ties_in_the_automatic_split(lane) -> None:
    connection, project = lane
    RuntimeConfigRepository(connection).upsert_preferences({"scope": "global", "runtimeOrder": ["codex_cli", "claude_code_cli"]})
    team = resolve_global_team(connection, project_id=project["id"], facts=FACTS)
    assert team.roles["developer"].effective[0] == "codex_cli"
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_global_team.py']))"`
Expected: FAIL con `ModuleNotFoundError: local_control_center.runtime_team.global_team`.

- [ ] **Step 3: Implementar el módulo**

```python
# local_control_center/runtime_team/global_team.py
"""Equipo de IA global: asignación de runtimes por rol para todos los hilos, general > proyecto.

Reemplaza el confinamiento de todos los roles al proveedor del PO (``role_allowlist`` sin equipo por
hilo): cada rol tiene una lista ordenada de proveedores (`team.role.<rol>`, settings con override por
proyecto). El primero es el asignado; los siguientes son fallback. Una lista vacía significa
"automático": el reparto determinista de ``auto_assign_roles`` sobre los proveedores activos y
elegibles, seguido del resto de elegibles en el mismo ranking. ``technical_lead`` y ``researcher``
heredan del PO cuando quedan vacíos. Los ids que no son activos ni elegibles se reportan (`invalid`)
y no rompen la resolución. La política del proyecto (`project.runtime.allowedProviders` y el veto de
`runtime_policy_decision`) solo restringe.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from local_control_center.runtime_integrations.repository import RuntimeConfigRepository
from local_control_center.settings.registry import GLOBAL_TEAM_ROLE_KEYS
from local_control_center.settings.repository import UNSET, SettingsRepository
from local_control_center.settings.resolver import resolve_setting_value

from .facts import load_runtime_facts
from .roles import REQUIRED_TEAM_ROLES, TEAM_ROLES, RuntimeFacts, auto_assign_roles

GLOBAL_TEAM_ROLES: tuple[str, ...] = GLOBAL_TEAM_ROLE_KEYS
DERIVED_TEAM_ROLES: tuple[str, ...] = ("technical_lead", "researcher")
"""Roles sin contrato propio de elegibilidad: usan la elegibilidad del PO y heredan su orden si están vacíos."""
TEAM_ROLE_SOURCES: tuple[str, ...] = ("project", "general", "automatic", "inherited")
TEAM_ROLE_SETTING_PREFIX = "team.role."


@dataclass(frozen=True)
class RoleAssignment:
    """Orden configurado y efectivo de un rol, con su procedencia y los ids descartados."""

    role: str
    configured: tuple[str, ...]
    effective: tuple[str, ...]
    source: str
    invalid: tuple[str, ...] = ()

    @property
    def assigned(self) -> str | None:
        """Proveedor asignado: el primero del orden efectivo, o ``None`` si el rol no tiene candidatos."""
        return self.effective[0] if self.effective else None


@dataclass(frozen=True)
class GlobalTeam:
    """Equipo global resuelto para un proyecto (o para el scope general con ``project_id=None``)."""

    roles: Mapping[str, RoleAssignment]
    allowed_runtimes: tuple[str, ...]

    def role_order(self, role: str) -> list[str]:
        """Orden efectivo del rol (asignado primero); vacío si el rol no existe o no tiene candidatos."""
        assignment = self.roles.get(role)
        return list(assignment.effective) if assignment else []

    def sealed(self) -> dict[str, Any]:
        """Forma que se sella en la metadata del run (`globalRuntimeTeam`); solo datos, sin objetos."""
        return {
            "roleRuntimeOrder": {role: list(item.effective) for role, item in self.roles.items()},
            "roleRuntimes": {role: item.assigned for role, item in self.roles.items()},
            "source": {role: item.source for role, item in self.roles.items()},
            "allowedRuntimes": list(self.allowed_runtimes),
        }


def _clean_ids(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(item).strip() for item in values or [] if str(item).strip()))


def _configured_order(repo: SettingsRepository, role: str, project_id: str | None) -> tuple[list[str], str]:
    """Lista configurada del rol y su scope. Una lista vacía no es un override: se sigue heredando."""
    key = f"{TEAM_ROLE_SETTING_PREFIX}{role}"
    if project_id:
        project_value = repo.get_value(key, "project", project_id)
        if project_value is not UNSET and _clean_ids(project_value):
            return _clean_ids(project_value), "project"
    general_value = repo.get_value(key, "general", None)
    if general_value is not UNSET and _clean_ids(general_value):
        return _clean_ids(general_value), "general"
    return [], "automatic"


def _eligibility_role(role: str) -> str:
    return "product_owner" if role in DERIVED_TEAM_ROLES else role


def _ranked(facts: Sequence[RuntimeFacts], runtime_order: Sequence[str]) -> list[RuntimeFacts]:
    """Mismo ranking que ``auto_assign_roles``: CLI primero, luego ``runtimeOrder``, luego id."""
    order = {provider_id: index for index, provider_id in enumerate(runtime_order)}
    return sorted(facts, key=lambda item: (item.kind != "cli", order.get(item.provider_id, len(order)), item.provider_id))


def _runtime_order(connection: sqlite3.Connection) -> list[str]:
    try:
        preferences = RuntimeConfigRepository(connection).get_preferences()
    except KeyError:
        return []
    return _clean_ids(preferences.get("runtimeOrder") or [])


def _active_facts(
    connection: sqlite3.Connection, *, project_id: str | None, facts: Mapping[str, RuntimeFacts] | None
) -> dict[str, RuntimeFacts]:
    """Proveedores habilitados, no vetados por la política y dentro de `project.runtime.allowedProviders`."""
    source = facts if facts is not None else load_runtime_facts(connection, project_id=project_id)
    active = {provider_id: item for provider_id, item in source.items() if not item.policy_denied_reason}
    if project_id:
        allowed = set(
            _clean_ids(
                resolve_setting_value(
                    connection=connection, key="project.runtime.allowedProviders", project_id=project_id
                )
                or []
            )
        )
        if allowed:
            active = {provider_id: item for provider_id, item in active.items() if provider_id in allowed}
    return active


def resolve_global_team(
    connection: sqlite3.Connection,
    *,
    project_id: str | None,
    facts: Mapping[str, RuntimeFacts] | None = None,
) -> GlobalTeam:
    """Resuelve el equipo global: por rol, configurado (project > general) o automático, filtrado a activos."""
    active = _active_facts(connection, project_id=project_id, facts=facts)
    ranked = _ranked(list(active.values()), _runtime_order(connection))
    automatic = auto_assign_roles(ranked, _runtime_order(connection))
    repo = SettingsRepository(connection)
    roles: dict[str, RoleAssignment] = {}
    for role in GLOBAL_TEAM_ROLES:
        eligibility = _eligibility_role(role)
        eligible = [item.provider_id for item in ranked if eligibility in item.eligible_roles]
        configured, source = _configured_order(repo, role, project_id)
        if configured:
            effective = [provider_id for provider_id in configured if provider_id in eligible]
            invalid = tuple(provider_id for provider_id in configured if provider_id not in eligible)
            roles[role] = RoleAssignment(role, tuple(configured), tuple(effective), source, invalid)
            continue
        if role in DERIVED_TEAM_ROLES:
            roles[role] = RoleAssignment(role, (), roles["product_owner"].effective, "inherited")
            continue
        first = automatic.get(role)
        effective = ([first] if first else []) + [provider_id for provider_id in eligible if provider_id != first]
        roles[role] = RoleAssignment(role, (), tuple(effective), "automatic")
    allowed = _clean_ids(provider_id for item in roles.values() for provider_id in item.effective)
    return GlobalTeam(roles=roles, allowed_runtimes=tuple(allowed))


def describe_global_team(connection: sqlite3.Connection, *, project_id: str | None) -> dict[str, Any]:
    """Cuerpo de ``GET /api/v1/runtime/team``: equipo efectivo por rol más los candidatos activos."""
    from .candidates import RuntimeTeamCandidatesService

    facts = load_runtime_facts(connection, project_id=project_id)
    team = resolve_global_team(connection, project_id=project_id, facts=facts)
    candidates = RuntimeTeamCandidatesService(connection).list_candidates(project_id=project_id, selected=None)
    active = _active_facts(connection, project_id=project_id, facts=facts)
    ranked = _ranked(list(active.values()), _runtime_order(connection))
    return {
        "roles": [
            {
                "role": role,
                "required": role in REQUIRED_TEAM_ROLES,
                "configured": list(item.configured),
                "effective": list(item.effective),
                "assigned": item.assigned,
                "source": item.source,
                "invalid": list(item.invalid),
                "candidates": [
                    fact.provider_id for fact in ranked if _eligibility_role(role) in fact.eligible_roles
                ],
            }
            for role, item in team.roles.items()
        ],
        "allowedRuntimes": list(team.allowed_runtimes),
        "activeProviders": len(active),
        "candidates": candidates["candidates"],
    }


__all__ = [
    "DERIVED_TEAM_ROLES",
    "GLOBAL_TEAM_ROLES",
    "TEAM_ROLES",
    "TEAM_ROLE_SETTING_PREFIX",
    "TEAM_ROLE_SOURCES",
    "GlobalTeam",
    "RoleAssignment",
    "describe_global_team",
    "resolve_global_team",
]
```

Nota: `_ranked` y `auto_assign_roles` reciben `facts` como objetos `RuntimeFacts` (no dicts); `auto_assign_roles` ya existe en `runtime_team/roles.py:96-117` con esa firma.

- [ ] **Step 4: Correr en verde**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_global_team.py']))"`
Expected: PASS (8 tests). Si `test_the_operator_runtime_order_breaks_ties_in_the_automatic_split` falla porque `upsert_preferences` exige más campos, revisar `runtime_integrations/repository.py:576-640` (`upsert_preferences(body)` acepta `scope`, `runtimeOrder`) y ajustar el test, no el módulo.

- [ ] **Step 5: Commit**

```bash
uv run --extra dev ruff format local_control_center/runtime_team/global_team.py tests_py/test_global_team.py && uv run --extra dev ruff check local_control_center/runtime_team/global_team.py tests_py/test_global_team.py
git add local_control_center/runtime_team/global_team.py tests_py/test_global_team.py
git commit -m "Feature (RuntimeTeam): resolucion del equipo de IA global por rol sobre los proveedores activos" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Sellado `globalRuntimeTeam` y `role_allowlist` por orden del rol

**Files:**
- Modify: `local_control_center/runtime_team/configuration.py` (`seal_thread_runtime_team`, `role_allowlist`, funciones nuevas)
- Modify: `local_control_center/threads/coordinator.py:65-85` (set `_PUBLIC_MESSAGE_PROTECTED_PRODUCT_LOOP_METADATA_KEYS`)
- Test: `tests_py/test_runtime_team_configuration.py` (ampliar), `tests_py/test_thread_run_configuration.py` (revisar que el stripping cubra la clave nueva)

**Interfaces:**
- Produces en `runtime_team/configuration.py`:
  - `GLOBAL_RUNTIME_TEAM_METADATA_KEY = "globalRuntimeTeam"`
  - `global_team_of(request_meta) -> dict[str, Any] | None` (snapshot sellado validado: `roleRuntimeOrder` dict de listas, `source` dict, `allowedRuntimes` lista).
  - `global_role_order(request_meta, team_role: str | None) -> list[str]`: orden del rol; `None`/rol desconocido ⇒ orden del PO; rol sin candidatos ⇒ `allowedRuntimes`.
  - `global_role_is_explicit(request_meta, team_role) -> bool`: `source` en `{"project", "general"}`.
  - `role_routing_preferences(request_meta, team_role) -> tuple[list[str], list[dict[str, str]]]`: `(orden, [{"provider": id, "model": ""} …])`, o `([], [])` si aplica un equipo por hilo o no hay snapshot global.
- Cambia: `role_allowlist(request_meta, team_role, *, product_owner_provider_id=None)`: sin equipo por hilo y con snapshot global devuelve `global_role_order(...)`; sin snapshot conserva la herencia del PO (runs sellados antes de este cambio).

- [ ] **Step 1: Tests que fallan**

Agregar a `tests_py/test_runtime_team_configuration.py` (usar la fixture `lane` existente del archivo, líneas ~60-75, y los imports ya presentes; sumar los imports nuevos al bloque `from local_control_center.runtime_team.configuration import (...)`):

```python
from local_control_center.runtime_team.configuration import (  # ampliar el import existente
    GLOBAL_RUNTIME_TEAM_METADATA_KEY,
    global_role_is_explicit,
    global_role_order,
    global_team_of,
    role_routing_preferences,
)

GLOBAL = {
    GLOBAL_RUNTIME_TEAM_METADATA_KEY: {
        "roleRuntimeOrder": {
            "product_owner": ["codex_cli", "ollama"],
            "developer": ["codex_cli"],
            "architect": [],
            "security": ["ollama"],
            "technical_lead": ["codex_cli", "ollama"],
            "researcher": ["codex_cli", "ollama"],
        },
        "roleRuntimes": {"product_owner": "codex_cli", "developer": "codex_cli", "architect": None, "security": "ollama", "technical_lead": "codex_cli", "researcher": "codex_cli"},
        "source": {"product_owner": "general", "developer": "project", "architect": "automatic", "security": "automatic", "technical_lead": "inherited", "researcher": "inherited"},
        "allowedRuntimes": ["codex_cli", "ollama"],
    }
}


def test_the_global_team_gives_each_role_its_order_with_fallbacks():
    assert role_allowlist(GLOBAL, "developer") == ["codex_cli"]
    assert role_allowlist(GLOBAL, "product_owner") == ["codex_cli", "ollama"]
    assert role_allowlist(GLOBAL, "security") == ["ollama"]
    # Sin candidatos propios (arquitecto vacío) el rol puede usar cualquier runtime del equipo.
    assert role_allowlist(GLOBAL, "architect") == ["codex_cli", "ollama"]
    # Roles sin asignación propia (aido_lead, qa_engineer) siguen el orden del PO.
    assert role_allowlist(GLOBAL, None) == ["codex_cli", "ollama"]
    # La herencia del proveedor del PO del loop ya no confina al rol cuando hay equipo global.
    assert role_allowlist(GLOBAL, "developer", product_owner_provider_id="ollama") == ["codex_cli"]
    assert global_role_order(GLOBAL, "technical_lead") == ["codex_cli", "ollama"]


def test_the_thread_team_keeps_priority_over_the_global_snapshot():
    meta = {
        **GLOBAL,
        RUNTIME_TEAM_METADATA_KEY: {
            "allowedRuntimes": ["ollama"],
            "roleRuntimes": {"product_owner": "ollama", "developer": "ollama"},
        },
    }
    assert role_allowlist(meta, "developer") == ["ollama"]
    assert role_routing_preferences(meta, "developer") == ([], [])


def test_routing_preferences_follow_the_global_order_as_provider_wildcards():
    order, wildcards = role_routing_preferences(GLOBAL, "product_owner")
    assert order == ["codex_cli", "ollama"]
    assert wildcards == [{"provider": "codex_cli", "model": ""}, {"provider": "ollama", "model": ""}]
    assert global_role_is_explicit(GLOBAL, "developer") is True
    assert global_role_is_explicit(GLOBAL, "security") is False
    assert global_role_is_explicit({}, "developer") is False


def test_a_malformed_global_snapshot_is_ignored():
    assert global_team_of({GLOBAL_RUNTIME_TEAM_METADATA_KEY: {"roleRuntimeOrder": "nope"}}) is None
    assert global_team_of({GLOBAL_RUNTIME_TEAM_METADATA_KEY: []}) is None
    assert role_allowlist({GLOBAL_RUNTIME_TEAM_METADATA_KEY: {}}, "developer", product_owner_provider_id="ollama") == ["ollama"]


def test_sealing_a_thread_without_override_snapshots_the_global_team(lane):
    connection, project, thread = lane
    SettingsRepository(connection).set_value("team.role.developer", "general", None, ["codex_cli"])
    stamped = seal_thread_runtime_team(
        connection, project_id=project["id"], thread_id=thread["id"], metadata={GLOBAL_RUNTIME_TEAM_METADATA_KEY: {"forged": True}}
    )
    snapshot = stamped[GLOBAL_RUNTIME_TEAM_METADATA_KEY]
    assert "forged" not in snapshot
    assert snapshot["roleRuntimeOrder"]["developer"] == ["codex_cli"]
    assert snapshot["source"]["developer"] == "general"
    assert RUNTIME_TEAM_METADATA_KEY not in stamped


def test_sealing_a_thread_with_override_drops_the_global_snapshot(lane):
    connection, project, thread = lane
    _save(connection, project, thread)
    _validate(connection, "codex_cli", "gpt-5.5")
    _validate(connection, "ollama", "llama3")
    stamped = seal_thread_runtime_team(
        connection, project_id=project["id"], thread_id=thread["id"], metadata={GLOBAL_RUNTIME_TEAM_METADATA_KEY: {"stale": True}}
    )
    assert GLOBAL_RUNTIME_TEAM_METADATA_KEY not in stamped
    assert stamped[RUNTIME_TEAM_METADATA_KEY]["roleRuntimes"]["developer"] == "codex_cli"
```

Y en `tests_py/test_thread_run_configuration.py` (o donde se pruebe el stripping de `runtimeTeam` entrante; localizar con `grep -n "RUNTIME_TEAM_METADATA_KEY\|runtimeTeam" tests_py/test_thread_run_configuration.py tests_py/test_runtime_team_thread_api.py`), agregar al test que verifica que un `runtimeTeam` del cliente se descarta una aserción equivalente para `globalRuntimeTeam` (el mensaje POST con `metadata: {"globalRuntimeTeam": {...}}` no llega al run como tal).

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_runtime_team_configuration.py']))"`
Expected: FAIL con `ImportError` de `GLOBAL_RUNTIME_TEAM_METADATA_KEY`.

- [ ] **Step 3: Implementar en `configuration.py`**

Constante junto a `RUNTIME_TEAM_DISCARDED_METADATA_KEY`:

```python
GLOBAL_RUNTIME_TEAM_METADATA_KEY = "globalRuntimeTeam"
"""Snapshot del equipo de IA global sellado en el run cuando el hilo no tiene equipo propio; nunca del cliente."""
```

Funciones nuevas (después de `role_allowlist`/`role_model_pins`):

```python
def global_team_of(request_meta: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Snapshot ``globalRuntimeTeam`` sellado en el run, validado; ``None`` si falta o está malformado."""
    raw = (request_meta or {}).get(GLOBAL_RUNTIME_TEAM_METADATA_KEY)
    if not isinstance(raw, dict) or not isinstance(raw.get("roleRuntimeOrder"), dict):
        return None
    orders = {
        str(role): _clean_ids(order)
        for role, order in raw["roleRuntimeOrder"].items()
        if isinstance(order, list)
    }
    sources = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    return {
        "roleRuntimeOrder": orders,
        "source": {str(role): str(value) for role, value in sources.items()},
        "allowedRuntimes": _clean_ids(raw.get("allowedRuntimes") or []),
    }


def global_role_order(request_meta: Mapping[str, Any] | None, team_role: str | None) -> list[str]:
    """Orden de proveedores del rol en el equipo global: asignado primero, luego fallbacks.

    Un rol sin asignación propia (``None``: aido_lead, qa_engineer…) sigue el orden del PO. Un rol
    del equipo sin candidatos (opcional vacío) puede usar cualquier runtime del equipo, como en el
    equipo por hilo. Vacío sin snapshot global.
    """
    team = global_team_of(request_meta)
    if team is None:
        return []
    orders = team["roleRuntimeOrder"]
    order = orders.get(team_role) if team_role else None
    if order:
        return list(order)
    if team_role and team_role in orders:
        return list(team["allowedRuntimes"])
    return list(orders.get("product_owner") or team["allowedRuntimes"])


def global_role_is_explicit(request_meta: Mapping[str, Any] | None, team_role: str | None) -> bool:
    """Verdadero si el operador configuró el orden de ese rol (general o proyecto), no el automático."""
    team = global_team_of(request_meta)
    if team is None or not team_role:
        return False
    return team["source"].get(team_role) in {"project", "general"}


def role_routing_preferences(
    request_meta: Mapping[str, Any] | None, team_role: str | None
) -> tuple[list[str], list[dict[str, str]]]:
    """Orden del rol como preferencia de ruteo: ids y comodines ``{provider, model: ""}`` (tier 1).

    Solo aplica con equipo global y sin equipo por hilo (con equipo por hilo la allowlist es un único
    proveedor y los pins de la política del rol eligen el modelo, como hasta ahora).
    """
    if runtime_team_of(request_meta) is not None:
        return [], []
    order = global_role_order(request_meta, team_role)
    return order, [{"provider": provider_id, "model": ""} for provider_id in order]
```

Modificar `role_allowlist`: reemplazar el bloque

```python
    team = runtime_team_of(request_meta)
    if team is None:
        return [product_owner_provider_id] if product_owner_provider_id else None
```

por

```python
    team = runtime_team_of(request_meta)
    if team is None:
        # Con equipo global, el rol usa su orden (asignado + fallbacks). Sin snapshot (run sellado
        # antes del equipo global) se conserva la herencia del proveedor del PO.
        if global_team_of(request_meta) is not None:
            return global_role_order(request_meta, team_role) or None
        return [product_owner_provider_id] if product_owner_provider_id else None
```

y actualizar el docstring de `role_allowlist`: la frase "Sin equipo (hilo creado por API…) cada rol hereda el proveedor donde corrió el PO" pasa a describir que eso solo ocurre sin snapshot global.

Modificar `seal_thread_runtime_team`: al inicio agregar `stamped.pop(GLOBAL_RUNTIME_TEAM_METADATA_KEY, None)` junto a los otros dos `pop`, y reemplazar

```python
    if effective is None:
        return stamped
```

por

```python
    if effective is None:
        from .global_team import resolve_global_team

        stamped[GLOBAL_RUNTIME_TEAM_METADATA_KEY] = resolve_global_team(connection, project_id=project_id).sealed()
        return stamped
```

(import diferido: `global_team` importa `facts` y `roles`, y `configuration` ya los importa; el diferido evita un ciclo si `global_team` llega a importar `configuration`).

En `local_control_center/threads/coordinator.py`, agregar `GLOBAL_RUNTIME_TEAM_METADATA_KEY` al import de `runtime_team.configuration` y a `_PUBLIC_MESSAGE_PROTECTED_PRODUCT_LOOP_METADATA_KEYS` (junto a `RUNTIME_TEAM_METADATA_KEY`).

Docstring del módulo `configuration.py`: agregar un párrafo: "Sin equipo en el hilo, el sellado deja `globalRuntimeTeam` (snapshot del equipo de IA global, `global_team.py`) y `role_allowlist` devuelve el orden del rol; el equipo del hilo tiene prioridad."

- [ ] **Step 4: Correr en verde**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_runtime_team_configuration.py','tests_py/test_thread_run_configuration.py','tests_py/test_runtime_team_thread_api.py','tests_py/test_global_team.py']))"`
Expected: PASS. Si `test_threads_without_a_team_inherit_the_product_owners_runtime` (existente) sigue pasando es correcto: sin snapshot global se conserva la herencia.

- [ ] **Step 5: Commit**

```bash
uv run --extra dev ruff format local_control_center/runtime_team/configuration.py local_control_center/threads/coordinator.py tests_py/test_runtime_team_configuration.py && uv run --extra dev ruff check local_control_center/runtime_team/configuration.py local_control_center/threads/coordinator.py tests_py/test_runtime_team_configuration.py
git add local_control_center/runtime_team/configuration.py local_control_center/threads/coordinator.py tests_py/test_runtime_team_configuration.py tests_py/test_thread_run_configuration.py
git commit -m "Feature (RuntimeTeam): un hilo sin equipo propio sella el equipo global y cada rol rutea por su orden" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Coordinator — el orden del rol manda en la selección y en el failover

**Files:**
- Modify: `local_control_center/product_loop/coordinator.py` (`_team_resource_request` ~3350-3395, ampliación ~3262-3300, `_product_owner_resource_selection` ~3705-3760, `_failover_replacement` ~5440-5470, import ~80)
- Test: `tests_py/test_runtime_team_loop_enforcement.py` (ampliar)

**Interfaces:**
- Consumes: `role_routing_preferences`, `global_role_is_explicit`, `global_team_of` (Task 3).
- Cambia el comportamiento: con snapshot global, `AIResourceRequest.preferred_provider_ids` = orden del rol y `preferred_resources` = comodines del orden (los pins de la política del rol quedan detrás); la ampliación a "todo el catálogo" cuando el rol queda sin candidatos solo ocurre para roles automáticos/heredados, nunca para un orden explícito del operador.

- [ ] **Step 1: Tests que fallan**

Agregar a `tests_py/test_runtime_team_loop_enforcement.py` (reusa `coordinator`, `_team_request`, `_capture_selection`, `_fake_select_resource`, `TEAM`, `SCHEDULE` del archivo):

```python
GLOBAL = {
    "globalRuntimeTeam": {
        "roleRuntimeOrder": {
            "product_owner": ["codex_cli", "nvidia_nim"],
            "developer": ["nvidia_nim", "codex_cli"],
            "architect": [],
            "security": ["nvidia_nim"],
            "technical_lead": ["codex_cli", "nvidia_nim"],
            "researcher": ["codex_cli", "nvidia_nim"],
        },
        "roleRuntimes": {"product_owner": "codex_cli", "developer": "nvidia_nim", "architect": None, "security": "nvidia_nim", "technical_lead": "codex_cli", "researcher": "codex_cli"},
        "source": {"product_owner": "general", "developer": "project", "architect": "automatic", "security": "automatic", "technical_lead": "inherited", "researcher": "inherited"},
        "allowedRuntimes": ["codex_cli", "nvidia_nim"],
    }
}


def test_the_global_team_order_is_the_allowlist_and_the_routing_preference(coordinator):
    build = {"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}
    request = _team_request(coordinator, build, GLOBAL, product_owner_provider_id="codex_cli")
    assert request.allowed_provider_ids == ["nvidia_nim", "codex_cli"]
    assert request.preferred_provider_ids[:2] == ["nvidia_nim", "codex_cli"]
    assert request.preferred_resources[:2] == [
        {"provider": "nvidia_nim", "model": ""},
        {"provider": "codex_cli", "model": ""},
    ]
    # Con equipo por hilo nada cambia: allowlist de un proveedor y pins de la política del rol.
    thread_request = _team_request(coordinator, build, TEAM)
    assert thread_request.allowed_provider_ids == ["codex_cli"]
    assert all(item.get("model") != "" or item.get("provider") != "codex_cli" for item in thread_request.preferred_resources[:0])


def test_an_explicit_role_order_without_candidates_is_not_widened_to_the_whole_catalog(coordinator, monkeypatch):
    calls, select = _fake_select_resource(
        {("nvidia_nim", "codex_cli"): {"selected": None, "candidates": []}, None: {"selected": {"providerId": "gemini", "model": "g"}, "candidates": [{"providerId": "gemini"}]}}
    )
    monkeypatch.setattr(AIResourceManager, "select_resource", select)
    schedule = {**SCHEDULE, "roles": [{"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}]}
    roles, _blockers = coordinator._team_resource_selection(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=GLOBAL,
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
        product_owner_selected_resource={"providerId": "codex_cli", "model": "gpt-5.5"},
    )
    assert [tuple(call.allowed_provider_ids or []) for call in calls] == [("nvidia_nim", "codex_cli")]
    assert roles[0].get("resourceDecision", {}).get("selected") is None


def test_an_automatic_role_without_candidates_still_widens(coordinator, monkeypatch):
    automatic = {"globalRuntimeTeam": {**GLOBAL["globalRuntimeTeam"], "source": {**GLOBAL["globalRuntimeTeam"]["source"], "developer": "automatic"}}}
    calls, select = _fake_select_resource(
        {("nvidia_nim", "codex_cli"): {"selected": None, "candidates": []}, None: {"selected": {"providerId": "gemini", "model": "g"}, "candidates": [{"providerId": "gemini"}]}}
    )
    monkeypatch.setattr(AIResourceManager, "select_resource", select)
    schedule = {**SCHEDULE, "roles": [{"role": "backend_engineer", "kind": "build", "capabilities": ["code_edit"]}]}
    coordinator._team_resource_selection(
        project_id="project-team",
        loop_id="loop-1",
        request_meta=automatic,
        team_schedule=schedule,
        agent_tasks=[{"id": "task-1", "role": "backend_engineer"}],
        product_owner_selected_resource={"providerId": "codex_cli", "model": "gpt-5.5"},
    )
    assert [tuple(call.allowed_provider_ids or []) for call in calls] == [("nvidia_nim", "codex_cli"), ()]
```

Antes de escribir estos tests, leer la firma real de `_team_resource_selection` (`grep -n "def _team_resource_selection" -A 14 local_control_center/product_loop/coordinator.py`) y ajustar los argumentos con nombre a los reales (el bloque de ampliación de la línea ~3262 está dentro de esa función; sus parámetros incluyen `project_id`, `loop_id`, `request_meta`, `team_schedule`, `agent_tasks`, `product_owner_selected_resource`, `runtime_risk_review_id`, `thread_id`). Si la función devuelve otra forma que `(roles, blockers)`, adaptar las aserciones al valor real; la aserción esencial es la lista de allowlists que vio `select_resource`.

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_runtime_team_loop_enforcement.py']))"`
Expected: FAIL (`preferred_provider_ids` no empieza por el orden del rol; la segunda llamada ampliada aparece para el rol explícito).

- [ ] **Step 3: Implementar**

Import en `coordinator.py` (bloque `from local_control_center.runtime_team.configuration import (...)`): agregar `global_role_is_explicit`, `role_routing_preferences`.

En `_team_resource_request`, antes de construir `AIResourceRequest`:

```python
        order, order_wildcards = role_routing_preferences(request_meta, team_role)
        preferred_provider_ids = [
            *order,
            *(provider_id for provider_id in policy["preferredProviderIds"] if provider_id not in order),
        ]
        # El orden del equipo global va delante de los pins de la política del rol: los pins solo
        # eligen el modelo dentro del proveedor que el operador puso primero.
        preferred_resources = [*order_wildcards, *policy["preferredResources"]]
```

y en el constructor reemplazar `preferred_provider_ids=policy["preferredProviderIds"]` por `preferred_provider_ids=preferred_provider_ids` y `preferred_resources=policy["preferredResources"]` por `preferred_resources=preferred_resources`.

En la ampliación (~3262), la condición

```python
            if (
                runtime_risk_review_id is None
                and not thread_has_team
                and po_provider_id
                and not decision.get("candidates")
            ):
```

pasa a

```python
            if (
                runtime_risk_review_id is None
                and not thread_has_team
                and po_provider_id
                and not decision.get("candidates")
                # Un orden que el operador escribió para el rol no se amplía a todo el catálogo:
                # el rol queda sin candidatos y el blocker de asignación lo dice.
                and not global_role_is_explicit(request_meta, team_role_for(role, kind=str(role_plan.get("kind") or ""), capabilities=role_plan.get("capabilities") or []))
            ):
```

En `_failover_replacement` (rama `else`, la `AIResourceRequest` de ~5440): calcular `order, order_wildcards = role_routing_preferences(getattr(run, "request_meta", None), team_role_for(role))` antes del constructor y usar `preferred_provider_ids=[*order, *(p for p in policy["preferredProviderIds"] if p not in order)]` y `preferred_resources=[*order_wildcards, *policy["preferredResources"]]`.

En `_product_owner_resource_selection` (~3705-3735): después de `allowed_provider_ids = restrict_to_allowlist(...)`, calcular `po_order, po_wildcards = role_routing_preferences(request_meta, "product_owner")` y:
- en el bucle que arma `preferred_provider_ids`, iterar `[*po_order, *self._persisted_runtime_order(), *resource_policy["preferredProviderIds"], *ordered_contract_providers]`;
- en `preferred_resources`, anteponer `*po_wildcards` a las entradas del orden persistido.

- [ ] **Step 4: Correr en verde (unitarios + coordinador)**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_runtime_team_loop_enforcement.py','tests_py/test_runtime_team_execution_gate.py','tests_py/test_product_loop_coordinator.py','tests_py/test_product_loop_story_execution.py']))"`
Expected: PASS. `test_product_loop_coordinator.py` tarda ~8 min. Si un test existente falla porque ahora el run sella `globalRuntimeTeam` (p. ej. comparaciones exactas de `requestMeta`), ajustar la aserción a `assert "globalRuntimeTeam" in meta` en vez de igualdad exacta; si falla por ruteo (un rol elige otro proveedor), analizar: el equipo automático prioriza CLI y luego id, y ese es el nuevo comportamiento esperado — documentar en el commit.

- [ ] **Step 5: Commit**

```bash
uv run --extra dev ruff format local_control_center/product_loop/coordinator.py tests_py/test_runtime_team_loop_enforcement.py && uv run --extra dev ruff check local_control_center/product_loop/coordinator.py tests_py/test_runtime_team_loop_enforcement.py
git add local_control_center/product_loop/coordinator.py tests_py/test_runtime_team_loop_enforcement.py
git commit -m "Feature (ProductLoop): el orden del rol del equipo global manda en la seleccion y el failover recorre sus fallbacks" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: `GET /api/v1/runtime/team` + `enabled` en el estado de proveedores + cliente generado

**Files:**
- Modify: `local_control_center/runtime_team/contracts.py` (modelos nuevos)
- Modify: `local_control_center/agents/api.py` (endpoint, imports línea ~21-22)
- Modify: `local_control_center/agents/contracts.py:404-445` (`RuntimeProviderStatus.enabled`)
- Modify: `local_control_center/agents/runtime_status.py:1143-1150` (`runtime_provider_status` enriquece `enabled`)
- Modify: `local-control-center/web/src/api/generated/openapi.ts` (regenerado) y `local-control-center/web/src/api/client.ts` (`getRuntimeTeam`)
- Test: `tests_py/test_runtime_team_candidates_api.py` (ampliar)

**Interfaces:**
- Produces: `RuntimeTeamResponse` (`roles: list[GlobalTeamRoleRecord]`, `allowedRuntimes`, `activeProviders`, `candidates: list[RuntimeTeamCandidateRecord]`); `RuntimeProviderStatus.enabled: bool`; cliente `getRuntimeTeam(projectId: string | null, signal?) → RuntimeTeamResponse`.

- [ ] **Step 1: Test que falla**

Agregar a `tests_py/test_runtime_team_candidates_api.py`:

```python
def test_runtime_team_reports_the_global_assignment_sources_and_active_providers(tmp_path: Path) -> None:
    runtime, client = _client(tmp_path)
    try:
        project_id = _prepare(runtime, tmp_path)
        SettingsRepository(runtime.connection).set_value("team.role.developer", "project", project_id, ["codex_cli", "ghost"])
        response = client.get("/api/v1/runtime/team", params={"projectId": project_id})
        assert response.status_code == 200, response.text
        body = response.json()
        roles = {item["role"]: item for item in body["roles"]}
        assert roles["developer"]["source"] == "project"
        assert roles["developer"]["effective"] == ["codex_cli"]
        assert roles["developer"]["assigned"] == "codex_cli"
        assert roles["developer"]["invalid"] == ["ghost"]
        assert roles["developer"]["required"] is True
        assert roles["product_owner"]["source"] == "automatic"
        assert roles["technical_lead"]["source"] == "inherited"
        assert roles["architect"]["required"] is False
        assert body["activeProviders"] >= 2
        assert {item["providerId"] for item in body["candidates"]} >= {"codex_cli", "ollama"}

        providers = client.get("/api/v1/runtime/providers", params={"projectId": project_id}).json()["providers"]
        by_id = {item["id"]: item for item in providers}
        assert by_id["codex_cli"]["enabled"] is True
        ProviderAccountStore(runtime.connection).patch_provider_account("codex_cli", {"enabled": False})
        providers = client.get("/api/v1/runtime/providers", params={"projectId": project_id}).json()["providers"]
        assert {item["id"]: item for item in providers}["codex_cli"]["enabled"] is False
    finally:
        runtime.close()
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_runtime_team_candidates_api.py']))"`
Expected: FAIL con 404 en `/api/v1/runtime/team`.

- [ ] **Step 3: Contratos**

En `local_control_center/runtime_team/contracts.py`, después de `RuntimeTeamCandidatesResponse`:

```python
GlobalTeamRole = Literal["product_owner", "developer", "architect", "security", "technical_lead", "researcher"]
GlobalTeamSource = Literal["project", "general", "automatic", "inherited"]


class GlobalTeamRoleRecord(BaseModel):
    """Un rol del equipo de IA global: orden configurado, orden efectivo, asignado y procedencia."""

    role: GlobalTeamRole
    required: bool
    configured: list[str] = Field(default_factory=list)
    effective: list[str] = Field(default_factory=list)
    assigned: str | None = None
    source: GlobalTeamSource
    invalid: list[str] = Field(default_factory=list)
    candidates: list[str] = Field(default_factory=list)


class RuntimeTeamResponse(BaseModel):
    """Equipo de IA global efectivo de un proyecto (o general) con los candidatos activos."""

    roles: list[GlobalTeamRoleRecord]
    allowed_runtimes: list[str] = Field(alias="allowedRuntimes")
    active_providers: int = Field(alias="activeProviders")
    candidates: list[RuntimeTeamCandidateRecord]
```

En `local_control_center/agents/contracts.py`, en `RuntimeProviderStatus`, después de `executable: bool`:

```python
    #: Switch del operador (`provider_accounts.enabled`): apagado ⇒ ningún hilo lo usa, aunque esté sano.
    enabled: bool = False
```

- [ ] **Step 4: Endpoint y enriquecimiento**

`local_control_center/agents/api.py`: en el import de línea ~22 agregar `RuntimeTeamResponse`; agregar `from local_control_center.runtime_team.global_team import describe_global_team`; y después del endpoint `list_runtime_team_candidates`:

```python
    @router.get("/api/v1/runtime/team", response_model=RuntimeTeamResponse)
    def get_runtime_team(projectId: str | None = None) -> dict[str, Any]:
        """Equipo de IA global efectivo (proyecto > general > automático) y candidatos activos por rol."""
        return describe_global_team(platform.connection, project_id=projectId)
```

`local_control_center/agents/runtime_status.py`, en `runtime_provider_status`, justo después de `providers = self.list_provider_statuses(project_id=project_id)`:

```python
        enabled_accounts = {
            str(account["providerId"]): bool(account.get("enabled"))
            for account in self.accounts.list_provider_accounts()
        }
        for provider in providers:
            provider["enabled"] = enabled_accounts.get(str(provider["id"]), False)
```

- [ ] **Step 5: Regenerar el cliente y agregar `getRuntimeTeam`**

Run: `corepack pnpm@10.24.0 run openapi:generate` → `git diff --stat local-control-center/web/src/api/generated/openapi.ts` muestra `RuntimeTeamResponse`, `GlobalTeamRoleRecord` y `enabled` en `RuntimeProviderStatus`.

En `local-control-center/web/src/api/client.ts`, agregar `RuntimeTeamResponse` al `import type { … } from './generated/openapi'` (bloque de la línea ~15, donde ya está `RuntimeTeamCandidatesResponse`) y, después de `getRuntimeTeamCandidates`:

```ts
/** Effective global AI team (project > general > automatic) with the active, eligible candidates per role. */
export function getRuntimeTeam(projectId: string | null, signal?: AbortSignal) {
	return requestGeneratedOperation<'get_runtime_team_api_v1_runtime_team_get', RuntimeTeamResponse>(
		'get_runtime_team_api_v1_runtime_team_get',
		{ query: projectId ? { projectId } : {}, signal },
	);
}
```

Exportar el tipo desde `local-control-center/web/src/api/types.ts` siguiendo el patrón `export type RuntimeProviders = RuntimeProvidersResponse;`: agregar `export type RuntimeTeam = RuntimeTeamResponse;` y `export type RuntimeTeamRole = GlobalTeamRoleRecord;` (importando ambos del generado como hace el archivo con los demás).

- [ ] **Step 6: Verde y gates**

Run: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_runtime_team_candidates_api.py','tests_py/test_response_model_drift.py']))"` (si el gate de drift tiene otro nombre: `ls tests_py | grep -i "drift\|openapi"`); luego `corepack pnpm@10.24.0 run typecheck:web`.
Expected: PASS / sin errores.

- [ ] **Step 7: Commit**

```bash
uv run --extra dev ruff format local_control_center/runtime_team/contracts.py local_control_center/agents/api.py local_control_center/agents/contracts.py local_control_center/agents/runtime_status.py && uv run --extra dev ruff check local_control_center/runtime_team/contracts.py local_control_center/agents/api.py local_control_center/agents/contracts.py local_control_center/agents/runtime_status.py
git add local_control_center/runtime_team/contracts.py local_control_center/agents/api.py local_control_center/agents/contracts.py local_control_center/agents/runtime_status.py local-control-center/web/src/api/generated/openapi.ts local-control-center/web/src/api/client.ts local-control-center/web/src/api/types.ts tests_py/test_runtime_team_candidates_api.py
git commit -m "Feature (API): GET /api/v1/runtime/team expone el equipo de IA global y el estado de runtime dice si el proveedor esta activo" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Switch por proveedor en *Providers & CLI* y "proveedores activos" en la barra de estado

**Files:**
- Modify: `local-control-center/web/src/features/runtime-setup/RuntimeSetupPanel.tsx` (`ProviderCard` ~488-620 y el `.map` de tarjetas ~430-445; handler nuevo junto a `runProviderTask` ~240)
- Modify: `local-control-center/web/src/app/shellStatus.ts`, `local-control-center/web/src/app/StatusBar.tsx:92-105`
- Modify: `local_control_center/i18n/default_catalog.json`
- Test: `tests_web/settings-providers.spec.js` (ampliar)

**Interfaces:**
- `ProviderCard` recibe `onToggleEnabled: (enabled: boolean) => void` y muestra un switch (`role` implícito de `<input type="checkbox">` dentro de `label.setting-switch`, mismo markup que `SettingRow.tsx:257-275`) con `aria-label` = `t('app.providers.switch.label', 'Use {provider} in threads')` con `{provider}` reemplazado por el nombre.
- `ShellStatus.activeProviders: number` = `providers.filter((p) => p.enabled && p.policyAllowed !== false).length`.

- [ ] **Step 1: Spec Playwright que falla**

Agregar a `tests_web/settings-providers.spec.js` (usa `openSettings(page)` del archivo, que navega a `/#settings-runtime`):

```js
test('Providers & CLI: each provider card has a switch that patches enabled and the status bar counts active providers', async ({ page }) => {
	const patches = [];
	await page.route('**/api/v1/model-gateway/providers/*', async (route) => {
		if (route.request().method() !== 'PATCH') return route.continue();
		const id = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		patches.push({ id, ...route.request().postDataJSON() });
		await route.fulfill({ json: { provider: { providerId: id, enabled: route.request().postDataJSON().enabled } } });
	});
	try {
		const settings = await openSettings(page);
		const card = settings.locator('.card').filter({ hasText: 'Gemini' }).first();
		await expect(card).toBeVisible({ timeout: 30_000 });
		const toggle = card.getByRole('checkbox', { name: 'Use Gemini in threads' });
		await expect(toggle).toBeVisible();
		const wasChecked = await toggle.isChecked();
		await toggle.click();
		await expect.poll(() => patches).toEqual([{ id: 'gemini', enabled: !wasChecked }]);
		await expect(page.getByText(/\d+ active providers/)).toBeVisible();
	} finally {
		await page.unrouteAll({ behavior: 'ignoreErrors' });
	}
});
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `PLAYWRIGHT_DASHBOARD_PORT=4995 node scripts/run-web-tests.mjs tests_web/settings-providers.spec.js --grep "each provider card has a switch"`
Expected: FAIL (no existe el checkbox "Use Gemini in threads").

- [ ] **Step 3: Implementar el switch**

En `RuntimeSetupPanel.tsx`:
1. Importar `patchModelGatewayProvider` desde `'../../api/client'` (junto a `healthCheckModelGatewayProvider`).
2. Handler junto a `runProviderTask`:

```ts
	const toggleProvider = async (providerId: string, enabled: boolean) => {
		if (busyAction) return;
		if (!requireToken()) return;
		setBusyAction(`${providerId}:toggle`);
		try {
			await patchModelGatewayProvider(token, providerId, { enabled });
			await Promise.all([onRefresh(), loadGateway()]);
			notify({
				title: enabled
					? t('app.providers.action.enabled', 'Provider enabled for threads')
					: t('app.providers.action.disabled', 'Provider disabled for threads'),
				tone: 'ok',
			});
		} catch (error) {
			const message = error instanceof Error ? error.message : String(error);
			notify({
				title: t('app.providers.action.toggleFailed', 'Could not update the provider'),
				body: redactVisibleSecret(message, 'provider update failed'),
				tone: 'danger',
			});
		} finally {
			setBusyAction(null);
		}
	};
```

3. En el `.map` de tarjetas, pasar `onToggleEnabled={(enabled) => void toggleProvider(provider.id, enabled)}`.
4. En `ProviderCard`: agregar la prop `onToggleEnabled: (enabled: boolean) => void;` y, en el `card-header` después del `<Badge>` de estado, cuando `setup` no es null:

```tsx
				{setup ? (
					<label className="setting-switch provider-switch" data-disabled={busy || busyAction === `${provider.id}:toggle` ? 'true' : undefined}>
						<input
							type="checkbox"
							checked={setup.enabled}
							disabled={busyAction !== null}
							aria-label={t('app.providers.switch.label', 'Use {provider} in threads').replace('{provider}', provider.displayName)}
							onChange={(event) => onToggleEnabled(event.target.checked)}
						/>
						<span className="setting-switch-track" aria-hidden="true">
							<span className="setting-switch-thumb" />
						</span>
						<span className="setting-switch-state" aria-hidden="true">
							{setup.enabled ? t('app.providers.switch.on', 'Active') : t('app.providers.switch.off', 'Inactive')}
						</span>
					</label>
				) : null}
```

Las tarjetas de endpoints locales (`LocalEndpointsPanel`) ya tienen su checkbox de `enabled`: no se duplica.

- [ ] **Step 4: Barra de estado**

`shellStatus.ts`: agregar `activeProviders: number;` al tipo y en `deriveShellStatus`:

```ts
		activeProviders:
			runtimeProviders?.providers.filter((provider) => provider.enabled && provider.policyAllowed !== false).length ?? 0,
```

`StatusBar.tsx`: después del `<span className="status-bar-item">` de runtimes ejecutables, agregar:

```tsx
			<span className="status-bar-item">
				<StatusDot tone={status.activeProviders ? 'ok' : 'warn'} />
				<span className="tnum">{status.activeProviders}</span>{' '}
				{t('app.statusBar.activeProviders', 'active providers')}
			</span>
```

- [ ] **Step 5: i18n**

Registrar (mismo script que en Task 1, con estas entradas):

```
app.providers.switch.label      en "Use {provider} in threads"           es "Usar {provider} en los hilos"
app.providers.switch.on         en "Active"                              es "Activo"
app.providers.switch.off        en "Inactive"                            es "Inactivo"
app.providers.action.enabled    en "Provider enabled for threads"        es "Proveedor activado para los hilos"
app.providers.action.disabled   en "Provider disabled for threads"       es "Proveedor desactivado para los hilos"
app.providers.action.toggleFailed en "Could not update the provider"     es "No se pudo actualizar el proveedor"
app.statusBar.activeProviders   en "active providers"                    es "proveedores activos"
```

- [ ] **Step 6: Verde y gates**

Run: `corepack pnpm@10.24.0 run typecheck:web && corepack pnpm@10.24.0 run check:web && PLAYWRIGHT_DASHBOARD_PORT=4995 node scripts/run-web-tests.mjs tests_web/settings-providers.spec.js`
Y el gate i18n: `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py']))"`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add local-control-center/web/src/features/runtime-setup/RuntimeSetupPanel.tsx local-control-center/web/src/app/shellStatus.ts local-control-center/web/src/app/StatusBar.tsx local_control_center/i18n/default_catalog.json tests_web/settings-providers.spec.js
git commit -m "Feature (Web): switch por proveedor en Providers & CLI y conteo de proveedores activos en la barra de estado" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: Panel "Equipo de IA" en Settings (general y proyecto)

**Files:**
- Create: `local-control-center/web/src/features/settings/AiTeamPanel.tsx`
- Create: `local-control-center/web/src/features/runtime-team/useRuntimeTeam.ts`
- Modify: `local-control-center/web/src/features/settings/sections.tsx` (sección general `ai-team`, sección proyecto `team`, `SECTION_TO_SETTING_SECTION`)
- Modify: `local_control_center/i18n/default_catalog.json`
- Test: `tests_web/settings-ai-team.spec.js` (nuevo)

**Interfaces:**
- `useRuntimeTeam(projectId: string | null, enabled: boolean) → { data: RuntimeTeam | null; failed: boolean; reload: () => void }` (mismo patrón que `useRuntimeTeamCandidates.ts`).
- `AiTeamPanel({ ctx }: { ctx: SectionContext })`: lee `ctx.scope`/`ctx.scopeId`; escribe con `ctx.setValue('team.role.<rol>', ctx.scope, ctx.scopeId, ids)`; "Automático" = `ctx.clearValue(...)`.

- [ ] **Step 1: Spec Playwright que falla**

```js
// tests_web/settings-ai-team.spec.js
/**
 * Global AI team settings: per role, an ordered list of active providers (first = assigned, rest =
 * fallback) editable at general and project scope; "Automatic" clears the override. The team
 * endpoint and the settings writes are mocked; the Settings modal is the real one.
 * @author Rodrigo Mason
 */
import { expect, test } from '@playwright/test';

test.use({ viewport: { width: 1280, height: 800 } });

test.afterEach(async ({ page }) => {
	await page.unrouteAll({ behavior: 'ignoreErrors' });
});

const CANDIDATE = (providerId, label, kind, eligibleRoles) => ({
	providerId,
	label,
	kind,
	validation: { status: 'validated', checkedAt: '2026-09-26T10:00:00+00:00', latencyMs: 300, model: 'm', reason: null },
	eligibleRoles,
	loadedModels: [],
});

function teamBody(developerOrder, source) {
	const role = (name, required, effective, roleSource, candidates) => ({
		role: name,
		required,
		configured: roleSource === 'automatic' || roleSource === 'inherited' ? [] : effective,
		effective,
		assigned: effective[0] ?? null,
		source: roleSource,
		invalid: [],
		candidates,
	});
	return {
		roles: [
			role('product_owner', true, ['codex_cli', 'llama_cpp'], 'automatic', ['claude_code_cli', 'codex_cli', 'llama_cpp']),
			role('developer', true, developerOrder, source, ['claude_code_cli', 'codex_cli', 'llama_cpp']),
			role('architect', false, ['claude_code_cli'], 'automatic', ['claude_code_cli']),
			role('security', false, ['llama_cpp'], 'automatic', ['llama_cpp']),
			role('technical_lead', false, ['codex_cli', 'llama_cpp'], 'inherited', ['claude_code_cli', 'codex_cli', 'llama_cpp']),
			role('researcher', false, ['codex_cli', 'llama_cpp'], 'inherited', ['claude_code_cli', 'codex_cli', 'llama_cpp']),
		],
		allowedRuntimes: ['claude_code_cli', 'codex_cli', 'llama_cpp'],
		activeProviders: 3,
		candidates: [
			CANDIDATE('claude_code_cli', 'Claude Code CLI', 'cli', ['product_owner', 'developer', 'architect']),
			CANDIDATE('codex_cli', 'Codex CLI', 'cli', ['product_owner', 'developer']),
			CANDIDATE('llama_cpp', 'llama.cpp', 'local', ['product_owner', 'developer', 'security']),
		],
	};
}

async function openGeneralAiTeam(page, state) {
	await page.route('**/api/v1/runtime/team**', (route) =>
		route.fulfill({ json: teamBody(state.developer, state.source) }),
	);
	await page.route('**/api/v1/settings/team.role.*', async (route) => {
		const key = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-1));
		if (route.request().method() === 'PUT') {
			const body = route.request().postDataJSON();
			state.writes.push({ key, scope: body.scope, value: body.value });
			state.developer = body.value;
			state.source = 'general';
		} else if (route.request().method() === 'DELETE') {
			state.writes.push({ key, scope: 'general', value: null });
			state.developer = ['claude_code_cli', 'codex_cli', 'llama_cpp'];
			state.source = 'automatic';
		}
		await route.fulfill({ status: 204, body: '' });
	});
	await page.goto('/#settings');
	await expect(page.getByRole('heading', { name: 'AIDO Control Center' })).toBeVisible({ timeout: 30_000 });
	await expect(page.getByText('Loading control plane')).toBeHidden({ timeout: 30_000 });
	const settings = page.getByRole('dialog', { name: 'Settings' });
	await expect(settings).toBeVisible();
	await settings.locator('nav[aria-label="Settings sections"]').getByRole('button', { name: 'AI team', exact: true }).click();
	return settings.getByRole('region', { name: 'AI team' });
}

test('the AI team panel lists each role with its assigned provider and source', async ({ page }) => {
	const state = { developer: ['claude_code_cli', 'codex_cli', 'llama_cpp'], source: 'automatic', writes: [] };
	const panel = await openGeneralAiTeam(page, state);
	const developer = panel.getByRole('group', { name: 'Developer' });
	await expect(developer).toContainText('Claude Code CLI');
	await expect(developer.getByText('automatic', { exact: true })).toBeVisible();
	await expect(panel.getByRole('group', { name: 'Technical Lead' }).getByText('inherits the Product Owner', { exact: true })).toBeVisible();
});

test('adding a provider to a role writes the ordered list and "Automatic" clears it', async ({ page }) => {
	const state = { developer: ['claude_code_cli', 'codex_cli', 'llama_cpp'], source: 'automatic', writes: [] };
	const panel = await openGeneralAiTeam(page, state);
	const developer = panel.getByRole('group', { name: 'Developer' });
	await developer.getByRole('combobox', { name: 'Add provider' }).selectOption('codex_cli');
	await developer.getByRole('button', { name: 'Add', exact: true }).click();
	await expect.poll(() => state.writes).toEqual([{ key: 'team.role.developer', scope: 'general', value: ['codex_cli'] }]);
	await expect(developer.getByText('general', { exact: true })).toBeVisible();
	await developer.getByRole('button', { name: 'Automatic', exact: true }).click();
	await expect.poll(() => state.writes.length).toBe(2);
	expect(state.writes[1]).toEqual({ key: 'team.role.developer', scope: 'general', value: null });
});
```

- [ ] **Step 2: Correr y ver el fallo**

Run: `PLAYWRIGHT_DASHBOARD_PORT=4996 node scripts/run-web-tests.mjs tests_web/settings-ai-team.spec.js`
Expected: FAIL (no existe la sección "AI team").

- [ ] **Step 3: Hook `useRuntimeTeam.ts`**

```ts
// local-control-center/web/src/features/runtime-team/useRuntimeTeam.ts
/**
 * Loads the effective global AI team of a project (or the general one with `projectId === null`).
 * Only the latest request writes state; `reload` re-queries after a settings write.
 * @author Rodrigo Mason
 */
import { useCallback, useEffect, useRef, useState } from 'react';

import { getRuntimeTeam } from '../../api/client';
import type { RuntimeTeam } from '../../api/types';

export function useRuntimeTeam(projectId: string | null, enabled: boolean) {
	const [data, setData] = useState<RuntimeTeam | null>(null);
	const [failed, setFailed] = useState(false);
	const latestRequest = useRef<AbortController | null>(null);

	const load = useCallback(async () => {
		latestRequest.current?.abort();
		const controller = new AbortController();
		latestRequest.current = controller;
		try {
			const response = await getRuntimeTeam(projectId, controller.signal);
			if (!controller.signal.aborted) {
				setData(response);
				setFailed(false);
			}
		} catch {
			if (!controller.signal.aborted) setFailed(true);
		}
	}, [projectId]);

	useEffect(() => {
		if (!enabled) return undefined;
		void load();
		return () => latestRequest.current?.abort();
	}, [enabled, load]);

	const reload = useCallback(() => {
		void load();
	}, [load]);

	return { data, failed, reload };
}
```

- [ ] **Step 4: Panel `AiTeamPanel.tsx`**

```tsx
// local-control-center/web/src/features/settings/AiTeamPanel.tsx
/**
 * Global AI team: per role, the ordered providers a thread uses (first = assigned, rest = fallback).
 * The backend resolves eligibility, the automatic split and the effective order; this panel only
 * edits the `team.role.<role>` string lists of the current scope (general or project) and shows
 * where the effective order comes from. "Automatic" clears the scope's override.
 * @author Rodrigo Mason
 */
import { useState } from 'react';

import type { RuntimeTeamRole } from '../../api/types';
import { Button, ErrorState, Skeleton, StatusChip } from '../../components/ui';
import { useI18n } from '../../i18n/I18nProvider';
import { useRuntimeTeam } from '../runtime-team/useRuntimeTeam';
import type { SectionContext } from './sections';

const ROLE_LABEL: Record<string, { key: string; fallback: string }> = {
	product_owner: { key: 'app.aiTeam.role.product_owner', fallback: 'Product Owner' },
	developer: { key: 'app.aiTeam.role.developer', fallback: 'Developer' },
	architect: { key: 'app.aiTeam.role.architect', fallback: 'Architect' },
	security: { key: 'app.aiTeam.role.security', fallback: 'Security' },
	technical_lead: { key: 'app.aiTeam.role.technical_lead', fallback: 'Technical Lead' },
	researcher: { key: 'app.aiTeam.role.researcher', fallback: 'Researcher' },
};

const SOURCE_LABEL: Record<string, { key: string; fallback: string }> = {
	project: { key: 'app.aiTeam.source.project', fallback: 'project' },
	general: { key: 'app.aiTeam.source.general', fallback: 'general' },
	automatic: { key: 'app.aiTeam.source.automatic', fallback: 'automatic' },
	inherited: { key: 'app.aiTeam.source.inherited', fallback: 'inherits the Product Owner' },
};

export function AiTeamPanel({ ctx }: { ctx: SectionContext }) {
	const { t } = useI18n();
	const projectId = ctx.scope === 'project' ? ctx.scopeId : null;
	const { data, failed, reload } = useRuntimeTeam(projectId, true);
	const [busyRole, setBusyRole] = useState<string | null>(null);

	const write = async (role: string, ids: string[] | null) => {
		setBusyRole(role);
		try {
			if (ids === null) await ctx.clearValue(`team.role.${role}`, ctx.scope, ctx.scopeId);
			else await ctx.setValue(`team.role.${role}`, ctx.scope, ctx.scopeId, ids);
			reload();
		} finally {
			setBusyRole(null);
		}
	};

	if (failed) {
		return (
			<ErrorState
				title={t('app.aiTeam.loadFailed', 'The AI team could not be loaded')}
				action={<Button onClick={reload}>{t('app.global.retry', 'Retry')}</Button>}
			/>
		);
	}
	if (!data) return <Skeleton lines={6} />;

	const labelOf = (providerId: string) =>
		data.candidates.find((candidate) => candidate.providerId === providerId)?.label ?? providerId;

	return (
		<section className="ai-team-panel" aria-label={t('app.aiTeam.title', 'AI team')}>
			<p className="field-help">
				{t(
					'app.aiTeam.help',
					'Every thread uses this team unless it sets its own. The first provider of a role is used; the next ones are fallbacks when it is inactive or fails.',
				)}
			</p>
			{data.roles.map((role) => (
				<RoleRow
					key={role.role}
					role={role}
					scope={ctx.scope}
					busy={busyRole === role.role}
					labelOf={labelOf}
					onWrite={(ids) => void write(role.role, ids)}
				/>
			))}
		</section>
	);
}

function RoleRow({
	role,
	scope,
	busy,
	labelOf,
	onWrite,
}: {
	role: RuntimeTeamRole;
	scope: 'general' | 'project';
	busy: boolean;
	labelOf: (providerId: string) => string;
	onWrite: (ids: string[] | null) => void;
}) {
	const { t } = useI18n();
	const [pick, setPick] = useState('');
	const name = t(ROLE_LABEL[role.role]?.key ?? role.role, ROLE_LABEL[role.role]?.fallback ?? role.role);
	const source = SOURCE_LABEL[role.source] ?? SOURCE_LABEL.automatic;
	// The editable list is the operator's own order at this scope; automatic/inherited roles start empty.
	const own = role.source === scope ? role.configured : [];
	const addable = role.candidates.filter((id) => !own.includes(id));
	const commit = (ids: string[]) => onWrite(ids.length ? ids : null);
	const move = (index: number, delta: number) => {
		const next = [...own];
		const [item] = next.splice(index, 1);
		next.splice(index + delta, 0, item);
		commit(next);
	};
	return (
		<fieldset className="ai-team-role" aria-label={name} disabled={busy}>
			<legend>
				{name}
				{role.required ? <StatusChip tone="info">{t('app.aiTeam.required', 'required')}</StatusChip> : null}
				<StatusChip tone={role.source === 'automatic' || role.source === 'inherited' ? 'pending' : 'ok'}>
					{t(source.key, source.fallback)}
				</StatusChip>
			</legend>
			<ol className="ai-team-order">
				{role.effective.map((id, index) => (
					<li key={id} data-assigned={index === 0 ? 'true' : undefined}>
						<span>{labelOf(id)}</span>
						{index === 0 ? <StatusChip tone="ok">{t('app.aiTeam.assigned', 'assigned')}</StatusChip> : null}
						{own.includes(id) ? (
							<span className="inline">
								<Button variant="ghost" disabled={index === 0} onClick={() => move(own.indexOf(id), -1)} aria-label={t('app.aiTeam.moveUp', 'Move up')}>↑</Button>
								<Button variant="ghost" disabled={index === own.length - 1} onClick={() => move(own.indexOf(id), 1)} aria-label={t('app.aiTeam.moveDown', 'Move down')}>↓</Button>
								<Button variant="ghost" onClick={() => commit(own.filter((item) => item !== id))} aria-label={t('app.aiTeam.remove', 'Remove')}>×</Button>
							</span>
						) : null}
					</li>
				))}
				{role.effective.length === 0 ? (
					<li className="muted">{t('app.aiTeam.noCandidates', 'No active provider can take this role')}</li>
				) : null}
			</ol>
			{role.invalid.length ? (
				<p className="field-help" role="status">
					{t('app.aiTeam.invalid', 'Ignored (inactive or not eligible): {ids}').replace('{ids}', role.invalid.join(', '))}
				</p>
			) : null}
			<div className="inline">
				<select
					aria-label={t('app.aiTeam.addProvider', 'Add provider')}
					value={pick}
					onChange={(event) => setPick(event.target.value)}
				>
					<option value="">{t('app.aiTeam.pickProvider', 'Choose a provider…')}</option>
					{addable.map((id) => (
						<option key={id} value={id}>
							{labelOf(id)}
						</option>
					))}
				</select>
				<Button disabled={!pick} onClick={() => { commit([...own, pick]); setPick(''); }}>
					{t('app.aiTeam.add', 'Add')}
				</Button>
				<Button variant="ghost" disabled={own.length === 0} onClick={() => onWrite(null)}>
					{t('app.aiTeam.automatic', 'Automatic')}
				</Button>
			</div>
		</fieldset>
	);
}
```

Verificar contra `components/ui/Button.tsx` que `variant="ghost"` existe (`grep -n "ghost\|ButtonVariant" local-control-center/web/src/components/ui/Button.tsx`); si el nombre es otro (`secondary`, `subtle`), usar ese. `ErrorState`/`Skeleton`: revisar sus props reales en `components/ui/ErrorState.tsx` y `Skeleton.tsx` (`lines`, `title`, `action`) y ajustar.

- [ ] **Step 5: Registrar las secciones**

En `sections.tsx`:
1. `import { AiTeamPanel } from './AiTeamPanel';` y el icono `Users` ya importado de `lucide-react`.
2. En `GENERAL_SECTIONS`, después de `providers-cli`:

```tsx
	{
		id: 'ai-team',
		titleKey: 'app.settings.section.aiTeam',
		titleFallback: 'AI team',
		icon: Users,
		kind: 'display',
		render: (ctx) => <AiTeamPanel ctx={ctx} />,
	},
```

3. En `PROJECT_SECTIONS`, la sección `team` pasa a renderizar ambos paneles:

```tsx
		render: (ctx) => (
			<>
				<AiTeamPanel ctx={ctx} />
				<ProjectTeamPanel projectId={ctx.scopeId} onNavigate={ctx.closeSettings} />
			</>
		),
```

4. En `SECTION_TO_SETTING_SECTION` agregar `'ai-team': 'team',` y `team: 'team',`.

- [ ] **Step 6: i18n**

Registrar con el script de Task 1 (todas `en`/`es` distintas):

```
app.settings.section.aiTeam   "AI team" / "Equipo de IA"
app.aiTeam.title              "AI team" / "Equipo de IA"
app.aiTeam.help               "Every thread uses this team unless it sets its own. The first provider of a role is used; the next ones are fallbacks when it is inactive or fails." / "Todos los hilos usan este equipo salvo que definan el suyo. Se usa el primer proveedor de cada rol; los siguientes son respaldo si está inactivo o falla."
app.aiTeam.loadFailed         "The AI team could not be loaded" / "No se pudo cargar el equipo de IA"
app.aiTeam.required           "required" / "obligatorio"
app.aiTeam.assigned           "assigned" / "asignado"
app.aiTeam.noCandidates       "No active provider can take this role" / "Ningún proveedor activo puede cumplir este rol"
app.aiTeam.invalid            "Ignored (inactive or not eligible): {ids}" / "Ignorados (inactivos o no elegibles): {ids}"
app.aiTeam.addProvider        "Add provider" / "Agregar proveedor"
app.aiTeam.pickProvider       "Choose a provider…" / "Elige un proveedor…"
app.aiTeam.add                "Add" / "Agregar"
app.aiTeam.automatic          "Automatic" / "Automático"
app.aiTeam.moveUp             "Move up" / "Subir"
app.aiTeam.moveDown           "Move down" / "Bajar"
app.aiTeam.remove             "Remove" / "Quitar"
app.aiTeam.role.product_owner "Product Owner" / "Product Owner (PO)"
app.aiTeam.role.developer     "Developer" / "Desarrollador"
app.aiTeam.role.architect     "Architect" / "Arquitecto"
app.aiTeam.role.security      "Security" / "Seguridad"
app.aiTeam.role.technical_lead "Technical Lead" / "Líder técnico"
app.aiTeam.role.researcher    "Researcher" / "Investigador"
app.aiTeam.source.project     "project" / "proyecto"
app.aiTeam.source.general     "general" / "general (global)"
app.aiTeam.source.automatic   "automatic" / "automático"
app.aiTeam.source.inherited   "inherits the Product Owner" / "hereda del Product Owner"
```

(`app.global.retry` ya existe; verificar con `grep -c '"app.global.retry"' local_control_center/i18n/default_catalog.json`; si no, registrar "Retry"/"Reintentar".)

- [ ] **Step 7: Verde y gates**

Run: `corepack pnpm@10.24.0 run typecheck:web && corepack pnpm@10.24.0 run check:web && PLAYWRIGHT_DASHBOARD_PORT=4996 node scripts/run-web-tests.mjs tests_web/settings-ai-team.spec.js tests_web/settings-providers.spec.js`
Y `uv run python -c "import sys; sys.modules['faiss']=None; import pytest; sys.exit(pytest.main(['-q','-p','no:randomly','tests_py/test_i18n_platform.py','tests_py/test_web_rework_architecture.py','tests_py/test_settings_modal_tripwires.py']))"` (si el último no existe, `ls tests_py | grep -i settings` y correr los que anclan `sections.tsx`).
Expected: PASS. Si el test de tripwires del modal enumera las secciones, agregar `ai-team` a su lista esperada (es parte de esta feature, no un desvío).

- [ ] **Step 8: Commit**

```bash
git add local-control-center/web/src/features/settings/AiTeamPanel.tsx local-control-center/web/src/features/runtime-team/useRuntimeTeam.ts local-control-center/web/src/features/settings/sections.tsx local_control_center/i18n/default_catalog.json tests_web/settings-ai-team.spec.js
git commit -m "Feature (Web): panel Equipo de IA con el orden de proveedores por rol, en scope general y por proyecto" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: Chip "Equipo global" en el intake y equipo efectivo en el inspector

**Files:**
- Modify: `local-control-center/web/src/features/runtime-team/RuntimeTeamChip.tsx`
- Modify: `local-control-center/web/src/features/runtime-team/RuntimeTeamPanel.tsx:387-395` (texto del botón "Use automatic routing")
- Create: `local-control-center/web/src/features/shell/EffectiveTeamCard.tsx`
- Modify: `local-control-center/web/src/features/shell/ThreadInspector.tsx` (`TeamPanel` ~559-582)
- Modify: `local_control_center/i18n/default_catalog.json`
- Test: `tests_web/thread-runtime-team.spec.js:323` (actualizar el label), `tests_web/thread-inspector.spec.js` (ampliar)

**Interfaces:**
- Chip sin selección: label `t('app.runtimeTeam.chipGlobal', 'AI team · global')` y `title` con el resumen `PO: <label> · Dev: <label>` que viene de `useRuntimeTeam(projectId, !open)`.
- `EffectiveTeamCard({ projectId, threadMetadata })`: lista los roles con el proveedor efectivo; si `readRuntimeTeam(threadMetadata)` devuelve un equipo, muestra "this thread" como fuente para los roles que tiene asignados.

- [ ] **Step 1: Specs que fallan**

En `tests_web/thread-runtime-team.spec.js` cambiar la línea 323 a `await expect(page.getByRole('button', { name: 'AI team · global' })).toBeVisible();` y agregar el mock `await page.route('**/api/v1/runtime/team**', (route) => route.fulfill({ json: GLOBAL_TEAM }));` dentro de `mockRuntimeTeam` con:

```js
const GLOBAL_TEAM = {
	roles: [
		{ role: 'product_owner', required: true, configured: [], effective: ['claude_code_cli'], assigned: 'claude_code_cli', source: 'automatic', invalid: [], candidates: ['claude_code_cli'] },
		{ role: 'developer', required: true, configured: [], effective: ['claude_code_cli'], assigned: 'claude_code_cli', source: 'automatic', invalid: [], candidates: ['claude_code_cli'] },
		{ role: 'architect', required: false, configured: [], effective: [], assigned: null, source: 'automatic', invalid: [], candidates: [] },
		{ role: 'security', required: false, configured: [], effective: [], assigned: null, source: 'automatic', invalid: [], candidates: [] },
		{ role: 'technical_lead', required: false, configured: [], effective: ['claude_code_cli'], assigned: 'claude_code_cli', source: 'inherited', invalid: [], candidates: ['claude_code_cli'] },
		{ role: 'researcher', required: false, configured: [], effective: ['claude_code_cli'], assigned: 'claude_code_cli', source: 'inherited', invalid: [], candidates: ['claude_code_cli'] },
	],
	allowedRuntimes: ['claude_code_cli'],
	activeProviders: 1,
	candidates: [],
};
```

En `tests_web/thread-inspector.spec.js`, en el test que abre la pestaña Team (localizar con `grep -n "Team" tests_web/thread-inspector.spec.js`), agregar el mismo mock y la aserción `await expect(inspector.getByRole('region', { name: 'Effective AI team' })).toContainText('Developer');`.

- [ ] **Step 2: Correr y ver el fallo**

Run: `PLAYWRIGHT_DASHBOARD_PORT=4997 node scripts/run-web-tests.mjs tests_web/thread-runtime-team.spec.js tests_web/thread-inspector.spec.js`
Expected: FAIL (label viejo y región inexistente).

- [ ] **Step 3: Chip**

En `RuntimeTeamChip.tsx`: importar `useRuntimeTeam` y calcular

```ts
	const { data: globalTeam } = useRuntimeTeam(projectId, !open && selection === null);
	const roleLabel = (role: string) => {
		const item = globalTeam?.roles.find((entry) => entry.role === role);
		if (!item?.assigned) return null;
		return globalTeam?.candidates.find((c) => c.providerId === item.assigned)?.label ?? item.assigned;
	};
	const globalSummary = [
		roleLabel('product_owner') ? `PO: ${roleLabel('product_owner')}` : null,
		roleLabel('developer') ? `Dev: ${roleLabel('developer')}` : null,
	]
		.filter(Boolean)
		.join(' · ');
```

y reemplazar `t('app.runtimeTeam.chipAuto', 'AI team · automatic')` por `t('app.runtimeTeam.chipGlobal', 'AI team · global')`; en el `<button>` agregar `title={selection ? undefined : globalSummary || undefined}`.

En `RuntimeTeamPanel.tsx:395`, cambiar el fallback del botón a `t('app.runtimeTeam.clear', 'Use the global team')` y actualizar la clave `app.runtimeTeam.clear` en el catálogo (`en` "Use the global team", `es` "Usar el equipo global"). Grep previo: `grep -rn "Use automatic routing" tests_web local-control-center/web/src` y actualizar cada uso.

- [ ] **Step 4: Tarjeta del inspector**

```tsx
// local-control-center/web/src/features/shell/EffectiveTeamCard.tsx
/**
 * Effective AI team of a thread: per role, the provider that will run it and where the choice
 * comes from (this thread's own team, the project or general setting, or the automatic split).
 * @author Rodrigo Mason
 */
import { useI18n } from '../../i18n/I18nProvider';
import { readRuntimeTeam } from '../runtime-team/runtimeTeamModel';
import { useRuntimeTeam } from '../runtime-team/useRuntimeTeam';

const ROLE_LABEL: Record<string, { key: string; fallback: string }> = {
	product_owner: { key: 'app.aiTeam.role.product_owner', fallback: 'Product Owner' },
	developer: { key: 'app.aiTeam.role.developer', fallback: 'Developer' },
	architect: { key: 'app.aiTeam.role.architect', fallback: 'Architect' },
	security: { key: 'app.aiTeam.role.security', fallback: 'Security' },
	technical_lead: { key: 'app.aiTeam.role.technical_lead', fallback: 'Technical Lead' },
	researcher: { key: 'app.aiTeam.role.researcher', fallback: 'Researcher' },
};

export function EffectiveTeamCard({ projectId, threadMetadata }: { projectId: string; threadMetadata: unknown }) {
	const { t } = useI18n();
	const { data } = useRuntimeTeam(projectId, true);
	const threadTeam = readRuntimeTeam(threadMetadata);
	if (!data) return null;
	const labelOf = (id: string | null | undefined) =>
		id ? (data.candidates.find((c) => c.providerId === id)?.label ?? id) : t('app.aiTeam.unassigned', 'unassigned');
	return (
		<section className="card card--static effective-team" aria-label={t('app.aiTeam.effectiveTitle', 'Effective AI team')}>
			<h4 className="card-title">{t('app.aiTeam.effectiveTitle', 'Effective AI team')}</h4>
			<dl className="provider-facts">
				{data.roles.map((role) => {
					const own = threadTeam?.roleRuntimes[role.role as keyof typeof threadTeam.roleRuntimes];
					const source = own ? t('app.aiTeam.source.thread', 'this thread') : t(`app.aiTeam.source.${role.source}`, role.source);
					return (
						<div key={role.role}>
							<dt>{t(ROLE_LABEL[role.role]?.key ?? role.role, ROLE_LABEL[role.role]?.fallback ?? role.role)}</dt>
							<dd>
								{labelOf(own ?? role.assigned)} <span className="muted">· {source}</span>
							</dd>
						</div>
					);
				})}
			</dl>
		</section>
	);
}
```

En `ThreadInspector.tsx`: `TeamPanel` recibe además `projectId: string` y `detail: InspectorResource<ThreadDetail>`; renderiza `<EffectiveTeamCard projectId={projectId} threadMetadata={detail.data?.thread.metadata} />` antes del `<ResourceGate resource={roster}>`. En el sitio de uso (`tab === 'team'`, ~línea 201) pasar `projectId={project.id}` y `detail={detail}`.

- [ ] **Step 5: i18n**

```
app.runtimeTeam.chipGlobal  "AI team · global" / "Equipo IA · global"
app.aiTeam.effectiveTitle   "Effective AI team" / "Equipo de IA efectivo"
app.aiTeam.unassigned       "unassigned" / "sin asignar"
app.aiTeam.source.thread    "this thread" / "este hilo"
```

(y actualizar `app.runtimeTeam.clear` como se indicó).

- [ ] **Step 6: Verde y gates**

Run: `corepack pnpm@10.24.0 run typecheck:web && corepack pnpm@10.24.0 run check:web && PLAYWRIGHT_DASHBOARD_PORT=4997 node scripts/run-web-tests.mjs tests_web/thread-runtime-team.spec.js tests_web/thread-inspector.spec.js tests_web/threads.spec.js`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add local-control-center/web/src/features/runtime-team/RuntimeTeamChip.tsx local-control-center/web/src/features/runtime-team/RuntimeTeamPanel.tsx local-control-center/web/src/features/shell/EffectiveTeamCard.tsx local-control-center/web/src/features/shell/ThreadInspector.tsx local_control_center/i18n/default_catalog.json tests_web/thread-runtime-team.spec.js tests_web/thread-inspector.spec.js
git commit -m "Feature (Web): el intake muestra el equipo global por defecto y el inspector el proveedor efectivo de cada rol" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 9: Documentación, gates completos y validación en vivo

**Files:**
- Modify: `docs/model-routing.md` (nueva sección "Global AI team" después de "Role pins")
- Modify: `docs/runtime-providers.md` (sección "Settings and project mode": párrafo sobre el switch)
- Modify: `docs/superpowers/specs/2026-09-22-thread-runtime-team-design.md` (nota de estado: el equipo por hilo pasa a override opcional del equipo global; enlace al spec 2026-09-26)

- [ ] **Step 1: Docs**

En `docs/model-routing.md`, después de la sección "Role pins…", agregar:

```markdown
## Global AI team: which provider serves each role, for every thread

`team.role.<role>` (`string_list`, general scope with a project override) lists the providers a role
may use, in order: the first is the assigned one, the rest are fallbacks. Roles: `product_owner`,
`developer` (required), `architect`, `security` (optional), `technical_lead`, `researcher` (empty ⇒
they inherit the Product Owner's order). An empty list means "automatic": `auto_assign_roles`
(`runtime_team/roles.py`) over the enabled, policy-allowed providers, with the remaining eligible
providers as fallbacks. `GET /api/v1/runtime/team?projectId=` returns the effective team.

At send time, a thread without its own team seals the resolved team as `globalRuntimeTeam` in the
run metadata (`runtime_team/configuration.py:seal_thread_runtime_team`). `role_allowlist` then
returns the role's order, `AIResourceRequest.preferred_provider_ids`/`preferred_resources` carry it
ahead of the role policy pins (pins only pick the model within the chosen provider), and
`_run_with_failover` walks the fallbacks when the assigned provider is inactive or fails. A role whose
order the operator wrote explicitly is never widened to the whole catalog: if none of its providers
can serve, the role blocks with the existing assignment blocker. A thread's own team (`runtimeTeam`,
the composer chip) keeps priority and its single-provider allowlist.
```

En `docs/runtime-providers.md` → "Settings and project mode": agregar "The per-provider switch in *Settings → Providers & CLI* is `provider_accounts.enabled` (`PATCH /api/v1/model-gateway/providers/{id}`); a disabled provider is excluded from every thread's team, from failover and from the status bar's active-provider count."

- [ ] **Step 2: Gates completos**

Run: `corepack pnpm@10.24.0 run quality` (tier PR: ruff, pytest completo, Biome, typecheck, Playwright). Duración ~40-60 min. Registrar el resultado exacto. Si aparece un rojo que no está en la lista de fallos ambientales conocidos (memoria `aido-known-preexisting-test-failures`: `hard_memory_floor` por diseño, fixture sin `.venv` en worktrees, flakes), es de esta rebanada: corregir antes de seguir.

- [ ] **Step 3: Validación en vivo (sandbox `aido-e2e-sandbox`)**

1. Reiniciar el AIDO vivo (preview `control-center-real`) para cargar el código.
2. En *Settings → Providers & CLI*: apagar `codex_cli` con el switch y comprobar que la barra baja el conteo de proveedores activos y que `GET /api/v1/runtime/team?projectId=<sandbox>` deja de listarlo en `candidates`.
3. En *Settings → AI team* (general): poner `developer = [llama_cpp]` y `product_owner = [llama_cpp]`.
4. Crear un hilo pequeño en el sandbox (p. ej. "Agrega una función `slug_count` a textkit con su test") sin tocar el chip (debe decir "AI team · global"). Verificar en la BD (solo lectura, `mode=ro`) que el run sellado trae `globalRuntimeTeam.source.developer == "general"` y que los eventos `runtime_selected` del hilo muestran `llama_cpp` para developer y PO.
5. Apagar `llama_cpp` (switch) con el hilo terminado, poner `developer = [llama_cpp, claude_code_cli]` y volver a mandar un mensaje de continuación: el evento `runtime_failover`/selección debe caer a `claude_code_cli` (si Claude CLI sigue sin auth, el hilo bloquea con `runtime_not_executable` mencionando ambos: también es un resultado válido; anotarlo).
6. Restaurar los switches y la configuración.

Registrar en el informe de cierre los ids de hilo/loop y lo observado.

- [ ] **Step 4: Commit y push**

```bash
git add docs/model-routing.md docs/runtime-providers.md docs/superpowers/specs/2026-09-22-thread-runtime-team-design.md
git commit -m "Docs (RuntimeTeam): equipo de IA global, switch por proveedor y el equipo por hilo como override" -m "Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git push origin dev
```

---

## Self-review (hecho al escribir el plan)

- **Cobertura del spec (rebanada 1):** §4.1 switch (Task 6), barra de estado (Task 6); §4.2 settings/roles/automático/resolución/ruteo/failover (Tasks 1-4); §4.6 `runtime/team` (Task 5); §4.7 Providers & CLI, panel Equipo de IA, chip, inspector (Tasks 6-8); §4.8 filas de proveedor inactivo/falla/sin candidatos (Task 4 + failover existente); §4.10 unitarios, API, Playwright, gates, en vivo (cada task + Task 9). Fuera de esta rebanada: sesiones (§4.3-4.5), `team.sessionMaxTurns`, `provider-sessions` API, eventos `provider_session`.
- **Placeholders:** ninguno; donde una firma real puede diferir (props de `Button`/`ErrorState`/`Skeleton`, argumentos de `_team_resource_selection`, nombre del gate de drift) el paso dice qué leer y cómo ajustar.
- **Consistencia de nombres:** `GLOBAL_TEAM_ROLE_KEYS` (registry) = `GLOBAL_TEAM_ROLES` (global_team) — test en Task 2; `globalRuntimeTeam` = `GLOBAL_RUNTIME_TEAM_METADATA_KEY` en Tasks 3-4 y en los mocks de tests; `role_routing_preferences` y `global_role_is_explicit` definidas en Task 3 y usadas en Task 4; `getRuntimeTeam`/`RuntimeTeam`/`RuntimeTeamRole` definidos en Task 5 y usados en Tasks 7-8; claves i18n `app.aiTeam.*` compartidas por Tasks 7-8.
