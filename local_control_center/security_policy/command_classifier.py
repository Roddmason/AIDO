"""Clasifica el riesgo de un comando shell por coincidencia de patrones y categorias.

Mapea un comando crudo a un nivel de riesgo (low/medium/critical) y a categorias semanticas
que el motor de politicas consume para decidir. Invariante: cualquier patron critico
(borrado destructivo, force-push, deploy a prod, escalada de privilegios, escritura de
secretos) gana sobre el resto y fuerza riskLevel critical; no ejecuta nada, solo etiqueta.

@author Rodrigo Mason
"""

from __future__ import annotations

import re
from typing import Any

from .permissions import (
    low_risk_shell_category,
    package_manager_category,
    parse_command,
    pnpm_script_category,
)

CRITICAL_RULES: list[tuple[str, re.Pattern[str]]] = [
    (
        "destructive_delete",
        re.compile(r"\b(rm\s+-rf|Remove-Item\b.*(?<!\S)-Recurse\b.*(?<!\S)-Force\b)", re.I),
    ),
    ("force_push", re.compile(r"\bgit\s+push\b.*\b--force\b", re.I)),
    ("prod_deploy", re.compile(r"\b(deploy|release)\b.*\b(prod|production)\b", re.I)),
    ("privilege_escalation", re.compile(r"\b(sudo|Start-Process\b.*\b-Verb\s+RunAs|runas)\b", re.I)),
    (
        "secrets_write",
        re.compile(r"\b(\.env|secret|credential|token)\b.*\b(write|set|update|remove|delete)\b", re.I),
    ),
]

MEDIUM_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("git_write", re.compile(r"\bgit\s+(commit|merge|rebase|push|checkout|branch)\b", re.I)),
    ("network", re.compile(r"\b(curl|Invoke-WebRequest|wget|ssh|scp)\b", re.I)),
]


def classify_command(command: str | None) -> dict[str, Any]:
    """Devuelve ``{"riskLevel", "categories"}`` para un comando, priorizando reglas criticas.

    Las reglas criticas (borrado destructivo, force-push, deploy a prod, escalada de
    privilegios, escritura de secretos) cortocircuitan a ``critical``. Si no aplica ninguna,
    deriva el riesgo de paquetes/scripts, reglas medias (git de escritura, red) y, por ultimo,
    categorias de bajo riesgo; un comando vacio es ``low``/``none`` y uno opaco es ``medium``.
    """
    text = command or ""
    categories: list[str] = []
    for name, pattern in CRITICAL_RULES:
        if pattern.search(text):
            categories.append(name)
    if categories:
        return {"riskLevel": "critical", "categories": categories}
    parsed = parse_command(text)
    if parsed:
        package_category = package_manager_category(parsed)
        if package_category:
            categories.append(package_category)
        pnpm_category = pnpm_script_category(parsed)
        if pnpm_category in {"package_script_hook", "package_script"}:
            categories.append(pnpm_category)
        if categories:
            return {"riskLevel": "medium", "categories": categories}
    for name, pattern in MEDIUM_RULES:
        if pattern.search(text):
            categories.append(name)
    if categories:
        return {"riskLevel": "medium", "categories": categories}
    if parsed:
        low_category = low_risk_shell_category(parsed)
        if low_category:
            return {"riskLevel": "low", "categories": [low_category]}
    return {
        "riskLevel": "medium" if text.strip() else "low",
        "categories": ["unknown"] if text.strip() else ["none"],
    }
