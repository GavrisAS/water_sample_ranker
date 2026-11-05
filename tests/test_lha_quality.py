from datetime import date

import pytest
from openpyxl import load_workbook

from water_sample_ranker.config import AppConfig, CriteriaConfig
from water_sample_ranker.domain.models import Truth
from water_sample_ranker.readers.lha import read_lha
from water_sample_ranker.service import run_validate


def test_formula_cache_errors_and_units_are_explicit(synthetic: AppConfig) -> None:
    path = synthetic.path("lha")
    assert path is not None
    b = load_workbook(path)
    b["Для слайда"]["AB29"] = '="Т"'  # openpyxl creates no formula result cache.
    b["БД Сибнефтегаз"]["Z3"] = "#REF!"
    b.save(path)
    b.close()
    records, issues = read_lha(path)
    assert not any(i.severity == "error" for i in issues)
    s = next(
        s for s in records if s.source == "slide" and s.well == "1" and s.date and s.date.day == 10
    )
    assert s.water_l_day == 7000
    assert s.conclusion is None
    assert any(e.status == "FORMULA_NO_CACHE" and e.location.cell == "AB29" for e in s.evidence)
    r = run_validate(synthetic, as_of=date(2026, 11, 5))
    assert not r.has_errors
    first = next(
        row
        for row in r.rows
        if row.identity.display == "1" and row.identity.fund.normalized_field == "береговое"
    )
    assert first.criteria[1].state == Truth.UNKNOWN
    assert first.criteria[3].state == Truth.UNKNOWN
    assert first.criteria[4].state == Truth.UNKNOWN
    assert first.criteria[5].state == Truth.UNKNOWN


def test_schema_drift_blocks_instead_of_using_wrong_column(synthetic: AppConfig) -> None:
    path = synthetic.path("lha")
    assert path is not None
    b = load_workbook(path)
    b["БД Сибнефтегаз"]["D1"] = "Не скважина"
    b.save(path)
    b.close()
    result = run_validate(synthetic, as_of=date(2026, 11, 5))
    assert result.has_errors
    assert any("LHA_SCHEMA_DRIFT" in e for e in result.report["errors"])
    assert not (synthetic.root / "outputs").exists()


def test_custom_thresholds_are_marked_and_invalid_values_rejected() -> None:
    assert CriteriaConfig(cl_delta=1000).policy_status == "CUSTOM_THRESHOLDS_PENDING_APPROVAL"
    with pytest.raises(ValueError, match="INVALID_CRITERIA_PARAMETER"):
        CriteriaConfig(cl_delta=-1)
