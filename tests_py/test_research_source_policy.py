from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.evidence.repository import EvidenceRepository
from local_control_center.projects.repository import ProjectsRepository
from local_control_center.research.source_log import list_research_sources, persist_source
from local_control_center.research.source_policy import (
    TRUST_LEVELS,
    ResearchPolicyError,
    build_source_record,
    classify_source,
    detect_conflicts,
    require_citations,
    trust_rank,
)
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema


def test_trust_hierarchy_is_ordered_highest_first() -> None:
    assert TRUST_LEVELS[0] == "official_documentation"
    assert TRUST_LEVELS[-1] == "reputable_secondary"
    assert (
        trust_rank("official_documentation") < trust_rank("standard_rfc") < trust_rank("reputable_secondary")
    )
    assert trust_rank("untrusted") == len(TRUST_LEVELS)  # outside the policy ranks last


def test_classification_uses_allowlists_not_spoofable_prefixes() -> None:
    assert classify_source("https://www.rfc-editor.org/rfc/rfc9110") == "standard_rfc"
    assert classify_source("https://docs.python.org/3/library/asyncio.html") == "official_documentation"
    assert classify_source("https://pypi.org/project/requests/") == "official_repository"
    assert classify_source("https://arxiv.org/abs/2401.00001") == "primary_research"
    # Security: a `docs.` prefix on an arbitrary host is NOT official documentation.
    assert classify_source("https://docs.attacker.example/guide") == "untrusted"
    # A user-namespaced code host is reputable_secondary by default, not an official repository.
    assert classify_source("https://github.com/some-user/some-fork") == "reputable_secondary"
    assert classify_source("https://an-unknown-blog.example/post") == "untrusted"


def test_source_record_captures_provenance_and_hashes_content() -> None:
    record = build_source_record(
        url="https://docs.python.org/3/library/asyncio.html",
        publisher="Python Software Foundation",
        content="asyncio provides an event loop.",
        fetched_at="2026-06-24T00:00:00.000Z",
        related_artifact="artifact-conclusion-1",
    )
    assert record["trustLevel"] == "official_documentation"  # classified from the allowlist
    assert record["publisher"] == "Python Software Foundation"
    assert record["fetchedAt"] == "2026-06-24T00:00:00.000Z"
    assert record["relatedArtifact"] == "artifact-conclusion-1"
    assert len(record["hash"]) == 64  # sha256 hex
    # An explicit trust level (asserted by the agent after verifying the source) overrides the heuristic.
    asserted = build_source_record(
        url="https://github.com/python/cpython",
        publisher="Python",
        content="cpython source",
        fetched_at="2026-06-24T00:00:00.000Z",
        trust_level="official_repository",
    )
    assert asserted["trustLevel"] == "official_repository"


def test_source_record_rejects_empty_content_and_redacts_credential_urls() -> None:
    with pytest.raises(ResearchPolicyError, match="Empty source content"):
        build_source_record(url="https://x", publisher="p", content="   ", fetched_at="t")
    with pytest.raises(ResearchPolicyError, match="Unknown trust level"):
        build_source_record(
            url="https://x", publisher="p", content="body", fetched_at="t", trust_level="gospel"
        )
    leaked = build_source_record(
        url="https://user:s3cr3t@api.example.com/data",
        publisher="p",
        content="body",
        fetched_at="t",
        trust_level="untrusted",
    )
    assert "s3cr3t" not in leaked["url"]


def test_web_conclusions_must_cite_a_trusted_source() -> None:
    sources = [
        {"url": "https://docs.python.org/x", "trustLevel": "official_documentation"},
        {"url": "https://blog.example/y", "trustLevel": "untrusted"},
    ]
    conclusions = [
        {"statement": "Cited from docs", "citations": ["https://docs.python.org/x"], "webBased": True},
        {"statement": "No citation", "citations": [], "webBased": True},
        {"statement": "Only an untrusted blog", "citations": ["https://blog.example/y"], "webBased": True},
        {"statement": "Reasoning, not web", "citations": [], "webBased": False},
    ]
    result = require_citations(conclusions, sources=sources)
    assert result["valid"] is False
    assert result["uncited"] == ["No citation"]
    assert result["untrustedOnly"] == ["Only an untrusted blog"]


