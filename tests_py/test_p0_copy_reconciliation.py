"""Copy-only repair guards and atomic provenance for the authorized P0 orphan."""

import json
from contextlib import closing

import pytest

from local_control_center.quality import maintenance
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

OBSERVATION = "provider-limit-observation-abbee835-9c68-4645-a4ec-6adbfe37c21a"


@pytest.fixture
def orphan_copy(tmp_path):
    original = tmp_path / "original.sqlite"
    copy = tmp_path / "copy.sqlite"
    with closing(open_sqlite_connection(original)) as conn:
        initialize_platform_schema(conn)
        conn.execute("PRAGMA foreign_keys=OFF")  # Corrupt fixture only, never the operational DB.
        conn.execute(
            "INSERT INTO provider_limit_observations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                OBSERVATION,
                "codex_cli:*",
                "codex_cli",
                "*",
                "rate_limited",
                "2026-07-23",
                None,
                '{"keep":"intact"}',
            ),
        )
        conn.execute("PRAGMA foreign_keys=ON")
        expected = dict(
            conn.execute("SELECT * FROM provider_limit_observations WHERE id=?", (OBSERVATION,)).fetchone()
        )
        with closing(open_sqlite_connection(copy)) as writer:
            conn.backup(writer)
    return original, copy, expected


def repair(original, copy, expected):
    operation = getattr(maintenance, "reconcile_p0_observation_copy", None)
    assert callable(operation), "Missing guarded, audited copy-only reconciliation"
    return operation(copy, original=original, expected_row=expected, evidence="owner-authorized-fixture")


def test_repair_preserves_history_and_is_idempotent(orphan_copy):
    original, copy, expected = orphan_copy
    first = repair(original, copy, expected)
    second = repair(original, copy, expected)
    assert first["applied"] is True and second["applied"] is False
    assert first["auditId"] == second["auditId"]
    with closing(open_sqlite_connection(copy)) as conn:
        row = dict(
            conn.execute("SELECT * FROM provider_limit_observations WHERE id=?", (OBSERVATION,)).fetchone()
        )
        assert row == {**expected, "limit_id": None}
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        audits = conn.execute(
            "SELECT payload FROM audit_events WHERE action='data.p0.observation_reconciled'"
        ).fetchall()
        assert len(audits) == 1
        payload = json.loads(audits[0][0])
        assert payload["originalLimitId"] == "codex_cli:*"
        assert payload["historicalPolicyStatus"] == "unresolved"
        assert payload["before"] == expected
    with closing(open_sqlite_connection(original)) as conn:
        assert conn.execute("SELECT limit_id FROM provider_limit_observations").fetchone()[0] == "codex_cli:*"


def test_repair_refuses_original_and_hardlink(orphan_copy, tmp_path):
    original, _, expected = orphan_copy
    alias = tmp_path / "alias.sqlite"
    alias.hardlink_to(original)
    for target in [original, alias]:
        with pytest.raises(ValueError, match="original"):
            repair(original, target, expected)


def test_repair_refuses_changed_content(orphan_copy):
    original, copy, expected = orphan_copy
    with closing(open_sqlite_connection(copy)) as conn:
        conn.execute("UPDATE provider_limit_observations SET metadata_json='{}' WHERE id=?", (OBSERVATION,))
    with pytest.raises(ValueError, match="content"):
        repair(original, copy, expected)


def test_audit_failure_rolls_back_association(orphan_copy):
    original, copy, expected = orphan_copy
    with closing(open_sqlite_connection(copy)) as conn:
        conn.execute(
            "CREATE TRIGGER fail_audit BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT, 'audit unavailable'); END"
        )
    with pytest.raises(Exception, match="audit unavailable"):
        repair(original, copy, expected)
    with closing(open_sqlite_connection(copy)) as conn:
        assert conn.execute("SELECT limit_id FROM provider_limit_observations").fetchone()[0] == "codex_cli:*"


def test_null_without_matching_audit_is_not_claimed_repaired(orphan_copy):
    original, copy, expected = orphan_copy
    with closing(open_sqlite_connection(copy)) as conn:
        conn.execute("UPDATE provider_limit_observations SET limit_id=NULL WHERE id=?", (OBSERVATION,))
    with pytest.raises(ValueError, match="audit"):
        repair(original, copy, expected)
