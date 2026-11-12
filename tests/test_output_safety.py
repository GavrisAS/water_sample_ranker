from datetime import date
from pathlib import Path

import pytest

from water_sample_ranker.config import AppConfig
from water_sample_ranker.excel.writer import write
from water_sample_ranker.readers.ooxml import Book
from water_sample_ranker.service import ServiceError, run_update, run_validate
from water_sample_ranker.source_manifest import fingerprint


@pytest.mark.parametrize(
    "location", ["data/new.xlsx", "templates/new.xlsx", "gdis_loader/new.xlsx"]
)
def test_protected_output_directories(synthetic: AppConfig, location: str) -> None:
    synthetic.paths["output"] = location
    with pytest.raises(ServiceError, match="UNSAFE_OUTPUT"):
        run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 5))
    assert not (synthetic.root / location).exists()


def test_validate_ignores_output_and_field_is_explicit(synthetic: AppConfig) -> None:
    synthetic.paths["output"] = "data/new.xlsx"
    result = run_validate(synthetic, as_of=date(2026, 11, 5))
    assert not result.has_errors
    assert not (synthetic.root / "outputs").exists()
    with pytest.raises(ServiceError):
        run_update(synthetic, target_field="", as_of=date(2026, 11, 5))
    with pytest.raises(ServiceError):
        run_update(synthetic, target_field="нет такого поля", as_of=date(2026, 11, 5))


def test_source_change_counter_blocks_acceptance(synthetic, monkeypatch):
    import os

    from water_sample_ranker import service

    original = service.read_lha

    def touch_after_read(path):
        result = original(path)
        before = path.stat()
        # NTFS timestamps have 100 ns granularity; 1 ns can round back unchanged.
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000))
        assert path.stat().st_mtime_ns != before.st_mtime_ns
        return result

    monkeypatch.setattr(service, "read_lha", touch_after_read)
    result = run_validate(synthetic, as_of=date(2026, 11, 5))
    assert result.has_errors and result.report["source_changed_count"] == 1
    assert "SOURCE_CHANGED_DURING_READ: lha" in result.report["errors"]
    assert not (synthetic.root / "outputs").exists()


def test_capacity_1000_1001_and_exclusive_creation(synthetic: AppConfig, tmp_path: Path) -> None:
    r = run_validate(synthetic, as_of=date(2026, 11, 5))
    template = synthetic.path("calculator")
    assert template is not None
    output = tmp_path / "1000.xlsx"
    write(template, output, (r.rows[0],) * 1000, date(2026, 11, 5), "береговое")
    with Book(output) as b:
        assert max(row for row, _ in b.rows("Данные")) == 1006
    before = fingerprint(output)
    with pytest.raises(FileExistsError):
        write(template, output, (r.rows[0],), date(2026, 11, 5), "береговое")
    assert fingerprint(output) == before
    with pytest.raises(ValueError, match="CAPACITY"):
        write(template, tmp_path / "1001.xlsx", (r.rows[0],) * 1001, date(2026, 11, 5), "береговое")
    assert not (tmp_path / "1001.xlsx").exists()


@pytest.mark.parametrize("sheet,cell,value", [("Расчет", "J4", "=1"), ("Настройки", "D21", 1.1)])
def test_template_validation_rejects_invented_score_or_weights(
    synthetic: AppConfig, sheet, cell, value
) -> None:
    from openpyxl import load_workbook

    source = synthetic.path("calculator")
    assert source is not None
    tampered = synthetic.root / "tampered.xlsx"
    book = load_workbook(source)
    book[sheet][cell] = value
    book.save(tampered)
    book.close()
    synthetic.paths["calculator"] = str(tampered)
    before = fingerprint(tampered)
    result = run_validate(synthetic, as_of=date(2026, 11, 5))
    assert result.has_errors
    assert fingerprint(tampered) == before
    assert not (synthetic.root / "outputs").exists()
