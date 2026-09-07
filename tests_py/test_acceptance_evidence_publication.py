"""Immutable acceptance receipts, including competing publishers.

@author Rodrigo Mason
"""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from tests_py.operational_acceptance_support import evidence, save


def test_existing_receipt_is_never_replaced(tmp_path):
    destination = tmp_path / "historical.json"
    original = b'{"status":"FAIL","historical":true}\n'
    destination.write_bytes(original)
    with pytest.raises(FileExistsError):
        save(destination, {"status": "PASS"})
    assert destination.read_bytes() == original


def test_concurrent_publication_has_one_winner_and_explicit_collision(tmp_path):
    destination = tmp_path / "receipt.json"
    barrier = Barrier(2)

    def publish(value):
        barrier.wait(timeout=5)
        try:
            save(destination, {"publisher": value})
        except FileExistsError:
            return "collision"
        return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(publish, ["first", "second"]))
    assert results.count("collision") == 1
    winner = next(result for result in results if result != "collision")
    assert json.loads(destination.read_text(encoding="utf-8")) == {"publisher": winner}
    assert list(tmp_path.iterdir()) == [destination]


def test_each_evidence_publication_retains_its_original_result(tmp_path, monkeypatch):
    monkeypatch.setenv("AIDO_ACCEPTANCE_EVIDENCE", str(tmp_path))
    evidence("attempt", {"status": "FAIL"})
    before = {path: path.read_bytes() for path in tmp_path.glob("*.json")}
    evidence("attempt", {"status": "PASS"})
    assert len(list(tmp_path.glob("*.json"))) == 2
    assert all(path.read_bytes() == content for path, content in before.items())


def test_serialization_failure_does_not_publish_a_receipt(tmp_path):
    destination = tmp_path / "invalid.json"
    with pytest.raises(TypeError):
        save(destination, {"invalid": object()})
    assert not destination.exists()
    assert not list(tmp_path.iterdir())


def test_evidence_identity_collision_is_explicit_and_preserves_failure(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tests_py import operational_acceptance_support as support

    monkeypatch.setenv("AIDO_ACCEPTANCE_EVIDENCE", str(tmp_path))
    monkeypatch.setattr(support.uuid, "uuid4", lambda: SimpleNamespace(hex="controlled-collision"))
    path = evidence("collision", {"status": "FAIL"})
    original = path.read_bytes()
    with pytest.raises(FileExistsError):
        evidence("collision", {"status": "PASS"})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]
