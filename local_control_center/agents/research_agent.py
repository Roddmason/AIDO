"""Ejecuta el ResearchAgent: aplica política de fuentes, citas y conflictos con evidencia.

El runner consume fuentes verificables, persiste cada una como artefacto `research_source` con
provenance completo, valida que las conclusiones técnicas basadas en web citen fuentes confiables y
emite hallazgos explícitos para claims en conflicto. No sintetiza conclusiones ni reemplaza fuentes:
si las citas o la confianza no alcanzan el contrato, falla cerrado.

@author Rodrigo Mason
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urljoin, urlparse
from urllib.request import Request, urlopen

from local_control_center.evidence.artifacts import (
    artifact_hashes,
    artifact_records_from_ids,
    artifact_ref,
    write_text_artifact,
)
from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.jobs_approvals.repository import JobsRepository
from local_control_center.research.repository import ResearchRepository
from local_control_center.research.source_log import persist_source
from local_control_center.research.source_policy import (
    UNTRUSTED,
    ResearchPolicyError,
    build_source_record,
    classify_source,
    detect_conflicts,
    require_citations,
    trust_rank,
)
from local_control_center.shared.redaction import redact_secrets
from local_control_center.shared.serialization import json_dumps
from local_control_center.shared.time import utc_now
from local_control_center.threads.repository import ThreadsRepository
from local_control_center.workspaces_projects.repository import WorkspacesRepository

from .repository import AgentsRepository
from .research_agent_contract import RESEARCH_AGENT_ID, research_agent_contract, research_agent_status

MAX_SOURCE_BYTES = 1_000_000
FETCH_TIMEOUT_SECONDS = 20
MAX_RESEARCH_SOURCES = 50
DEFAULT_WEB_SEARCH_MAX_SOURCES = 5
WEB_SEARCH_TIMEOUT_SECONDS = 10
MAX_WEB_SEARCH_BYTES = 512_000
WEB_SEARCH_RESULT_BUFFER_MULTIPLIER = 4
DUCKDUCKGO_HTML_ENDPOINT = "https://duckduckgo.com/html/"
WebSearchProvider = Callable[[str, int], list[dict[str, Any]]]


def _source_policy_rank(source: dict[str, Any]) -> int:
    level = str(source.get("trustLevel") or classify_source(str(source.get("url") or "")))
    return trust_rank(level)


class _DuckDuckGoResultParser(HTMLParser):
    """Extrae resultados orgánicos del HTML simple de DuckDuckGo sin ejecutar scripts."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._current_href: str | None = None
        self._current_title: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attributes = {key.lower(): value or "" for key, value in attrs}
        css_class = attributes.get("class", "")
        href = attributes.get("href", "")
        if not href:
            return
        if "result__a" not in css_class and "/l/?" not in href:
            return
        self._current_href = href
        self._current_title = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None:
            self._current_title.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        title = " ".join("".join(self._current_title).split())
        self.results.append({"url": self._current_href, "title": title})
        self._current_href = None
        self._current_title = []


