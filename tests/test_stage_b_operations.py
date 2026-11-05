"""Transactional bundle, source snapshots and compatible evidence schema."""

import json
import os
from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from water_sample_ranker import service
from water_sample_ranker.cli import main
from water_sample_ranker.config import AppConfig
from water_sample_ranker.excel.layout import DATA_HEADERS, DATA_HEADERS_V2
from water_sample_ranker.source_manifest import fingerprint

DAY = date(2026, 11, 5)


def test_batch_one_lha_read_fixed_snapshot_and_output_rows(synthetic, monkeypatch):
    reads = []
    original = service.read_lha

    def read(path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(service, "read_lha", read)
    target = synthetic.root / "bundle"
    progress = []
    results = service.run_update_all(
        synthetic, output_dir=target, as_of=DAY, progress=progress.append
    )
    assert len(reads) == 1
    assert len(results) == 3
    assert [len(r.rows) for r in results] == [105, 2, 2]
    assert all(r.report["source_manifest"] == results[0].report["source_manifest"] for r in results)
    assert all(r.report["calculation_date"] == DAY.isoformat() for r in results)
    assert progress[-1].percent == 100
    assert all(p.percent < 100 for p in progress[:-1])
    marker = json.loads((target / "bundle-complete.json").read_text(encoding="utf-8"))
    assert marker["status"] == "COMPLETE" and len(marker["outputs"]) == 3
    for r in results:
        assert r.output
        assert r.report["output"]["sha256"] == fingerprint(r.output)["sha256"]
        assert all(
            row.identity.fund.normalized_field == r.report["selected_field"] for row in r.rows
        )
        w = load_workbook(r.output, read_only=False)
        assert w["Данные"].max_row - 6 == len(r.rows)
        headers = tuple(c.value for c in w["Данные"][6])
        assert headers == DATA_HEADERS_V2 and headers[: len(DATA_HEADERS)] == DATA_HEADERS
        assert "не баллы" in headers[47]
        assert w["Данные"]["AV7"].value
        assert w["Расчет"]["J4"].value == '=""'
        w.close()
    before = {p: fingerprint(p) for p in target.iterdir()}
    with pytest.raises(service.ServiceError, match="OUTPUT_EXISTS"):
        service.run_update_all(synthetic, output_dir=target, as_of=DAY)
    assert before == {p: fingerprint(p) for p in target.iterdir()}


@pytest.mark.parametrize("failure", [OSError("DEMO_FAILURE"), KeyboardInterrupt()])
def test_incomplete_bundle_rolls_back_interruption(synthetic, monkeypatch, failure):
    original = service.write
    calls = []

    def write(*args, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise failure
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "write", write)
    target = synthetic.root / "bundle"
    with pytest.raises((service.ServiceError, KeyboardInterrupt)):
        service.run_update_all(synthetic, output_dir=target, as_of=DAY)
    assert target.exists() and not list(target.iterdir())


def test_collision_does_not_delete_others_or_leave_partial_bundle(synthetic):
    target = synthetic.root / "bundle"
    target.mkdir()
    existing = target / "Water_Sample_Ranking_пырейное.xlsx"
    existing.write_bytes(b"EXISTING_SYNTHETIC_SENTINEL")
    before = fingerprint(existing)
    with pytest.raises(service.ServiceError, match="OUTPUT_EXISTS"):
        service.run_update_all(synthetic, output_dir=target, as_of=DAY)
    assert list(target.iterdir()) == [existing] and fingerprint(existing) == before


@pytest.mark.parametrize("dirname", ["data", "templates", "gdis_loader-demo"])
def test_batch_protected_directory(synthetic, dirname):
    target = synthetic.root / dirname
    target.mkdir(exist_ok=True)
    before = set(target.rglob("*"))
    with pytest.raises(service.ServiceError, match="UNSAFE_OUTPUT"):
        service.run_update_all(synthetic, output_dir=target / "new", as_of=DAY)
    assert set(target.rglob("*")) == before


def test_source_mutation_between_fields_aborts_entire_bundle(synthetic, monkeypatch):
    original = service.write
    count = 0

    def write(*args, **kwargs):
        nonlocal count
        original(*args, **kwargs)
        count += 1
        if count == 2:
            path = synthetic.path("lha")
            assert path
            st = path.stat()
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1000000))

    monkeypatch.setattr(service, "write", write)
    target = synthetic.root / "bundle"
    with pytest.raises(service.ServiceError, match="SOURCE_CHANGED"):
        service.run_update_all(synthetic, output_dir=target, as_of=DAY)
    assert not list(target.iterdir())


def test_optional_missing_wrong_schema_and_validate_no_writes(synthetic: AppConfig):
    bad = synthetic.root / "synthetic-plan.json"
    bad.write_text('{"schema":"unexpected"}')
    synthetic.paths.update(gtm_plan=str(bad), geodata="absent.json")
    before = set(synthetic.root.rglob("*"))
    r = service.run_validate(synthetic, as_of=DAY)
    assert not r.has_errors
    assert set(synthetic.root.rglob("*")) == before
    assert r.report["optional_sources"]["gtm_plan"]["status"] == "SCHEMA_DRIFT"
    assert r.report["optional_sources"]["geodata"]["status"] == "SOURCE_UNAVAILABLE"
    assert all(c.state == "UNKNOWN" for row in r.rows for c in row.criteria[6:])


def test_cli_all_help_and_batch(synthetic, capsys):
    with pytest.raises(SystemExit) as status:
        main(["update-all", "--help"])
    assert status.value.code == 0 and "--output-dir" in capsys.readouterr().out
    from water_sample_ranker.config import save_config

    save_config(synthetic)
    assert (
        main(
            [
                "update-all",
                "--project",
                str(synthetic.root),
                "--as-of",
                DAY.isoformat(),
                "--output-dir",
                str(synthetic.root / "cli-bundle"),
            ]
        )
        == 0
    )
    assert (synthetic.root / "cli-bundle/bundle-complete.json").exists()


def test_cancellation_before_publish_removes_workbook_and_temp(synthetic):
    def cancel(update):
        if update.percent == 96:
            raise service.ServiceError("OPERATION_CANCELLED")

    with pytest.raises(service.ServiceError, match="OPERATION_CANCELLED"):
        service.run_update(synthetic, target_field="береговое", as_of=DAY, progress=cancel)
    assert not list((synthetic.root / "outputs").iterdir())


def test_reproducible_demo_is_all_synthetic(tmp_path):
    from scripts.make_synthetic_demo import generate

    config = generate(tmp_path / "demo")
    results = service.run_update_all(config, as_of=DAY, output_dir=config.root / "outputs")
    assert [len(r.rows) for r in results] == [2, 2, 2]
    assert all(r.identity.display.startswith("DEMO-") for result in results for r in result.rows)
    assert all(
        c.normalized_score is None for result in results for r in result.rows for c in r.criteria
    )
    assert (
        sum(
            len(e.neighbors)
            for result in results
            for r in result.rows
            for e in r.diagnostic_evidence
        )
        == 3
    )


def test_concurrent_output_winner_is_preserved(synthetic, monkeypatch):
    original = service.os.link

    def race(source, destination):
        Path(destination).write_bytes(b"SYNTHETIC_CONCURRENT_WINNER")
        return original(source, destination)

    monkeypatch.setattr(service.os, "link", race)
    target = synthetic.root / "bundle-race"
    with pytest.raises(service.ServiceError):
        service.run_update_all(synthetic, output_dir=target, as_of=DAY)
    files = list(target.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"SYNTHETIC_CONCURRENT_WINNER"
