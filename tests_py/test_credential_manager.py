from __future__ import annotations

from pathlib import Path

import pytest

from local_control_center.credentials.backends import CredentialBackendError, EnvBackend
from local_control_center.credentials.manager import CredentialError, CredentialManager
from local_control_center.credentials.repository import CredentialRepository
from local_control_center.shared.db import open_sqlite_connection
from local_control_center.shared.migrations import initialize_platform_schema

SECRET = "unit-test-credential-material-0123456789"
CREDENTIAL_TABLES = {"credential_refs", "credential_audit"}
SECRET_COLUMN_TOKENS = ("value", "secret", "password", "plaintext", "apikey")


class _DictBackend:
    """In-process credential store implementing the CredentialBackend protocol for tests."""

    backend_kind = "memory"

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def write(self, locator: str, value: str) -> None:
        self.store[locator] = value

    def read(self, locator: str) -> str | None:
        return self.store.get(locator)

    def remove(self, locator: str) -> None:
        self.store.pop(locator, None)


def _manager(connection) -> tuple[CredentialManager, CredentialRepository, _DictBackend]:
    repository = CredentialRepository(connection)
    backend = _DictBackend()
    manager = CredentialManager(
        repository,
        backends={"memory": backend, "env": EnvBackend({"BOOT_TOKEN": SECRET})},
        default_backend="memory",
    )
    return manager, repository, backend


def test_credential_schema_has_no_secret_column_and_is_idempotent(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        initialize_platform_schema(connection)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        }
        phase22_rows = connection.execute(
            "SELECT COUNT(*) AS total FROM schema_migrations WHERE version = 22"
        ).fetchone()["total"]
        ref_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(credential_refs)").fetchall()
        }

    assert tables >= CREDENTIAL_TABLES
    assert phase22_rows == 1
    # SQLite stores refs/fingerprints/audit, never the secret value.
    assert not any(token in column.lower() for column in ref_columns for token in SECRET_COLUMN_TOKENS)


