"""Política de fuentes del ResearchAgent: jerarquía de confianza, procedencia, citación y conflictos.

Lógica pura y determinista. Clasifica una fuente en la jerarquía de confianza (de mayor a menor:
documentación oficial, repos/releases oficiales, estándares/RFCs, investigación primaria, fuentes
secundarias reputables; todo lo demás es ``untrusted`` y queda fuera de política). Construye el registro
de procedencia de cada fuente (URL, publisher, ``fetchedAt``, hash SHA-256 del contenido, nivel de
confianza, artefacto relacionado). Exige que toda conclusión técnica basada en web cite al menos una
fuente. Y detecta claims en conflicto sobre un mismo tópico: emite un hallazgo explícito y una
recomendación basada en la fuente de mayor confianza, marcando para revisión manual cuando las fuentes
de máxima confianza discrepan entre sí.

@author Rodrigo Mason
"""

from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

from local_control_center.shared.redaction import redact_secrets

TRUST_LEVELS: tuple[str, ...] = (
    "official_documentation",
    "official_repository",
    "standard_rfc",
    "primary_research",
    "reputable_secondary",
)
UNTRUSTED = "untrusted"

_RFC_HOSTS = {"ietf.org", "rfc-editor.org", "w3.org", "iso.org", "ecma-international.org", "unicode.org"}
_DOCS_HOSTS = {
    "docs.python.org",
    "docs.rs",
    "docs.microsoft.com",
    "learn.microsoft.com",
    "docs.aws.amazon.com",
    "cloud.google.com",
    "docs.docker.com",
    "docs.github.com",
    "developer.mozilla.org",
    "developer.apple.com",
    "developer.android.com",
    "kubernetes.io",
    "nodejs.org",
}
_REPO_HOSTS = {"pypi.org", "npmjs.com", "registry.npmjs.org", "crates.io", "rubygems.org", "pkg.go.dev"}
_RESEARCH_HOSTS = {
    "arxiv.org",
    "doi.org",
    "dl.acm.org",
    "ieeexplore.ieee.org",
    "pubmed.ncbi.nlm.nih.gov",
    "link.springer.com",
    "sciencedirect.com",
    "nature.com",
}
_SECONDARY_HOSTS = {
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "raw.githubusercontent.com",
    "en.wikipedia.org",
    "stackoverflow.com",
    "martinfowler.com",
}


class ResearchPolicyError(ValueError):
    """Se lanza cuando se referencia un nivel de confianza fuera del catálogo de la política."""


def trust_rank(level: str) -> int:
    """Devuelve el rango del nivel (0 = mayor confianza); ``untrusted`` y desconocidos quedan al final."""
    return TRUST_LEVELS.index(level) if level in TRUST_LEVELS else len(TRUST_LEVELS)


def is_trusted(level: str) -> bool:
    """Indica si el nivel pertenece a la jerarquía de confianza de la política (no ``untrusted``)."""
    return level in TRUST_LEVELS


def _host_and_path(url: str) -> tuple[str, str]:
    parsed = urlparse(url if "://" in url else f"https://{url}")
    host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host, (parsed.path or "").lower()


def classify_source(url: str) -> str:
    """Devuelve el nivel de confianza de la fuente según una allowlist de host (conservador).

    Solo allowlists controladas elevan la confianza: ``rfc-editor.org`` → estándar, ``docs.python.org`` →
    documentación oficial, ``pypi.org`` → repo/release oficial, ``arxiv.org`` → investigación primaria. Un
    prefijo ``docs.`` o un host de code-hosting (``github.com``) NO prueban publisher oficial: caen a
    secundario o ``untrusted`` para que el agente deba afirmar el nivel explícitamente cuando lo verifica.
    El orden de chequeo respeta la precedencia de la jerarquía.
    """
    host, path = _host_and_path(url)
    if not host:
        return UNTRUSTED
    if host in _RFC_HOSTS or path.startswith("/rfc") or "/rfc/" in path or path.startswith("/tr/"):
        return "standard_rfc"
    if host in _DOCS_HOSTS:
        return "official_documentation"
    if host in _REPO_HOSTS:
        return "official_repository"
    if host in _RESEARCH_HOSTS or host.endswith(".edu"):
        return "primary_research"
    if host in _SECONDARY_HOSTS:
        return "reputable_secondary"
    return UNTRUSTED