def test_conflicting_sources_produce_a_finding_resolved_by_trust() -> None:
    claims = [
        {
            "topic": "max_upload",
            "value": "10MB",
            "source": {
                "trustLevel": "official_documentation",
                "url": "d",
                "fetchedAt": "2026-01-01T00:00:00.000Z",
            },
        },
        {
            "topic": "max_upload",
            "value": "5MB",
            "source": {
                "trustLevel": "reputable_secondary",
                "url": "s",
                "fetchedAt": "2025-01-01T00:00:00.000Z",
            },
        },
        {
            "topic": "stable_release",
            "value": "3.13",
            "source": {
                "trustLevel": "official_repository",
                "url": "r",
                "fetchedAt": "2026-01-01T00:00:00.000Z",
            },
        },
    ]
    findings = detect_conflicts(claims)
    assert len(findings) == 1  # only max_upload conflicts; stable_release has a single value
    finding = findings[0]
    assert finding["topic"] == "max_upload"
    assert finding["conflictingValues"] == ["10MB", "5MB"]
    assert finding["recommendation"]["needsManualReview"] is False
    assert finding["recommendation"]["value"] == "10MB"  # the official-documentation source wins
    assert finding["recommendation"]["basis"] == "official_documentation"
    assert finding["claims"][0]["fetchedAt"] == "2026-01-01T00:00:00.000Z"


def test_top_trust_sources_in_conflict_escalate_to_manual_review() -> None:
    claims = [
        {
            "topic": "t",
            "value": "A",
            "source": {
                "trustLevel": "official_documentation",
                "url": "d1",
                "fetchedAt": "2026-01-01T00:00:00.000Z",
            },
        },
        {
            "topic": "t",
            "value": "B",
            "source": {
                "trustLevel": "official_documentation",
                "url": "d2",
                "fetchedAt": "2026-01-01T00:00:00.000Z",
            },
        },
    ]
    finding = detect_conflicts(claims)[0]
    assert finding["recommendation"]["needsManualReview"] is True


def test_stale_high_trust_source_is_flagged() -> None:
    claims = [
        {
            "topic": "t",
            "value": "old",
            "source": {
                "trustLevel": "official_documentation",
                "url": "d",
                "fetchedAt": "2019-01-01T00:00:00.000Z",
            },
        },
        {
            "topic": "t",
            "value": "new",
            "source": {
                "trustLevel": "reputable_secondary",
                "url": "s",
                "fetchedAt": "2026-01-01T00:00:00.000Z",
            },
        },
    ]
    rec = detect_conflicts(claims)[0]["recommendation"]
    assert rec["value"] == "old"  # highest trust still wins
    assert rec["staleWarning"] is True  # but it is older than a conflicting source


def test_persisting_a_source_records_full_provenance_as_an_artifact(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        project = ProjectsRepository(connection).create_project(
            name="R", path=tmp_path / "r", template_id="other"
        )
        evidence = EvidenceRepository(connection)
        record = build_source_record(
            url="https://www.rfc-editor.org/rfc/rfc9110",
            publisher="IETF",
            content="HTTP Semantics",
            fetched_at="2026-06-24T00:00:00.000Z",
            related_artifact="artifact-conclusion-1",
        )

        artifact = persist_source(
            evidence, root=tmp_path, project_id=project["id"], record=record, content="HTTP Semantics"
        )
        assert artifact["kind"] == "research_source"
        assert artifact["hash"] == record["hash"]
        assert Path(artifact["path"]).exists()

        stored = list_research_sources(connection, project["id"])
        assert len(stored) == 1
        source = stored[0]
        assert source["url"] == "https://www.rfc-editor.org/rfc/rfc9110"
        assert source["publisher"] == "IETF"
        assert source["fetchedAt"] == "2026-06-24T00:00:00.000Z"
        assert source["trustLevel"] == "standard_rfc"
        assert source["hash"] == record["hash"]
        assert source["relatedArtifact"] == "artifact-conclusion-1"


def test_research_sources_table_exposes_canonical_source_contract_columns(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)

        columns = {row["name"] for row in connection.execute("PRAGMA table_info(research_sources)")}

        assert {
            "url",
            "title",
            "publisher",
            "source_type",
            "fetched_at",
            "content_hash",
            "trust_level",
            "related_thread_id",
            "related_task_id",
        } <= columns
