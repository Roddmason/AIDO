from __future__ import annotations

from pathlib import Path

from tests_py.control_plane_fixture import ControlPlaneFixture


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_shared_event_bus_records_and_lists_events_and_audit(tmp_path: Path) -> None:
    from local_control_center.shared.event_bus import EventBus

    store = ControlPlaneFixture(cwd=tmp_path, db_path=tmp_path / "platform.sqlite")
    store.init()
    project = store.create_project(name="Events", path=tmp_path / "events", template_id="other")
    bus = EventBus(store.connection)

    event = bus.record_event(project_id=project["id"], event_type="risk.created", payload={"riskId": "risk-1"})
    audit = bus.record_audit(
        project_id=project["id"],
        action="risk.create",
        target="risk-1",
        actor="operator",
        payload={"reason": "test"},
    )

    assert event["id"].startswith("event-")
    assert event["type"] == "risk.created"
    assert bus.list_events(project_id=project["id"])[0]["payload"] == {"riskId": "risk-1"}
    assert audit["id"].startswith("audit-")
    assert bus.list_audit_events(project_id=project["id"])[0]["payload"] == {"reason": "test"}


def test_shared_event_bus_owns_events_and_audit_sql() -> None:
    event_bus = ROOT / "local_control_center" / "shared" / "event_bus.py"
    assert event_bus.exists()

    event_bus_source = event_bus.read_text(encoding="utf-8")
    jobs_repository_source = read("local_control_center/jobs_approvals/repository.py")
    overview_source = read("local_control_center/control_plane/overview.py")
    runtime_source = read("local_control_center/control_plane/runtime.py")

    assert "INSERT INTO events" in event_bus_source
    assert "INSERT INTO audit_events" in event_bus_source
    assert "INSERT INTO events" not in jobs_repository_source
    assert "INSERT INTO audit_events" not in jobs_repository_source
    assert "EventBus(connection)" in overview_source
    assert "EventBus(self.connection)" in runtime_source
