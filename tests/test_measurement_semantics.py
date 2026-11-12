"""Негативные измерения не становятся доказательствами; исходник сохраняется."""

from datetime import date

import pytest
from openpyxl import load_workbook

from water_sample_ranker.config import AppConfig
from water_sample_ranker.domain.models import Truth
from water_sample_ranker.readers.common import parse_number
from water_sample_ranker.readers.lha import read_lha
from water_sample_ranker.service import run_validate
from water_sample_ranker.source_manifest import fingerprint

DAY = date(2026, 11, 5)


@pytest.mark.parametrize("raw", [-1, -0.001, "-1,5"])
@pytest.mark.parametrize(
    "sheet,col,field",
    [
        ("БД Сибнефтегаз", "L", "water"),
        ("БД Сибнефтегаз", "W", "mineralization"),
        ("БД Сибнефтегаз", "Z", "chlorine"),
        ("Для слайда", "H", "water"),
        ("Для слайда", "K", "mineralization"),
        ("Для слайда", "N", "chlorine"),
    ],
)
def test_negative_is_semantic_not_numeric_parse_error(synthetic: AppConfig, raw, sheet, col, field):
    path = synthetic.path("lha")
    assert path is not None
    b = load_workbook(path)
    coord = f"{col}{3 if sheet == 'БД Сибнефтегаз' else 29}"
    b[sheet][coord] = raw
    b.save(path)
    b.close()
    before = fingerprint(path)
    samples, issues = read_lha(path)
    assert fingerprint(path) == before
    parsed = parse_number(raw)
    assert parsed is not None and parsed < 0
    bad = [s for s in samples if s.invalid_measurements]
    assert len(bad) == 1 and bad[0].invalid_measurements == (field,)
    e = next(e for e in bad[0].evidence if e.status == "INVALID_MEASUREMENT")
    assert e.raw == raw and e.value == raw and e.measurement_field == field
    assert e.location.sheet == sheet and e.location.cell == coord
    issue = next(i for i in issues if i.code == "INVALID_MEASUREMENT")
    assert issue.locations == (e.location,) and issue.well_id == "1"
    assert getattr(bad[0], "water_l_day" if field == "water" else field) is None


@pytest.mark.parametrize("column,affected", [("L", (6,)), ("W", (2,)), ("Z", (2, 3, 6))])
def test_same_well_sources_and_fields_do_not_mask_invalid_latest(synthetic, column, affected):
    path = synthetic.path("lha")
    b = load_workbook(path)
    b["БД Сибнефтегаз"][column + "3"] = -1
    b.save(path)
    b.close()
    r = run_validate(synthetic, as_of=DAY)
    assert not r.has_errors
    row = next(
        x
        for x in r.rows
        if x.identity.display == "1" and x.identity.fund.normalized_field == "береговое"
    )
    assert row.pair.last is not None and row.pair.last.date is not None
    assert row.pair.last.date.day == 10  # Не выбирается более старая пригодная проба.
    for number in affected:
        assert row.criteria[number - 1].state == Truth.UNKNOWN
        assert "INVALID_MEASUREMENT" in row.criteria[number - 1].diagnostic_reason
    assert row.criteria[3].state == Truth.TRUE  # Независимый AB из slide сохранён.
    assert row.criteria[4].state == Truth.TRUE
    if column == "W":
        assert (
            row.criteria[2].state == Truth.FALSE
            and row.criteria[5].state == Truth.PENDING_CUSTOMER_RULE
        )
        assert row.separator == "сепаратор МЗС"
    if column == "L":
        assert row.criteria[1].state == Truth.TRUE and row.separator == "сепаратор МЗС"
    other = next(
        x
        for x in r.rows
        if x.identity.display == "1" and x.identity.fund.normalized_field == "пырейное"
    )
    assert (
        other.criteria[1].state == Truth.TRUE
        and other.criteria[5].state == Truth.PENDING_CUSTOMER_RULE
    )
    assert r.report["lha"]["invalid_measurements"]["total"] == 1


