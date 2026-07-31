"""Render de la constitución como bloque de prompt acotado y determinista.

El bloque es estrictamente opcional: los constructores de prompt lo anteponen solo cuando existe
(patrón ``spec_block`` de ``runtime_registry.developer_agent_prompt``), garantizando prompt
byte-idéntico cuando no hay constitución. Tope de caracteres propio con marcador de truncado para
que el documento nunca compita por contexto con el spec de la historia ni con el assessment.

@author Rodrigo Mason
"""

from __future__ import annotations

from typing import Any

CONSTITUTION_PROMPT_CHAR_LIMIT = 2_500
_TRUNCATION_MARKER = "\n[constitution truncated]"


def render_constitution_prompt(constitution: dict[str, Any] | None) -> str:
    """Convierte la constitución en texto de prompt compacto; cadena vacía cuando no hay documento."""
    if not constitution:
        return ""
    lines: list[str] = [f"Project constitution (v{constitution.get('version')}):"]
    for principle in constitution.get("principles") or []:
        text = str(principle).strip()
        if text:
            lines.append(f"- {text}")
    non_negotiables = [
        str(item).strip() for item in constitution.get("nonNegotiables") or [] if str(item).strip()
    ]
    if non_negotiables:
        lines.append("Non-negotiables (violating any of these fails the review):")
        lines.extend(f"- {item}" for item in non_negotiables)
    rendered = "\n".join(lines)
    if len(rendered) > CONSTITUTION_PROMPT_CHAR_LIMIT:
        rendered = rendered[:CONSTITUTION_PROMPT_CHAR_LIMIT] + _TRUNCATION_MARKER
    return rendered
