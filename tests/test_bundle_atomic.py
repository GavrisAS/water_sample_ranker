"""Real processes, faults and corruption: existence alone never implies COMPLETE."""

import json
import multiprocessing
from pathlib import Path

import pytest

from tests.test_stage_b_operations import DAY
from water_sample_ranker import bundle, service


def publish_worker(directory: str, label: str) -> None:
    try:
        bundle.publish_json_exclusive(Path(directory) / "bundle-complete.json", {"winner": label})
    except FileExistsError:
        pass


def test_processes_race_without_clobber(tmp_path):
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(target=publish_worker, args=(str(tmp_path), str(i))) for i in range(4)
    ]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(30)
        assert worker.exitcode == 0
    assert json.loads((tmp_path / "bundle-complete.json").read_text(encoding="utf-8"))[
        "winner"
    ] in {
        "0",
        "1",
        "2",
        "3",
    }
    assert len(list(tmp_path.iterdir())) == 1


@pytest.mark.parametrize("phase", ["serialize", "mid_write", "fsync", "before_link", "after_link"])
def test_atomic_faults_leave_no_marker_or_owned_temporary(tmp_path, monkeypatch, phase):
    marker = tmp_path / "bundle-complete.json"

    def fail(*args, **kwargs):
        assert not marker.exists()
        raise OSError("SYNTHETIC_FAULT")

    if phase == "serialize":
        monkeypatch.setattr(bundle.json, "dumps", fail)
    elif phase == "mid_write":
        original = bundle.os.fdopen

        class BrokenStream:
            def __init__(self, *args):
                self.stream = original(*args)

            def __enter__(self):
                return self

            def write(self, data):
                self.stream.write(data[:5])
                self.stream.flush()
                fail()

            def __exit__(self, *args):
                self.stream.close()

        monkeypatch.setattr(bundle.os, "fdopen", BrokenStream)
    elif phase == "fsync":
        monkeypatch.setattr(bundle.os, "fsync", fail)
    elif phase == "before_link":
        monkeypatch.setattr(bundle.os, "link", fail)
    else:
        original_link = bundle.os.link

        def linked_then_failed(source, target):
            original_link(source, target)
            assert json.loads(marker.read_text(encoding="utf-8")) == {"complete": True}
            raise OSError("SYNTHETIC_AFTER_LINK")

        monkeypatch.setattr(bundle.os, "link", linked_then_failed)
    with pytest.raises(OSError):
        bundle.publish_json_exclusive(marker, {"complete": True})
    assert not list(tmp_path.iterdir())


def test_collision_preserves_marker_and_crash_leftovers(tmp_path):
    marker = tmp_path / "bundle-complete.json"
    marker.write_bytes(b"USER_OBJECT")
    leftover = tmp_path / ".bundle-crashed.tmp"
    leftover.write_bytes(b"CRASH_LEFTOVER")
    with pytest.raises(FileExistsError):
        bundle.publish_json_exclusive(marker, {"complete": True})
    assert marker.read_bytes() == b"USER_OBJECT"
    assert leftover.read_bytes() == b"CRASH_LEFTOVER"
    assert set(tmp_path.iterdir()) == {marker, leftover}
    assert bundle.verify_bundle(tmp_path)["status"] == "INCOMPLETE"


def test_cleanup_preserves_replaced_user_object(tmp_path):
    target = tmp_path / "output"
    owned = bundle.publish_json_exclusive(target, {"own": True})
    # Keep original inode alive, making identity reuse impossible in this check.
    target.rename(tmp_path / "original")
    target.write_bytes(b"USER_REPLACEMENT")
    owned.remove()
    assert target.read_bytes() == b"USER_REPLACEMENT"


@pytest.mark.parametrize(
    "fault", ["truncated", "duplicate", "checksum", "missing", "binding", "names", "count"]
)
def test_complete_requires_valid_bound_json_and_every_payload(synthetic, fault):
    directory = synthetic.root / "bundle"
    results = service.run_update_all(synthetic, as_of=DAY, output_dir=directory)
    assert results[0].output is not None
    assert bundle.verify_bundle(directory)["status"] == "COMPLETE"
    assert bundle.verify_bundle(directory, expected_date="2020-01-01")["status"] == "INCOMPLETE"
    marker = directory / "bundle-complete.json"
    raw = json.loads(marker.read_text(encoding="utf-8"))
    if fault == "truncated":
        marker.write_text('{"status":"COMPLETE",')
    elif fault == "duplicate":
        marker.write_text(
            marker.read_text(encoding="utf-8").replace('"schema": 1', '"schema": 1, "schema": 1')
        )
    elif fault == "checksum":
        results[0].output.write_bytes(b"BROKEN_XLSX")
    elif fault == "missing":
        results[0].output.with_suffix(".log").unlink()
    else:
        if fault == "binding":
            raw["version"] = "0.1.999"
        elif fault == "names":
            raw["outputs"][0]["workbook"]["name"] = "../foreign.xlsx"
        else:
            raw["outputs"].pop()
        marker.write_text(json.dumps(raw))
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    assert bundle.verify_bundle(directory)["status"] == "INCOMPLETE"
    with pytest.raises(service.ServiceError, match="OUTPUT_EXISTS"):
        service.run_update_all(synthetic, as_of=DAY, output_dir=directory)
    assert before == {p.name: p.read_bytes() for p in directory.iterdir()}


def test_service_failure_after_publication_rolls_back_own_files(synthetic):
    directory = synthetic.root / "bundle"

    def fail(update):
        if update.percent == 100:
            assert bundle.verify_bundle(directory)["status"] == "COMPLETE"
            raise OSError("SYNTHETIC_AFTER_PUBLICATION")

    with pytest.raises(OSError):
        service.run_update_all(synthetic, as_of=DAY, output_dir=directory, progress=fail)
    assert not list(directory.iterdir())


def test_marker_race_rolls_back_only_losing_outputs(synthetic, monkeypatch):
    original = service.publish_json_exclusive
    directory = synthetic.root / "bundle"

    def race(path, payload):
        path.write_bytes(b"WINNING_USER_MARKER")
        return original(path, payload)

    monkeypatch.setattr(service, "publish_json_exclusive", race)
    with pytest.raises(FileExistsError):
        service.run_update_all(synthetic, as_of=DAY, output_dir=directory)
    assert [p.name for p in directory.iterdir()] == ["bundle-complete.json"]
    assert (directory / "bundle-complete.json").read_bytes() == b"WINNING_USER_MARKER"