@pytest.mark.parametrize("other", [-2, None])
def test_distinct_bad_values_are_not_collapsed_as_exact_duplicates(synthetic, other):
    path = synthetic.path("lha")
    b = load_workbook(path)
    ws = b["БД Сибнефтегаз"]
    row = ws.max_row + 1
    for cell in ws[3]:
        ws.cell(row, cell.column, cell.value)
    ws["Z3"] = -1
    ws.cell(row, 26, other)
    b.save(path)
    b.close()
    r = run_validate(synthetic, as_of=DAY)
    first = r.rows[0]
    assert first.pair.conflict and first.pair.reason == "DUPLICATE_TIMESTAMP_CONFLICT"


@pytest.mark.parametrize("header", [None, "Итог", "Тип воды", '="Заключение"', "#REF!"])
def test_ab_header_drift_fails_closed_with_exact_location(synthetic, header):
    path = synthetic.path("lha")
    b = load_workbook(path)
    b["Для слайда"]["AB27"] = header
    b.save(path)
    b.close()
    r = run_validate(synthetic, as_of=DAY)
    assert r.has_errors
    issue = next(i for i in r.report["issues"] if i["code"] == "LHA_SCHEMA_DRIFT")
    assert issue["locations"] == [{"file": str(path), "sheet": "Для слайда", "cell": "AB27"}]
    assert all(x.criteria[4].state == Truth.UNKNOWN for x in r.rows)
    assert all(
        x.criteria[3].state == Truth.UNKNOWN
        or (
            x.identity.display == "23"
            and x.identity.fund.normalized_field == "береговое"
            and x.identity.plast == "ПК1"
            and x.criteria[3].state == Truth.NOT_APPLICABLE
        )
        for x in r.rows
    )


def test_missing_slide_is_an_error_and_never_database_conclusion(synthetic):
    path = synthetic.path("lha")
    b = load_workbook(path)
    del b["Для слайда"]
    b.save(path)
    b.close()
    r = run_validate(synthetic, as_of=DAY)
    assert r.has_errors and any(i["code"] == "LHA_SHEET_MISSING" for i in r.report["issues"])
    assert all(x.criteria[4].state == Truth.UNKNOWN for x in r.rows)
    assert all(
        x.criteria[3].state == Truth.UNKNOWN
        or (
            x.identity.display == "23"
            and x.identity.fund.normalized_field == "береговое"
            and x.identity.plast == "ПК1"
            and x.criteria[3].state == Truth.NOT_APPLICABLE
        )
        for x in r.rows
    )


@pytest.mark.parametrize("value", [None, "#REF!", "#DIV/0!", '="Т"'])
def test_valid_ab_header_does_not_turn_missing_or_error_result_into_false(synthetic, value):
    path = synthetic.path("lha")
    b = load_workbook(path)
    b["Для слайда"]["AB29"] = value
    b.save(path)
    b.close()
    r = run_validate(synthetic, as_of=DAY)
    assert not r.has_errors
    assert r.rows[0].criteria[3].state == r.rows[0].criteria[4].state == Truth.UNKNOWN
    assert r.rows[0].criteria[1].state == Truth.TRUE


def test_valid_contract_and_freshness_dates_are_observed_without_cutoff_policy(synthetic):
    r = run_validate(synthetic, as_of=DAY)
    assert not r.has_errors
    expected = {
        "source_date": "2026-08-10T00:00:00",
        "selected_fund_date": "2026-10-01",
        "calculation_date": "2026-11-05",
        "status": "FRESHNESS_POLICY_PENDING",
    }
    assert {k: r.report["source_freshness"][k] for k in expected} == expected
    assert r.report["source_freshness"]["confirmed_latest_count"] == 106
    assert r.report["source_freshness"]["policy"] == "DESCRIPTIVE_ONLY_NO_EXPIRY_OR_SCORE"
    assert "ВНИМАНИЕ: актуальность ЛХА" in r.summary()
    assert r.rows[0].criteria[0].state == Truth.TRUE