def content_hash(content: str) -> str:
    """Devuelve el hash SHA-256 (hex) del contenido textual de la fuente."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def build_source_record(
    *,
    url: str,
    publisher: str,
    content: str,
    fetched_at: str,
    title: str | None = None,
    source_type: str | None = None,
    related_artifact: str | None = None,
    related_thread_id: str | None = None,
    related_task_id: str | None = None,
    trust_level: str | None = None,
) -> dict[str, Any]:
    """Construye el registro de procedencia de una fuente, con su hash y nivel de confianza.

    ``trust_level`` explícito (afirmado por el agente) tiene prioridad sobre la heurística de
    ``classify_source``. Persiste: URL (redactada por si lleva credenciales incrustadas), publisher,
    ``fetchedAt``, ``hash`` (SHA-256 del contenido), ``trustLevel`` y ``relatedArtifact``. Rechaza
    contenido vacío: el hash del string vacío es constante y haría pasar dos fetches fallidos por
    idénticos.

    Raises:
        ResearchPolicyError: si ``trust_level`` no es un nivel conocido (ni ``untrusted``), o si
            ``content`` está vacío o es solo espacios (fetch fallido, no se debe persistir como fuente).
    """
    if trust_level is not None and trust_level not in TRUST_LEVELS and trust_level != UNTRUSTED:
        raise ResearchPolicyError(f"Unknown trust level: {trust_level}.")
    if not content or not content.strip():
        raise ResearchPolicyError("Empty source content: a failed fetch must not be persisted as a source.")
    level = trust_level or classify_source(url)
    return {
        "url": redact_secrets(url),
        "title": title.strip() if isinstance(title, str) and title.strip() else publisher,
        "publisher": publisher,
        "sourceType": source_type.strip() if isinstance(source_type, str) and source_type.strip() else "web",
        "fetchedAt": fetched_at,
        "hash": content_hash(content),
        "trustLevel": level,
        "relatedArtifact": related_artifact,
        "relatedThreadId": related_thread_id,
        "relatedTaskId": related_task_id,
    }


def require_citations(
    conclusions: list[dict[str, Any]], *, sources: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Verifica que toda conclusión técnica basada en web cite al menos una fuente CONFIABLE.

    Una conclusión es ``{statement, citations: [url, ...], webBased: bool}``; las marcadas ``webBased``
    (por defecto ``True``) sin ``citations`` son ``uncited``. Si se pasan ``sources`` (registros con
    ``url``/``trustLevel``), las conclusiones cuyas citas resuelven todas a ``untrusted`` son
    ``untrustedOnly``: citar solo una fuente fuera de política no cumple. Devuelve
    ``{valid, uncited, untrustedOnly}`` para que el caller falle cerrado.
    """
    trust_by_url = {source.get("url"): source.get("trustLevel", UNTRUSTED) for source in (sources or [])}
    uncited: list[str] = []
    untrusted_only: list[str] = []
    for conclusion in conclusions:
        if not conclusion.get("webBased", True):
            continue
        statement = str(conclusion.get("statement", ""))
        citations = conclusion.get("citations") or []
        if not citations:
            uncited.append(statement)
        elif trust_by_url and all(
            trust_by_url.get(citation, UNTRUSTED) == UNTRUSTED for citation in citations
        ):
            untrusted_only.append(statement)
    return {"valid": not uncited and not untrusted_only, "uncited": uncited, "untrustedOnly": untrusted_only}


def detect_conflicts(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Detecta claims en conflicto por tópico y emite, por cada uno, un hallazgo con recomendación.

    Un ``claim`` es ``{topic, value, source}`` donde ``source`` lleva ``trustLevel`` y ``url``. Para cada
    tópico con dos o más valores distintos emite un hallazgo: ordena los claims por confianza y recomienda
    el valor de la fuente de mayor confianza; si dos o más fuentes de la MÁXIMA confianza presente
    discrepan, marca ``needsManualReview`` en vez de resolver automáticamente.
    """
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for claim in claims:
        by_topic.setdefault(str(claim.get("topic", "")), []).append(claim)

    findings: list[dict[str, Any]] = []
    for topic, topic_claims in by_topic.items():
        values = {str(claim.get("value")) for claim in topic_claims}
        if len(values) < 2:
            continue
        ranked = sorted(
            topic_claims,
            key=lambda claim: trust_rank((claim.get("source") or {}).get("trustLevel", UNTRUSTED)),
        )
        top_rank = trust_rank((ranked[0].get("source") or {}).get("trustLevel", UNTRUSTED))
        top_claims = [
            claim
            for claim in ranked
            if trust_rank((claim.get("source") or {}).get("trustLevel", UNTRUSTED)) == top_rank
        ]
        top_values = {str(claim.get("value")) for claim in top_claims}
        if len(top_values) > 1:
            recommendation = {
                "needsManualReview": True,
                "reason": "The highest-trust sources disagree; a human must resolve the conflict.",
            }
        else:
            winner = top_claims[0]
            source = winner.get("source") or {}
            winner_fetched = str(source.get("fetchedAt") or "")
            newest_fetched = max(
                (str((claim.get("source") or {}).get("fetchedAt") or "") for claim in topic_claims),
                default="",
            )
            recommendation = {
                "needsManualReview": False,
                "value": winner.get("value"),
                "basis": source.get("trustLevel", UNTRUSTED),
                "sourceUrl": source.get("url"),
                "staleWarning": bool(winner_fetched and newest_fetched and winner_fetched < newest_fetched),
            }
        findings.append(
            {
                "topic": topic,
                "conflictingValues": sorted(values),
                "claims": [
                    {
                        "value": claim.get("value"),
                        "trustLevel": (claim.get("source") or {}).get("trustLevel", UNTRUSTED),
                        "sourceUrl": (claim.get("source") or {}).get("url"),
                        "fetchedAt": (claim.get("source") or {}).get("fetchedAt"),
                    }
                    for claim in ranked
                ],
                "recommendation": recommendation,
            }
        )
    return findings
