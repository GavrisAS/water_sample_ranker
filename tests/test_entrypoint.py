"""Windowed CLI has explicit UTF-8 diagnostics and never clobbers a chosen log."""

import sys

import pytest

from water_sample_ranker import cli
from water_sample_ranker.__main__ import entrypoint


def test_windowed_cli_routes_streams_without_a_console(tmp_path, monkeypatch):
    path = tmp_path / "журнал.log"

    def command(args):
        assert args == ["validate"]
        print("Синтетическая проверка")
        print("UNKNOWN сохраняется", file=sys.stderr)
        return 0

    monkeypatch.setattr(cli, "main", command)
    with monkeypatch.context() as patch:
        patch.setattr(sys, "stdout", None)
        patch.setattr(sys, "stderr", None)
        assert entrypoint(["--cli", "--cli-log", str(path), "validate"]) == 0
    assert path.read_bytes() == "Синтетическая проверка\nUNKNOWN сохраняется\n".encode()


def test_cli_log_collision_preserves_user_file_and_does_not_run(tmp_path, monkeypatch):
    path = tmp_path / "existing.log"
    path.write_bytes(b"USER_EXISTING_LOG")
    calls = []
    monkeypatch.setattr(cli, "main", lambda args: calls.append(args))
    with pytest.raises(FileExistsError):
        entrypoint(["--cli", "--cli-log", str(path), "validate"])
    assert path.read_bytes() == b"USER_EXISTING_LOG" and not calls
