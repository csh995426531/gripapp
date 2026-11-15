from __future__ import annotations

from pathlib import Path

from grip.db import connect
from grip.service import GripService
from grip.vault import Vault


def test_audit_integrity_ok_after_events(service: GripService) -> None:
    t = service.tasks.create(resource_name="r1", action="run_command", command="echo hi")
    service.tasks.set_running(t.id)
    service.tasks.finish(t.id, status="success", exit_code=0, summary="ok")
    assert service.tasks.audit_integrity() == "ok"
    board = service.board()
    assert board["security"]["audit_integrity"] == "ok"
    assert board["security"]["vault_backend"] in {"file", "keyring"}


def test_audit_integrity_broken_after_wipe(service: GripService) -> None:
    t = service.tasks.create(resource_name="r1", action="run_command", command="uptime")
    service.tasks.add_event(t.id, "note", "keep chain")
    assert service.tasks.audit_integrity() == "ok"

    # Quiet wipe of events while leaving sealed head — simulates AI truncate.
    with connect(service._db_path) as conn:
        conn.execute("DELETE FROM task_events")
        conn.commit()

    assert service.tasks.audit_integrity() == "broken"
    assert service.board()["security"]["audit_integrity"] == "broken"


def test_audit_integrity_broken_on_tampered_detail(
    grip_env: Path, service: GripService
) -> None:
    t = service.tasks.create(resource_name="r1", action="probe", actor="panel")
    assert service.tasks.audit_integrity() == "ok"

    with connect(service._db_path) as conn:
        conn.execute(
            "UPDATE task_events SET detail=? WHERE task_id=?",
            ("tampered", t.id),
        )
        conn.commit()

    assert service.tasks.audit_integrity() == "broken"


def test_export_includes_integrity(service: GripService) -> None:
    service.tasks.create(resource_name="r1", action="run_command", command="x")
    out = service.export_audit()
    assert out["audit_integrity"] == "ok"
    assert out["vault_backend"] == "file"