def test_create_persists_ref_and_fingerprint_without_returning_or_storing_value(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager, repository, backend = _manager(connection)

        created = manager.create_credential(
            name="codex/personal",
            value=SECRET,
            locator="AIDO/codex",
            auth_mode="subscription",
            actor="rodd",
        )
        assert created["backendKind"] == "memory"
        assert created["status"] == "active"
        assert created["hasFingerprint"] is True
        # The API never returns the value, the fingerprint, or the salt.
        assert "fingerprint" not in created and "salt" not in created
        assert SECRET not in str(created)

        # The secret lives in the backend, not in SQLite.
        assert backend.read("AIDO/codex") == SECRET
        row = connection.execute(
            "SELECT * FROM credential_refs WHERE name = ?", ("codex/personal",)
        ).fetchone()
        assert row["fingerprint"] and len(row["fingerprint"]) == 64  # HMAC-SHA256 hex digest
        assert SECRET not in str(tuple(row))  # no column holds the secret

        audit = repository.list_audit()
        assert audit[-1]["action"] == "create" and audit[-1]["outcome"] == "success"
        assert SECRET not in str(audit)

        with pytest.raises(CredentialError, match="already exists"):
            manager.create_credential(name="codex/personal", value="other", locator="AIDO/codex")


def test_validate_detects_match_mismatch_and_missing(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager, repository, backend = _manager(connection)
        manager.create_credential(name="c", value=SECRET, locator="loc")

        ok = manager.validate("c", actor="rodd")
        assert ok == {"name": "c", "present": True, "valid": True, "fingerprintMatches": True}
        assert SECRET not in str(ok)

        # Tampering the backend value is detected by the fingerprint.
        backend.write("loc", "tampered-value")
        mismatch = manager.validate("c")
        assert mismatch["valid"] is False and mismatch["fingerprintMatches"] is False

        # Removing the secret marks the credential missing.
        backend.remove("loc")
        missing = manager.validate("c")
        assert missing["present"] is False
        assert repository.get_credential_by_name("c")["status"] == "missing"
        assert {entry["action"] for entry in repository.list_audit()} == {"create", "validate"}


def test_rotate_updates_fingerprint_and_revalidates(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager, repository, backend = _manager(connection)
        manager.create_credential(name="c", value=SECRET, locator="loc")
        first = connection.execute("SELECT fingerprint FROM credential_refs WHERE name = 'c'").fetchone()[0]

        rotated = manager.rotate("c", "rotated-secret-987", actor="rodd")
        assert rotated["rotatedAt"]
        assert SECRET not in str(rotated) and "rotated-secret-987" not in str(rotated)
        second = connection.execute("SELECT fingerprint FROM credential_refs WHERE name = 'c'").fetchone()[0]
        assert second != first
        assert backend.read("loc") == "rotated-secret-987"
        assert manager.validate("c")["valid"] is True
        assert {entry["action"] for entry in repository.list_audit()} >= {"create", "rotate"}


def test_delete_removes_backend_secret_and_ref_but_keeps_audit(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager, repository, backend = _manager(connection)
        manager.create_credential(name="c", value=SECRET, locator="loc")

        result = manager.delete("c", actor="rodd")
        assert result == {"name": "c", "deleted": True}
        assert backend.read("loc") is None
        assert repository.find_credential_by_name("c") is None
        actions = [entry["action"] for entry in repository.list_audit()]
        assert actions[0] == "create" and actions[-1] == "delete"  # audit trail survives deletion


def test_list_metadata_never_exposes_secrets(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager, _repository, _backend = _manager(connection)
        manager.create_credential(name="a", value=SECRET, locator="la")
        manager.create_credential(name="b", value="another-secret", locator="lb", backend="memory")

        items = manager.list_metadata()
        assert {item["name"] for item in items} == {"a", "b"}
        for item in items:
            assert not any(key in {"fingerprint", "salt", "value"} for key in item)
        assert SECRET not in str(items) and "another-secret" not in str(items)


class _FailingRemoveBackend:
    """Writable backend whose removal always fails — to exercise the orphan-on-delete guard."""

    backend_kind = "failremove"

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def write(self, locator: str, value: str) -> None:
        self.store[locator] = value

    def read(self, locator: str) -> str | None:
        return self.store.get(locator)

    def remove(self, locator: str) -> None:
        raise CredentialBackendError("removal not permitted")


class _FailingCreateRepository(CredentialRepository):
    def create_credential_ref(self, body: dict) -> dict:
        raise RuntimeError("simulated persistence failure")


def test_delete_with_readonly_backend_removes_ref_and_audits_success(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        manager, repository, _backend = _manager(connection)
        # A bootstrap ref pointing at an env var (created out-of-band; env backend is read-only).
        repository.create_credential_ref(
            {"name": "boot", "backendKind": "env", "locator": "BOOT_TOKEN", "fingerprint": "x", "salt": "00"}
        )

        result = manager.delete("boot", actor="ci")
        assert result == {"name": "boot", "deleted": True}
        assert repository.find_credential_by_name("boot") is None
        assert repository.list_audit()[-1]["outcome"] == "success"


def test_delete_keeps_ref_and_raises_when_writable_backend_removal_fails(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = CredentialRepository(connection)
        backend = _FailingRemoveBackend()
        manager = CredentialManager(
            repository, backends={"failremove": backend}, default_backend="failremove"
        )
        manager.create_credential(name="c", value=SECRET, locator="loc")

        with pytest.raises(CredentialError, match="Backend removal failed"):
            manager.delete("c")
        # The orphaned secret is still tracked (ref kept) and the failure is audited.
        assert repository.find_credential_by_name("c") is not None
        assert repository.list_audit()[-1]["outcome"] == "failure"


def test_list_audit_is_stable_by_insertion_order_on_timestamp_ties(tmp_path: Path) -> None:
    # Two audit rows in the SAME millisecond must order by insertion, never by their random uuid id;
    # otherwise list_audit()[-1] (the latest action) is non-deterministic. Regression for a flake.
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = CredentialRepository(connection)
        same_ts = "2026-01-01T00:00:00.000Z"

        def _insert(audit_id: str, outcome: str) -> None:
            connection.execute(
                """
                INSERT INTO credential_audit
                    (id, credential_id, name, action, outcome, actor, backend_kind, detail, created_at)
                VALUES (?, 'cred-1', 'c', 'delete', ?, 'op', 'memory', '', ?)
                """,
                (audit_id, outcome, same_ts),
            )

        # The chronologically later row is given a lexically smaller id than the earlier one.
        _insert("credential-audit-zzz", "success")
        _insert("credential-audit-aaa", "failure")

        assert repository.list_audit()[-1]["outcome"] == "failure"
        assert repository.list_audit(credential_id="cred-1")[-1]["outcome"] == "failure"


def test_create_rolls_back_backend_secret_when_ref_persistence_fails(tmp_path: Path) -> None:
    with open_sqlite_connection(tmp_path / "platform.sqlite") as connection:
        initialize_platform_schema(connection)
        repository = _FailingCreateRepository(connection)
        backend = _DictBackend()
        manager = CredentialManager(repository, backends={"memory": backend}, default_backend="memory")

        with pytest.raises(CredentialError, match="persist credential reference"):
            manager.create_credential(name="c", value=SECRET, locator="loc")
        # No orphan: the secret was rolled back out of the backend, and the failure was audited.
        assert backend.read("loc") is None
        assert repository.find_credential_by_name("c") is None
        assert repository.list_audit()[-1] == {
            **repository.list_audit()[-1],
            "action": "create",
            "outcome": "failure",
        }


def test_env_backend_is_read_only_bootstrap(tmp_path: Path) -> None:
    backend = EnvBackend({"BOOT_TOKEN": SECRET})
    assert backend.read("BOOT_TOKEN") == SECRET
    assert backend.read("MISSING") is None
    with pytest.raises(CredentialBackendError, match="read-only"):
        backend.write("BOOT_TOKEN", "x")
    with pytest.raises(CredentialBackendError, match="cannot delete"):
        backend.remove("BOOT_TOKEN")
