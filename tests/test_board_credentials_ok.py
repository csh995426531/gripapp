from __future__ import annotations

from grip.service import GripService


def test_board_marks_missing_ssh_credentials(service: GripService, tmp_path) -> None:
    service.resources.add_ssh(
        name="box",
        host="127.0.0.1",
        user="u",
        password="secret",
        port=22,
    )
    board = service.board()
    row = next(r for r in board["resources"] if r["name"] == "box")
    assert row["labels"].get("credentials_ok") is True

    service.vault.delete("ssh:box")
    board2 = service.board()
    row2 = next(r for r in board2["resources"] if r["name"] == "box")
    assert row2["labels"].get("credentials_ok") is False
