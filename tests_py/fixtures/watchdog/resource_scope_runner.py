"""Runs the real supervisor in an owned OS child. No provider or credentials."""

import json
import os
import sys
from contextlib import closing
from pathlib import Path

from local_control_center.process_supervision.context import ProcessExecutionContext, execution_scope
from local_control_center.process_supervision.service import ProcessSupervisorService, ResourceWaitError
from local_control_center.shared.db import open_sqlite_connection

db, mode, destination = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
os.environ["AIDO_DIAGNOSTICS_DIR"] = str(destination.parent / "diagnostics")
with closing(open_sqlite_connection(db)) as connection:
    row = connection.execute(
        "SELECT resource_lease_id FROM managed_processes WHERE execution_id='scope-parent'"
    ).fetchone()
context = ProcessExecutionContext(
    db_path=db,
    execution_id="scope-child",
    resource_lease_id=row[0] if mode in {"borrow", "capture"} else None,
)
if mode == "capture":
    import hashlib

    from local_control_center.shared import diagnostics

    os.environ["AIDO_DIAGNOSTICS_DIR"] = str(destination.parent / "diagnostics")
    diagnostics.activate_attempt(
        destination.parent / "diagnostics",
        "scope-child",
        ttl_seconds=120,
        native_collector=os.environ["AIDO_TEST_PROCDUMP"],
        executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
    )
with execution_scope(context):
    service = ProcessSupervisorService(db_path=db)
    try:
        child = service.start(
            argv=[sys.executable, "-c", "import time; time.sleep(.2)"],
            cwd=Path.cwd(),
            workload_class="agent_cli" if mode == "independent" else "qa_light",
        )
    except ResourceWaitError as error:
        destination.write_text(json.dumps({"rejected": True, "reason": str(error)}), encoding="utf-8")
    else:
        receipt = child.containment_evidence
        child.process.wait(timeout=10)
        service.complete(child, exit_code=child.process.returncode)
        destination.write_text(json.dumps({"rejected": False, "containment": receipt}), encoding="utf-8")
