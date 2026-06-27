"""Pure resolver for the settings plane: applies project > general > default precedence.

``resolve_settings`` is a side-effect-free builder that reads from the repository and
produces the full ``{general, project}`` resolved snapshot in the camelCase dict shape
consumed directly by Pydantic models and the API layer. Precedence rules are:

- **project** override (scope='project', scope_id=project_id) → origin='project', inherited=False
- **general** value (scope='general') → origin='general', inherited=True (project scope) / False (general scope)
- **descriptor default** → origin='default', inherited=True (project scope) / False (general scope)
"""

from __future__ import annotations

import sqlite3
from typing import Any

from .registry import REGISTRY, SettingDescriptor
from .repository import UNSET, SettingsRepository


def _resolve_one(
    *,
    descriptor: SettingDescriptor,
    repo: SettingsRepository,
    project_id: str | None,
    scope_view: str,
) -> dict[str, Any]:
    """Resolve a single descriptor for either 'general' or 'project' scope view.

    For *general* scope view: project_id is ignored; origin is 'general' if a general
    value exists, else 'default'. inherited is always False.
    For *project* scope view: project override wins; falls back to general, then default.
    inherited=True when no project-level override exists.
    """
    editable_scopes: list[str] = [scope_view]

    if scope_view == "general":
        general_val = repo.get_value(descriptor.key, "general", None)
        if general_val is not UNSET:
            value, origin, inherited = general_val, "general", False
        else:
            value, origin, inherited = descriptor.default, "default", False
    else:
        # project scope view
        project_val = repo.get_value(descriptor.key, "project", project_id)
        if project_val is not UNSET:
            value, origin, inherited = project_val, "project", False
        else:
            general_val = repo.get_value(descriptor.key, "general", None)
            if general_val is not UNSET:
                value, origin, inherited = general_val, "general", True
            else:
                value, origin, inherited = descriptor.default, "default", True

    return {
        "key": descriptor.key,
        "labelKey": descriptor.label_key,
        "section": descriptor.section,
        "projectSection": descriptor.project_section,
        "type": descriptor.type,
        "enum": list(descriptor.enum) if descriptor.enum is not None else None,
        "value": value,
        "origin": origin,
        "inherited": inherited,
        "source": origin,
        "editableScopes": editable_scopes,
    }


def resolve_settings(
    *,
    connection: sqlite3.Connection,
    project_id: str | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return the full resolved settings snapshot as ``{general: [...], project: [...]}``.

    Each entry is a camelCase dict matching the ``ResolvedSetting`` Pydantic schema.
    The *general* list covers every descriptor; the *project* list covers only descriptors
    with a ``project_section`` (i.e. overridable at project scope).
    """
    repo = SettingsRepository(connection)

    general = [
        _resolve_one(descriptor=d, repo=repo, project_id=project_id, scope_view="general") for d in REGISTRY
    ]

    project = [
        _resolve_one(descriptor=d, repo=repo, project_id=project_id, scope_view="project")
        for d in REGISTRY
        if d.project_section is not None
    ]

    return {"general": general, "project": project}