def _normalize_search_result_url(raw_url: str) -> str | None:
    url = unescape(raw_url).strip()
    if not url:
        return None
    if url.startswith("//"):
        url = f"https:{url}"
    elif url.startswith("/"):
        url = urljoin(DUCKDUCKGO_HTML_ENDPOINT, url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if host.endswith("duckduckgo.com") and parsed.path.startswith("/l/"):
        target = (parse_qs(parsed.query).get("uddg") or [""])[0].strip()
        if not target:
            return None
        url = target
        parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return url


def _publisher_from_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or parsed.netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


def _duckduckgo_web_search_provider(query: str, max_sources: int) -> list[dict[str, Any]]:
    safe_query = str(redact_secrets(query)).strip()
    if not safe_query or safe_query == "[redacted]":
        raise ResearchAgentValidationError("ResearchAgent web search query is empty after secret redaction.")
    candidate_limit = min(MAX_RESEARCH_SOURCES, max(max_sources * WEB_SEARCH_RESULT_BUFFER_MULTIPLIER, 10))
    request_url = f"{DUCKDUCKGO_HTML_ENDPOINT}?{urlencode({'q': safe_query})}"
    request = Request(
        request_url,
        headers={
            "User-Agent": "AIDO-ResearchAgent/1.0",
            "Accept": "text/html",
        },
    )
    try:
        with urlopen(request, timeout=WEB_SEARCH_TIMEOUT_SECONDS) as response:
            content = response.read(MAX_WEB_SEARCH_BYTES + 1)
            if len(content) > MAX_WEB_SEARCH_BYTES:
                raise ResearchAgentValidationError(
                    "ResearchAgent web search response exceeds the fetch limit."
                )
            content_type = response.headers.get_content_charset() or "utf-8"
    except (HTTPError, URLError, TimeoutError) as error:
        raise ResearchAgentValidationError(f"ResearchAgent web search failed: {error}") from error

    parser = _DuckDuckGoResultParser()
    parser.feed(content.decode(content_type, errors="replace"))
    sources: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for result in parser.results:
        url = _normalize_search_result_url(result["url"])
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        sources.append(
            {
                "url": url,
                "title": result.get("title") or _publisher_from_url(url),
                "publisher": _publisher_from_url(url),
                "sourceType": "web_search",
            }
        )
        if len(sources) >= candidate_limit:
            break
    return sources


class ResearchAgentValidationError(ValueError):
    """Se lanza cuando el payload de ResearchAgent no cumple el contrato de investigación."""


def _fetch_url_text(url: str) -> str:
    """Obtiene texto de una fuente HTTP(S) acotada, sin credenciales incrustadas ni esquemas locales."""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ResearchAgentValidationError("ResearchAgent source URL must be absolute HTTP(S).")
    if parsed.username or parsed.password:
        raise ResearchAgentValidationError("ResearchAgent source URL must not contain credentials.")
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"}:
        raise ResearchAgentValidationError("ResearchAgent source URL must not target loopback hosts.")
    request = Request(url, headers={"User-Agent": "AIDO-ResearchAgent/1.0"})
    try:
        with urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            content = response.read(MAX_SOURCE_BYTES + 1)
            if len(content) > MAX_SOURCE_BYTES:
                raise ResearchAgentValidationError("ResearchAgent source content exceeds the fetch limit.")
            content_type = response.headers.get_content_charset() or "utf-8"
    except (HTTPError, URLError, TimeoutError) as error:
        raise ResearchAgentValidationError(f"ResearchAgent source fetch failed: {error}") from error
    return content.decode(content_type, errors="replace")


def _source_text(source: dict[str, Any]) -> str:
    content = source.get("content")
    if content is not None:
        return str(content)
    return _fetch_url_text(str(source.get("url") or ""))


def _required_text(payload: dict[str, Any], key: str, *, field: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise ResearchAgentValidationError(f"{field}.{key} is required.")
    return value


class ResearchAgentRunner:
    """Orquesta el ResearchAgent sobre la base, workspace y evidencia existentes."""

    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        root: Path,
        web_search_provider: WebSearchProvider | None = None,
    ):
        self.connection = connection
        self.root = root
        self.web_search_provider = web_search_provider or _duckduckgo_web_search_provider
        self.agents = AgentsRepository(connection)
        self.jobs = JobsRepository(connection)
        self.evidence = EvidenceRepository(connection)
        self.research = ResearchRepository(connection)
        self.workspaces = WorkspacesRepository(connection, root=root)

    def status(self) -> dict[str, Any]:
        """Devuelve el readiness del ResearchAgent."""
        return research_agent_status()

    def _ensure_profile(self) -> dict[str, Any]:
        return self.agents.upsert_agent_profile(
            {
                "id": RESEARCH_AGENT_ID,
                "name": "AIDO Research Agent",
                "role": "analyst",
                "runtimeMode": "manual",
                "permissionProfile": "plan",
                "allowedTools": [],
                "allowedProviders": [],
                "allowedRuntimes": [],
                "allowRemote": False,
                "allowCli": False,
                "allowApi": False,
                "outputSchema": research_agent_contract()["outputSchema"],
            }
        )

    def _workspace(self, *, project_id: str, workspace_id: str) -> dict[str, Any]:
        workspace = self.workspaces.get_workspace(workspace_id)
        if workspace["projectId"] != project_id:
            raise ValueError("ResearchAgent workspace does not belong to the project.")
        if workspace["status"] == "archived":
            raise ValueError("ResearchAgent cannot run on an archived workspace.")
        return workspace

    def _max_sources(self, payload: dict[str, Any]) -> int:
        requested = payload.get("maxSources") or (payload.get("metadata") or {}).get("maxSources")
        if requested is None:
            return DEFAULT_WEB_SEARCH_MAX_SOURCES
        try:
            value = int(requested)
        except (TypeError, ValueError) as error:
            raise ResearchAgentValidationError("ResearchAgent maxSources must be an integer.") from error
        if value < 1 or value > MAX_RESEARCH_SOURCES:
            raise ResearchAgentValidationError(
                f"ResearchAgent maxSources must be between 1 and {MAX_RESEARCH_SOURCES}."
            )
        return value

    @staticmethod
    def _web_search_allowed(metadata: dict[str, Any]) -> bool:
        policy = metadata.get("policy") if isinstance(metadata.get("policy"), dict) else {}
        research_policy = (
            metadata.get("researchPolicy") if isinstance(metadata.get("researchPolicy"), dict) else {}
        )
        return bool(
            metadata.get("allowWebSearch")
            or metadata.get("webSearchAllowed")
            or policy.get("allowWebSearch")
            or policy.get("webSearchAllowed")
            or research_policy.get("allowWebSearch")
            or research_policy.get("webSearchAllowed")
        )

    def _sources_for_payload(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        sources = payload.get("sources") or []
        if not isinstance(sources, list):
            raise ResearchAgentValidationError("ResearchAgent sources must be a list.")
        if len(sources) > MAX_RESEARCH_SOURCES:
            raise ResearchAgentValidationError(
                f"ResearchAgent accepts at most {MAX_RESEARCH_SOURCES} sources."
            )
        if sources:
            return sources

        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        query = str(payload.get("query") or metadata.get("query") or "").strip()
        if not query:
            raise ResearchAgentValidationError(
                "ResearchAgent requires at least one source or a research query."
            )
        if not self._web_search_allowed(metadata):
            raise ResearchAgentValidationError("ResearchAgent web search is disabled by policy.")
        if self.web_search_provider is None:
            raise ResearchAgentValidationError("ResearchAgent web search provider is not configured.")

        discovered = self.web_search_provider(query, self._max_sources(payload))
        if not isinstance(discovered, list):
            raise ResearchAgentValidationError("ResearchAgent web search provider must return a source list.")
        if not discovered:
            raise ResearchAgentValidationError("ResearchAgent web search returned no sources.")
        return sorted(discovered, key=_source_policy_rank)[: self._max_sources(payload)]

    def _persist_sources(
        self,
        *,
        project_id: str,
        thread_id: str | None,
        task_id: str,
        agent_run_id: str | None,
        sources: list[dict[str, Any]],
        report_artifact_id: str,
    ) -> tuple[list[dict[str, Any]], list[str]]:
        if not sources:
            raise ResearchAgentValidationError("ResearchAgent requires at least one source.")
        if len(sources) > MAX_RESEARCH_SOURCES:
            raise ResearchAgentValidationError(
                f"ResearchAgent accepts at most {MAX_RESEARCH_SOURCES} sources."
            )
        records: list[dict[str, Any]] = []
        artifact_ids: list[str] = []
        for index, source in enumerate(sources):
            if not isinstance(source, dict):
                raise ResearchAgentValidationError(f"sources[{index}] must be an object.")
            content = _source_text(source)
            related_artifact = str(source.get("relatedArtifact") or report_artifact_id)
            source_url = _required_text(source, "url", field=f"sources[{index}]")
            record = build_source_record(
                url=source_url,
                title=str(source.get("title") or "").strip() or None,
                publisher=_required_text(source, "publisher", field=f"sources[{index}]"),
                content=content,
                fetched_at=str(source.get("fetchedAt") or utc_now()),
                source_type=str(source.get("sourceType") or source.get("source_type") or "web"),
                related_artifact=related_artifact,
                related_thread_id=thread_id,
                related_task_id=task_id,
                trust_level=source.get("trustLevel"),
            )
            artifact = persist_source(
                self.evidence,
                root=self.root,
                project_id=project_id,
                thread_id=thread_id,
                task_id=task_id,
                agent_run_id=agent_run_id,
                record=record,
                content=content,
            )
            records.append(artifact)
            artifact_ids.append(artifact["artifactId"])
        return records, artifact_ids

    def _normalize_conclusions(self, conclusions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
        for index, conclusion in enumerate(conclusions):
            if not isinstance(conclusion, dict):
                raise ResearchAgentValidationError(f"conclusions[{index}] must be an object.")
            citations = conclusion.get("citations") or []
            if not isinstance(citations, list) or not all(isinstance(item, str) for item in citations):
                raise ResearchAgentValidationError(f"conclusions[{index}].citations must be a string list.")
            normalized.append(
                {
                    "statement": _required_text(conclusion, "statement", field=f"conclusions[{index}]"),
                    "citations": citations,
                    "webBased": bool(conclusion.get("webBased", True)),
                }
            )
        return normalized

    def _claims_with_sources(
        self, *, claims: list[dict[str, Any]], sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_url = {str(source["url"]): source for source in sources}
        normalized: list[dict[str, Any]] = []
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                raise ResearchAgentValidationError(f"claims[{index}] must be an object.")
            source_url = _required_text(claim, "sourceUrl", field=f"claims[{index}]")
            source = by_url.get(source_url)
            if not source:
                raise ResearchAgentValidationError(f"claims[{index}].sourceUrl must reference a source URL.")
            normalized.append(
                {
                    "topic": _required_text(claim, "topic", field=f"claims[{index}]"),
                    "value": _required_text(claim, "value", field=f"claims[{index}]"),
                    "source": source,
                }
            )
        return normalized

    def _normalize_technical_decisions(
        self, *, decisions: list[dict[str, Any]], sources: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        by_url = {str(source["url"]): source for source in sources}
        normalized: list[dict[str, Any]] = []
        for index, decision in enumerate(decisions):
            if not isinstance(decision, dict):
                raise ResearchAgentValidationError(f"technicalDecisions[{index}] must be an object.")
            source_urls = decision.get("sourceUrls") or []
            if not source_urls:
                raise ResearchAgentValidationError(
                    f"technicalDecisions[{index}].sourceUrls must cite at least one source."
                )
            citations = []
            for source_url in source_urls:
                source = by_url.get(str(source_url))
                if not source:
                    raise ResearchAgentValidationError(
                        f"technicalDecisions[{index}].sourceUrls must reference persisted sources."
                    )
                citations.append(self._source_citation(source))
            if all(citation["trustLevel"] == UNTRUSTED for citation in citations):
                raise ResearchAgentValidationError(
                    f"technicalDecisions[{index}] must cite at least one trusted source."
                )
            normalized.append(
                {
                    "title": _required_text(decision, "title", field=f"technicalDecisions[{index}]"),
                    "decision": _required_text(decision, "decision", field=f"technicalDecisions[{index}]"),
                    "sourceCitations": citations,
                }
            )
        return normalized

    @staticmethod
    def _source_citation(source: dict[str, Any]) -> dict[str, Any]:
        return {
            "sourceId": source.get("id"),
            "artifactId": source.get("artifactId"),
            "url": source.get("url"),
            "title": source.get("title"),
            "publisher": source.get("publisher"),
            "sourceType": source.get("sourceType"),
            "trustLevel": source.get("trustLevel"),
            "fetchedAt": source.get("fetchedAt"),
            "hash": source.get("hash"),
        }

    def _recommendation(
        self,
        *,
        technical_decisions: list[dict[str, Any]],
        conflict_findings: list[dict[str, Any]],
        conclusions: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        status: str,
        reason: str,
    ) -> dict[str, Any]:
        if technical_decisions:
            first = technical_decisions[0]
            return {
                "title": first["title"],
                "decision": first["decision"],
                "sourceCitations": first["sourceCitations"],
            }
        for finding in conflict_findings:
            recommendation = finding.get("recommendation") or {}
            if recommendation.get("needsManualReview"):
                return {
                    "title": f"Manual review required: {finding.get('topic')}",
                    "decision": recommendation.get("reason", reason),
                    "sourceCitations": [
                        {
                            "url": claim.get("sourceUrl"),
                            "trustLevel": claim.get("trustLevel"),
                            "fetchedAt": claim.get("fetchedAt"),
                        }
                        for claim in finding.get("claims") or []
                    ],
                }
            if recommendation.get("sourceUrl"):
                return {
                    "title": f"Prefer highest-trust source for {finding.get('topic')}",
                    "decision": str(recommendation.get("value") or ""),
                    "sourceCitations": [
                        {
                            "url": recommendation.get("sourceUrl"),
                            "trustLevel": recommendation.get("basis"),
                        }
                    ],
                }
        trusted_sources = [source for source in sources if source.get("trustLevel") != UNTRUSTED]
        if conclusions and trusted_sources:
            first_statement = str(conclusions[0].get("statement") or reason)
            return {
                "title": "Research recommendation",
                "decision": first_statement,
                "sourceCitations": [self._source_citation(source) for source in trusted_sources],
            }
        if trusted_sources:
            return {
                "title": "Research recommendation",
                "decision": reason,
                "sourceCitations": [self._source_citation(source) for source in trusted_sources],
            }
        return {"title": status.replace("_", " "), "decision": reason, "sourceCitations": []}

    @staticmethod
    def _remediation_for_blocker(*, status: str, reason: str) -> dict[str, Any]:
        if status != "research_blocked":
            return {}
        reason_text = reason.lower()
        if "network" in reason_text or "urlopen" in reason_text or "timed out" in reason_text:
            return {
                "action": "check_network_access",
                "summary": "Check network access and retry ResearchAgent after official sources are reachable.",
                "steps": [
                    "Check network access from the local AIDO runtime.",
                    "Retry ResearchAgent with the same query after connectivity is restored.",
                    "If the official source is unavailable, provide an approved source payload explicitly.",
                ],
            }
        if "web search is disabled by policy" in reason_text:
            return {
                "action": "enable_research_policy_or_provide_sources",
                "summary": "Enable web search by policy or provide official sources explicitly.",
                "steps": [
                    "Set researchPolicy.webSearchAllowed when internet research is permitted.",
                    "Otherwise provide official source URLs and content in the ResearchAgent request.",
                ],
            }
        if "cite" in reason_text or "trusted source" in reason_text:
            return {
                "action": "add_trusted_citations",
                "summary": "Add trusted citations for every web-based conclusion.",
                "steps": [
                    "Use official documentation, official repositories, standards, or primary research.",
                    "Reference each cited source URL from conclusions or technical decisions.",
                ],
            }
        return {
            "action": "review_research_blocker",
            "summary": "Review the ResearchAgent blocker and retry with compliant official sources.",
            "steps": [
                "Inspect the persisted research findings.",
                "Retry after correcting the source policy issue.",
            ],
        }

    def _persist_findings(
        self,
        *,
        research_run_id: str,
        project_id: str,
        thread_id: str | None,
        status: str,
        reason: str,
        citation_check: dict[str, Any],
        conflict_findings: list[dict[str, Any]],
        technical_decisions: list[dict[str, Any]],
        recommendation: dict[str, Any],
        remediation: dict[str, Any],
    ) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        findings.append(
            self.research.create_finding(
                research_run_id=research_run_id,
                project_id=project_id,
                thread_id=thread_id,
                finding_type="citation_check",
                severity="low" if citation_check.get("valid") else "high",
                summary="Citation policy passed."
                if citation_check.get("valid")
                else "Citation policy blocked the research run.",
                payload=citation_check,
            )
        )
        for conflict in conflict_findings:
            claims = conflict.get("claims") if isinstance(conflict.get("claims"), list) else []
            citations = [
                {
                    "url": claim.get("sourceUrl"),
                    "trustLevel": claim.get("trustLevel"),
                    "fetchedAt": claim.get("fetchedAt"),
                }
                for claim in claims
                if isinstance(claim, dict)
            ]
            findings.append(
                self.research.create_finding(
                    research_run_id=research_run_id,
                    project_id=project_id,
                    thread_id=thread_id,
                    finding_type="contradiction",
                    severity="high"
                    if (conflict.get("recommendation") or {}).get("needsManualReview")
                    else "medium",
                    summary=f"Contradictory source claims for {conflict.get('topic')}.",
                    citations=citations,
                    payload=conflict,
                )
            )
        for decision in technical_decisions:
            citations = decision.get("sourceCitations") if isinstance(decision, dict) else []
            findings.append(
                self.research.create_finding(
                    research_run_id=research_run_id,
                    project_id=project_id,
                    thread_id=thread_id,
                    finding_type="technical_decision",
                    severity="medium",
                    summary=str(decision.get("title") or decision.get("decision") or "Technical decision"),
                    source_ids=[
                        str(citation.get("sourceId"))
                        for citation in citations
                        if isinstance(citation, dict) and citation.get("sourceId")
                    ],
                    citations=citations,
                    payload=decision,
                )
            )
        if status == "research_blocked":
            findings.append(
                self.research.create_finding(
                    research_run_id=research_run_id,
                    project_id=project_id,
                    thread_id=thread_id,
                    finding_type="blocker",
                    severity="high",
                    summary=reason,
                    payload={"reason": reason, "remediation": remediation},
                )
            )
        citations = (
            recommendation.get("sourceCitations")
            if isinstance(recommendation.get("sourceCitations"), list)
            else []
        )
        findings.append(
            self.research.create_finding(
                research_run_id=research_run_id,
                project_id=project_id,
                thread_id=thread_id,
                finding_type="recommendation",
                severity="low" if status == "research_ready" else "medium",
                summary=str(recommendation.get("decision") or reason),
                source_ids=[
                    str(citation.get("sourceId"))
                    for citation in citations
                    if isinstance(citation, dict) and citation.get("sourceId")
                ],
                citations=citations,
                payload=recommendation,
            )
        )
        return findings

    def _write_report_artifact(
        self, *, project_id: str, artifact_id: str, report: dict[str, Any]
    ) -> dict[str, Any]:
        content = json_dumps(redact_secrets(report))
        written = write_text_artifact(
            root=self.root, artifact_id=artifact_id, suffix=".research.json", content=content
        )
        return self.evidence.create_artifact(
            artifact_id=artifact_id,
            project_id=project_id,
            evidence_package_id=None,
            kind="research_report",
            path=written["path"],
            content_hash=written["hash"],
            metadata={
                "name": "research-agent-report.json",
                "source": RESEARCH_AGENT_ID,
                "mimeType": "application/json",
                "sizeBytes": written["sizeBytes"],
                "hashAlgorithm": "sha256",
            },
        )

    def _status_from_policy(
        self, *, citation_check: dict[str, Any], conflict_findings: list[dict[str, Any]]
    ) -> tuple[str, str]:
        if not citation_check["valid"]:
            return "research_blocked", "Technical web conclusions must cite at least one trusted source."
        if any(
            (finding.get("recommendation") or {}).get("needsManualReview") for finding in conflict_findings
        ):
            return "research_blocked", "Highest-trust sources conflict and require human review."
        return "research_ready", "ResearchAgent completed source policy validation."

    def _link_sources_to_evidence(self, *, artifact_ids: list[str], evidence_id: str) -> None:
        if not artifact_ids:
            return
        placeholders = ",".join("?" for _ in artifact_ids)
        self.connection.execute(
            f"UPDATE research_sources SET evidence_package_id = ? WHERE artifact_id IN ({placeholders})",
            (evidence_id, *artifact_ids),
        )

    def _attach_report_to_thread(
        self,
        *,
        thread_id: str | None,
        project_id: str,
        report_artifact_id: str,
        report: dict[str, Any],
    ) -> None:
        if not thread_id:
            return
        threads = ThreadsRepository(self.connection)
        thread = threads.get_thread(thread_id)
        if thread["projectId"] != project_id:
            raise ValueError("ResearchAgent thread does not belong to the project.")
        threads.attach_artifact(
            thread_id=thread_id,
            kind="research_report",
            title="Research",
            artifact_id=report_artifact_id,
            payload={
                "status": report["status"],
                "reason": report["reason"],
                "recommendation": report["recommendation"],
                "sources": report["sources"],
                "technicalDecisions": report["technicalDecisions"],
                "discrepancies": report["discrepancies"],
            },
        )

    def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Ejecuta la validación de research y devuelve estado, fuentes, hallazgos y evidencia."""
        project_id = str(payload["projectId"])
        workspace = self._workspace(project_id=project_id, workspace_id=str(payload["workspaceId"]))
        task_id = str(payload.get("taskId") or "research_agent")
        workflow_run_id = str(payload.get("workflowRunId") or "").strip() or None
        workflow_step_id = str(payload.get("workflowStepId") or "").strip() or None
        metadata = payload.get("metadata") or {}
        thread_id = str(metadata.get("threadId") or "").strip() or None
        profile = self._ensure_profile()
        existing_job_id = str(payload.get("jobId") or metadata.get("jobId") or "").strip()
        owns_job = not existing_job_id
        if existing_job_id:
            job = self.jobs.get_job(existing_job_id)
            if job["projectId"] != project_id:
                raise ValueError("ResearchAgent job does not belong to the project.")
        else:
            job = self.jobs.create_job(
                project_id=project_id,
                kind="agent.research",
                status="running",
                workflow_run_id=workflow_run_id,
                workflow_step_id=workflow_step_id,
                payload={"workspaceId": workspace["id"], "taskId": task_id},
            )["job"]
        agent_run = self.agents.create_agent_run(
            project_id=project_id,
            agent_profile_id=profile["id"],
            task_id=task_id,
            input_payload=redact_secrets({**payload, "workspacePath": workspace["path"]}),
            output_payload={},
            job_id=job["id"],
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            status="running",
        )

        report_artifact_id = f"artifact-{uuid.uuid4()}"
        sources: list[dict[str, Any]] = []
        source_artifact_ids: list[str] = []
        conclusions: list[dict[str, Any]] = []
        technical_decisions: list[dict[str, Any]] = []
        citation_check: dict[str, Any] = {"valid": False, "uncited": [], "untrustedOnly": []}
        conflict_findings: list[dict[str, Any]] = []
        status = "research_running"
        reason = "ResearchAgent is validating official sources."
        research_run = self.research.create_run(
            project_id=project_id,
            thread_id=thread_id,
            message_id=str(metadata.get("messageId") or "").strip() or None,
            workspace_id=workspace["id"],
            job_id=job["id"],
            agent_run_id=agent_run["id"],
            task_id=task_id,
            query=str(payload.get("query") or metadata.get("query") or ""),
            status=status,
            reason=reason,
            metadata={
                "source": RESEARCH_AGENT_ID,
                "workflowRunId": workflow_run_id,
                "workflowStepId": workflow_step_id,
                "policy": metadata.get("policy") if isinstance(metadata.get("policy"), dict) else {},
                "researchPolicy": metadata.get("researchPolicy")
                if isinstance(metadata.get("researchPolicy"), dict)
                else {},
            },
        )
        try:
            sources, source_artifact_ids = self._persist_sources(
                project_id=project_id,
                thread_id=thread_id,
                task_id=task_id,
                agent_run_id=agent_run["id"],
                sources=self._sources_for_payload(payload),
                report_artifact_id=report_artifact_id,
            )
            conclusions = self._normalize_conclusions(payload.get("conclusions") or [])
            citation_check = require_citations(conclusions, sources=sources)
            claims = self._claims_with_sources(claims=payload.get("claims") or [], sources=sources)
            conflict_findings = detect_conflicts(claims)
            technical_decisions = self._normalize_technical_decisions(
                decisions=payload.get("technicalDecisions") or [], sources=sources
            )
            status, reason = self._status_from_policy(
                citation_check=citation_check, conflict_findings=conflict_findings
            )
        except (ResearchAgentValidationError, ResearchPolicyError) as error:
            status = "research_blocked"
            reason = str(error)
            citation_check = {
                "valid": False,
                "uncited": citation_check.get("uncited", []),
                "untrustedOnly": citation_check.get("untrustedOnly", []),
            }
        recommendation = self._recommendation(
            technical_decisions=technical_decisions,
            conflict_findings=conflict_findings,
            conclusions=conclusions,
            sources=sources,
            status=status,
            reason=reason,
        )
        remediation = self._remediation_for_blocker(status=status, reason=reason)
        report_payload = {
            "status": status,
            "verdict": status,
            "reason": reason,
            "sources": sources,
            "conclusions": conclusions,
            "technicalDecisions": technical_decisions,
            "recommendation": recommendation,
            "citationCheck": citation_check,
            "conflictFindings": conflict_findings,
            "discrepancies": conflict_findings,
            "remediation": remediation,
        }
        report_artifact = self._write_report_artifact(
            project_id=project_id, artifact_id=report_artifact_id, report=report_payload
        )
        artifact_ids = [*source_artifact_ids, report_artifact["id"]]
        artifact_records = artifact_records_from_ids(self.evidence, artifact_ids)
        qa_verdict = "passed" if status == "research_ready" else "blocked"
        evidence = self.evidence.create_evidence_package(
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            workflow_step_id=workflow_step_id,
            agent_id=RESEARCH_AGENT_ID,
            agent_run_id=agent_run["id"],
            job_id=job["id"],
            workspace_id=workspace["id"],
            runtime_id=f"{RESEARCH_AGENT_ID}.source_policy",
            task_id=task_id,
            test_plan="Validate research sources, citations and conflicts with deterministic source policy.",
            acceptance_checklist=[
                "Each source has URL, publisher, fetchedAt, SHA-256 hash, trustLevel and relatedArtifact.",
                "Technical web conclusions cite at least one trusted source.",
                "Conflicting claims emit explicit findings and recommendations.",
                "Sources and report are persisted as evidence artifacts with hashes.",
                "Downloaded code is never executed by ResearchAgent.",
            ],
            test_results=[
                {
                    "command": "research_agent.source_policy",
                    "status": "passed" if status == "research_ready" else "blocked",
                    "outputRef": report_artifact["id"],
                    "metadata": {
                        "sourceCount": len(sources),
                        "conflictCount": len(conflict_findings),
                        "uncited": citation_check["uncited"],
                        "untrustedOnly": citation_check["untrustedOnly"],
                    },
                }
            ],
            logs=[redact_secrets({"source": RESEARCH_AGENT_ID, "reason": reason})],
            risk_notes=[
                {
                    "severity": "low"
                    if status == "research_ready"
                    else "high"
                    if status == "research_blocked"
                    else "medium",
                    "description": reason,
                    "mitigation": (
                        "Add trusted citations for every web-based conclusion."
                        if status == "research_blocked"
                        else "No mitigation required."
                    ),
                }
            ],
            artifact_ids=artifact_ids,
            diff_summary={
                "reportArtifactId": report_artifact["id"],
                "sourceArtifactIds": source_artifact_ids,
            },
            runtime_health={
                "id": f"{RESEARCH_AGENT_ID}.source_policy",
                "status": status,
                "available": True,
                "executable": True,
                "downloadedCodeExecution": "denied",
                "sourceCount": len(sources),
                "trustedSourceCount": sum(1 for source in sources if source["trustLevel"] != UNTRUSTED),
            },
            model_calls=[],
            tool_calls=[],
            policy_decisions=[],
            approvals=self.jobs.list_action_requests(job["id"]),
            artifacts=[artifact_ref(artifact) for artifact in artifact_records],
            hashes=artifact_hashes(artifact_records),
            evidence_source="evidence_collected",
            qa_verdict=qa_verdict,
        )
        for artifact_id in artifact_ids:
            self.evidence.attach_artifact_to_evidence(
                artifact_id=artifact_id, evidence_package_id=evidence["id"]
            )
        research_run = self.research.update_run_result(
            research_run["id"],
            status=status,
            reason=reason,
            recommendation=recommendation,
            citation_check=citation_check,
            sources=sources,
            conflict_findings=conflict_findings,
            evidence_package_id=evidence["id"],
            report_artifact_id=report_artifact["id"],
            remediation=remediation,
        )
        research_findings = self._persist_findings(
            research_run_id=research_run["id"],
            project_id=project_id,
            thread_id=thread_id,
            status=status,
            reason=reason,
            citation_check=citation_check,
            conflict_findings=conflict_findings,
            technical_decisions=technical_decisions,
            recommendation=recommendation,
            remediation=remediation,
        )
        self._link_sources_to_evidence(artifact_ids=source_artifact_ids, evidence_id=evidence["id"])
        self._attach_report_to_thread(
            thread_id=thread_id,
            project_id=project_id,
            report_artifact_id=report_artifact["id"],
            report=report_payload,
        )
        agent_status = "completed" if status == "research_ready" else "blocked"
        agent_run = self.agents.update_agent_run_status(
            agent_run["id"],
            status=agent_status,
            output_payload={
                **report_payload,
                "reportArtifactId": report_artifact["id"],
                "evidence_refs": [evidence["id"], *artifact_ids],
            },
        )
        if owns_job:
            job = self.jobs.update_job_status(
                job["id"],
                status="completed" if status == "research_ready" else "failed",
                metadata={
                    "status": status,
                    "reason": reason,
                    "evidencePackageId": evidence["id"],
                    "reportArtifactId": report_artifact["id"],
                },
            )
        else:
            job = self.jobs.get_job(job["id"])
        return {
            "status": status,
            "verdict": status,
            "reason": reason,
            "contract": research_agent_contract(),
            "workspace": workspace,
            "job": job,
            "agentRun": agent_run,
            "evidencePackage": evidence,
            "sources": sources,
            "conclusions": conclusions,
            "technicalDecisions": technical_decisions,
            "recommendation": recommendation,
            "citationCheck": citation_check,
            "conflictFindings": conflict_findings,
            "discrepancies": conflict_findings,
            "reportArtifact": report_artifact,
            "researchRun": research_run,
            "researchFindings": research_findings,
        }
