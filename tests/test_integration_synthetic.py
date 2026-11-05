import json
from datetime import date
from pathlib import Path
from zipfile import ZipFile

import pytest
from openpyxl import load_workbook

from water_sample_ranker.config import AppConfig, discover, load_config, save_config
from water_sample_ranker.domain.models import Truth
from water_sample_ranker.excel.compatibility import changed_parts, inspect_template
from water_sample_ranker.excel.layout import DATA_HEADERS, SHEETS
from water_sample_ranker.service import ServiceError, run_update, run_validate
from water_sample_ranker.source_manifest import fingerprint

DAY = date(2026, 11, 5)


def test_validate_does_not_write_and_update_is_independent_copy(synthetic: AppConfig) -> None:
    paths = [synthetic.path(r) for r in ("fund", "lha", "well_history", "calculator")]
    before = {str(p): fingerprint(p) for p in paths if p}
    inventory = set(synthetic.root.rglob("*"))
    progress = []
    result = run_validate(synthetic, as_of=DAY, progress=progress.append)
    assert not result.has_errors, result.summary()
    assert set(synthetic.root.rglob("*")) == inventory
    assert result.report["fund_total"] == 109
    assert result.report["available_fields"] == ["береговое", "пырейное", "хадырьяхинское"]
    assert progress[0].percent == 0 and progress[-1].percent == 100
    assert all(a.percent <= b.percent for a, b in zip(progress, progress[1:], strict=False))
    result = run_update(synthetic, target_field="БЛУ", as_of=DAY)
    assert result.output is not None and result.output.exists()
    assert len(result.rows) == 105
    for p in paths:
        if p:
            assert fingerprint(p) == before[str(p)]
    template = synthetic.path("calculator")
    assert template is not None
    assert len(changed_parts(template, result.output)) == 3
    with ZipFile(template) as a, ZipFile(result.output) as b:
        for name in a.namelist():
            if name.startswith(
                (
                    "xl/charts/",
                    "xl/styles",
                    "xl/worksheets/sheet4",
                    "xl/worksheets/sheet5",
                    "xl/worksheets/sheet6",
                )
            ):
                assert a.read(name) == b.read(name)
    book = load_workbook(result.output, data_only=False, keep_links=False)
    assert book.sheetnames == list(SHEETS)
    assert book["Данные"].tables["tblData"].ref == "A6:BA111"
    assert tuple(c.value for c in book["Данные"][6])[: len(DATA_HEADERS)] == DATA_HEADERS
    headers = {c.value: c.column for c in book["Данные"][6]}
    assert book["Данные"].cell(111, headers["Скважина"]).value == "105"
    assert book["Данные"].cell(7, headers["Критерий 2"]).value == "TRUE"
    assert book["Данные"].cell(7, headers["Критерий 7"]).value == "UNKNOWN"
    assert book["Данные"].cell(7, headers["Месторождение"]).value == "береговое"
    assert book["Рейтинг"]["A4"].value == '=""'
    assert book["Рейтинг"]["E4"].value == '=""'
    assert len(book["Рейтинг"]._charts) == 1
    assert book.calculation.calcMode == "auto"
    assert book["Данные"].cell(7, headers["Последняя проба"]).number_format.startswith("dd.mm")
    book.close()
    report = json.loads(result.output.with_suffix(".load-report.json").read_text())
    assert report["output"]["sha256"] == fingerprint(result.output)["sha256"]
    assert report["selected_field"] == "береговое"
    assert report["rows_written"] == 105
    assert all(c["normalized_score"] is None for row in report["rows"] for c in row["criteria"])
    with pytest.raises(ServiceError, match="OUTPUT_EXISTS"):
        run_update(synthetic, target_field="Береговое", as_of=DAY)


def test_discovery_and_config_roundtrip(synthetic: AppConfig) -> None:
    paths, ambiguous = discover(synthetic.root)
    assert not ambiguous
    assert paths["fund"] == "data/версия фонда.xlsx"
    synthetic.extra["top_n"] = 9
    synthetic.extra["other"] = {"user_note": "сохранить"}
    save_config(synthetic)
    config = load_config(synthetic.root)
    assert config.paths["fund"] == synthetic.paths["fund"]
    assert config.extra["other"] == synthetic.extra["other"]
    assert config.extra["top_n"] == 9


def test_template_is_sanitized_and_links_are_dynamic() -> None:
    path = Path(__file__).resolve().parents[1] / "templates/Water_Sample_Ranking_Calculator.xlsx"
    report = inspect_template(path)
    assert report["capacity"] == 1000
    with ZipFile(path) as z:
        assert not any(n.startswith("xl/externalLinks/") for n in z.namelist())
        assert "xl/calcChain.xml" not in z.namelist()
        for n in z.namelist():
            if n.endswith(".xml"):
                raw = z.read(n).decode()
                assert "#REF!" not in raw and "EXP(" not in raw and "LN(" not in raw
    book = load_workbook(path, data_only=False)
    assert book["Данные"]["A7"].value is None
    assert book["Критерии"]["B20"].value.startswith("=IFERROR")
    assert "tblData[Критерий 1]" in book["Критерии"]["B20"].value
    assert "Настройки!$D$21" in book["Расчет"]["H4"].value
    assert "UNKNOWN" in book["Расчет"]["F4"].value
    assert all(s.sheet_state == "visible" for s in book)
    assert all(book["Настройки"].row_dimensions[r].hidden is False for r in range(21, 30))
    assert sum(book["Настройки"].cell(i, 4).value for i in range(21, 30)) == pytest.approx(1)
    book.close()


def test_missing_source_and_unsafe_output(synthetic: AppConfig) -> None:
    synthetic.paths["fund"] = "missing.xlsx"
    result = run_validate(synthetic, as_of=DAY)
    assert result.has_errors
    assert not (synthetic.root / "outputs").exists()


def test_all_three_fields_are_written_separately(synthetic: AppConfig) -> None:
    for field in ("пырейное", "хадырьяхинское"):
        result = run_update(synthetic, target_field=field, as_of=DAY)
        assert len(result.rows) == 2
        assert all(r.identity.fund.normalized_field == field for r in result.rows)
        assert any(
            r.identity.fund.source_category.value.startswith("бездействующие") for r in result.rows
        )
        assert all(r.criteria[6].state == Truth.UNKNOWN for r in result.rows)
        assert all(not r.criteria[6].source_locations for r in result.rows)
        assert all(r.criteria[7].source_date is None for r in result.rows)


def test_custom_thresholds_are_reported_and_written(synthetic: AppConfig) -> None:
    from water_sample_ranker.config import CriteriaConfig

    synthetic.criteria = CriteriaConfig(cl_delta=4000)
    r = run_update(synthetic, target_field="береговое", as_of=DAY)
    assert r.report["criteria_policy_status"] == "CUSTOM_THRESHOLDS_PENDING_APPROVAL"
    assert r.rows[0].criteria[1].state == Truth.FALSE
    assert r.rows[0].criteria[1].normalized_score is None
    assert r.output is not None
    b = load_workbook(r.output)
    assert "CUSTOM_THRESHOLDS_PENDING_APPROVAL" in b["Настройки"]["B6"].value
    assert "4000" in b["Настройки"]["G22"].value
    assert b["Настройки"]["B7"].number_format == "dd.mm.yyyy"
    b.close()
