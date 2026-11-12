"""Независимые граничные сценарии согласованных решений Stage C."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tests.formula_subset import Evaluator
from tests.test_water_rules import sample
from water_sample_ranker.config import AppConfig
from water_sample_ranker.criteria.rules import duration, full_months, water_rate
from water_sample_ranker.domain.models import SamplePair, Truth
from water_sample_ranker.excel.manual import identity_key, merge_marks, read_marks
from water_sample_ranker.matching.matcher import complementary_gate
from water_sample_ranker.readers.optional import read_optional
from water_sample_ranker.service import ServiceError, run_update, run_update_all


@pytest.mark.parametrize(
    "start,end,months",
    [
        ("2024-01-31", "2024-02-29", 1),
        ("2023-01-31", "2023-02-28", 1),
        ("2024-02-29", "2025-02-28", 12),
        ("2026-03-31", "2026-04-30", 1),
        ("2026-01-30", "2026-02-27", 0),
        ("2025-12-31", "2026-01-31", 1),
        ("2025-11-12", "2026-11-12", 12),
        ("2025-11-13", "2026-11-12", 11),
    ],
)
def test_calendar_months_golden(start, end, months):
    start, end = date.fromisoformat(start), date.fromisoformat(end)
    assert full_months(start, end) == months
    c, days, actual = duration(
        SamplePair(sample(date=datetime.combine(start, datetime.min.time()))), end
    )
    assert actual == months and days == (end - start).days
    assert c.normalized_score == min(months, 12) / 12


def test_future_reference_has_no_pseudo_days():
    c, days, months = duration(SamplePair(sample(date=datetime(2030, 1, 1))), date(2026, 1, 1))
    assert c.state == Truth.UNKNOWN and days is months is None


@pytest.mark.parametrize("days,state", [(7, Truth.PENDING_CUSTOMER_RULE), (7.0001, Truth.UNKNOWN)])
def test_gate_complementary_boundaries(days, state):
    a = sample(water_l_day=None, chlorine=2500)
    assert a.date is not None
    b = sample(date=a.date - timedelta(days=days), chlorine=None, water_l_day=1200)
    gate = complementary_gate(SamplePair(a), (a, b))
    assert water_rate(gate).state == state
    assert water_rate(gate).weighted_contribution is None
    assert (
        water_rate(complementary_gate(SamplePair(a), (a, b, replace(b, conclusion="К")))).state
        == Truth.UNKNOWN
    )
    assert (
        water_rate(complementary_gate(SamplePair(a), (a, replace(b, plast="ПК2")))).state
        == Truth.UNKNOWN
    )


def test_optional_deep_json_fail_closed(tmp_path):
    p = tmp_path / "deep.json"
    p.write_text("[" * 2500 + "0" + "]" * 2500)
    assert read_optional(p, "geodata").status == "SCHEMA_DRIFT"


def test_manual_persistence_switch_and_stage_boundary(synthetic: AppConfig):
    first = run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 12))
    path = first.output
    assert path
    b = load_workbook(path)
    b["Настройки"]["F56"] = "Да"
    b["Настройки"]["D28"] = 0.21
    b.save(path)
    b.close()
    before = path.read_bytes()
    synthetic.paths["manual_input"] = str(path)
    synthetic.paths["output"] = str(synthetic.root / "again.xlsx")
    second = run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 12))
    assert second.rows[0].criteria[7].state == Truth.TRUE
    assert second.rows[0].criteria[7].weighted_contribution == 0.21
    assert path.read_bytes() == before
    synthetic.paths["manual_input"] = str(second.output)
    synthetic.paths["output"] = str(synthetic.root / "other.xlsx")
    switched = run_update(synthetic, target_field="пырейное", as_of=date(2026, 11, 12))
    assert switched.output is not None
    marks = read_marks(switched.output)
    assert marks[identity_key(first.rows[0])][5] == "Да"
    assert switched.rows[0].criteria[7].state == Truth.UNKNOWN
    row = first.rows[0]
    assert row.identity.stage is not None
    changed = replace(
        row, identity=replace(row.identity, stage=replace(row.identity.stage, ordinal=99))
    )
    merged = merge_marks((changed,), marks)
    assert merged[identity_key(changed)][5] == "Не задано"
    assert merged[identity_key(row)][5] == "Да"
    # Конфликтная копия не публикует частичный результат.
    b = load_workbook(path)
    for col in range(1, 11):
        b["Настройки"].cell(57, col, b["Настройки"].cell(56, col).value)
    b.save(path)
    b.close()
    synthetic.paths["manual_input"] = str(path)
    synthetic.paths["output"] = str(synthetic.root / "conflict.xlsx")
    with pytest.raises(ServiceError, match="MANUAL_DUPLICATE"):
        run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 12))
    assert not Path(synthetic.paths["output"]).exists()


def test_calendar_excel_and_manual_diameter(synthetic: AppConfig):
    result = run_update(synthetic, target_field="пырейное", as_of=date(2026, 11, 12))
    b = load_workbook(result.output)
    ev = Evaluator(b)
    headers = {c.value: c.column for c in b["Данные"][6]}
    ref = b["Данные"].cell(7, headers["Опорная дата давности"])
    ref.value = datetime(2024, 1, 31)
    b["Настройки"]["B7"] = datetime(2024, 2, 29)
    assert ev.cell("Критерии", "L20") == 1
    assert ev.cell("Расчет", "O4") == pytest.approx(0.2 / 12)
    b["Настройки"]["B7"] = datetime(2026, 11, 12)
    # Таблица включает все поля; редактируется запись именно выбранного поля.
    s = b["Настройки"]
    row = next(r for r in range(56, s.max_row + 1) if s.cell(r, 2).value == "пырейное")
    for c, v in ((7, datetime(2026, 9, 1)), (8, 10), (9, 12), (10, "Подтверждённый диаметр, мм")):
        s.cell(row, c, v)
    assert ev.cell("Критерии", "H20") == "TRUE"
    s.cell(row, 7, datetime(2026, 8, 1))
    assert ev.cell("Критерии", "H20") == "UNKNOWN"
    b.close()


def test_batch_marks_from_all_previous_fields(synthetic: AppConfig):
    before = synthetic.root / "batch-before"
    results = run_update_all(synthetic, as_of=date(2026, 11, 12), output_dir=before)
    for result in results:
        b = load_workbook(result.output)
        s = b["Настройки"]
        field = result.report["selected_field"]
        row = next(r for r in range(56, s.max_row + 1) if s.cell(r, 2).value == field)
        s.cell(row, 6, "Да")
        b.save(result.output)
        b.close()
    synthetic.paths["manual_input"] = str(before)
    after = run_update_all(
        synthetic, as_of=date(2026, 11, 12), output_dir=synthetic.root / "batch-after"
    )
    assert all(r.rows[0].criteria[7].state == Truth.TRUE for r in after)


def test_current_stage_start_and_partial_or_conflicting_history(synthetic):
    from water_sample_ranker.domain.models import DatePrecision, PartialDate

    result = run_update(synthetic, target_field="пырейное", as_of=date(2026, 11, 12))
    identity = result.rows[0].identity
    assert identity.stage
    stage = replace(identity.stage, started_at=PartialDate(2026, 10, 12, DatePrecision.DAY))
    identity = replace(identity, stage=stage)
    criterion, days, months = duration(SamplePair(), date(2026, 11, 12), identity)
    assert months == 1 and days == 31 and criterion.normalized_score == 1 / 12
    partial = replace(stage, started_at=PartialDate(2026, 10, None, DatePrecision.MONTH))
    assert (
        duration(SamplePair(), date(2026, 11, 12), replace(identity, stage=partial))[0].state
        == Truth.UNKNOWN
    )
    assert (
        duration(SamplePair(), date(2026, 11, 12), replace(identity, status="AMBIGUOUS"))[0].state
        == Truth.UNKNOWN
    )


def test_deprecated_inputs_never_opened_and_missing_monitor_nonblocking(synthetic, monkeypatch):
    import water_sample_ranker.readers.optional as optional
    from water_sample_ranker.service import run_validate

    def forbidden(*args, **kwargs):
        raise AssertionError("Запрещено читать устаревший источник")

    monkeypatch.setattr(optional, "read_optional", forbidden)
    synthetic.paths.update(
        gtm_plan="missing.json", geodata="missing2.json", monitoring="missing.xlsx"
    )
    result = run_validate(synthetic, as_of=date(2026, 11, 12))
    assert not result.has_errors
    assert result.report["deprecated_inputs_ignored"] == ["gtm_plan", "geodata"]
    assert all(r.criteria[8].state == Truth.DISABLED for r in result.rows)


def test_disabled_inputs_can_be_enabled_after_repeated_update(synthetic):
    result = run_update(synthetic, target_field="пырейное", as_of=date(2026, 11, 12))
    assert result.output
    b = load_workbook(result.output)
    s = b["Настройки"]
    s["I21"] = "Нет"
    s["I22"] = "Нет"
    b.save(result.output)
    b.close()
    synthetic.paths["manual_input"] = str(result.output)
    synthetic.paths["output"] = str(synthetic.root / "disabled.xlsx")
    repeated = run_update(synthetic, target_field="пырейное", as_of=date(2026, 11, 12))
    assert repeated.rows[0].criteria[0].state == Truth.DISABLED
    b = load_workbook(repeated.output)
    ev = Evaluator(b)
    assert ev.cell("Критерии", "B20") == "DISABLED"
    assert ev.cell("Критерии", "C20") == "DISABLED"
    b["Настройки"]["I21"] = "Да"
    b["Настройки"]["I22"] = "Да"
    assert ev.cell("Критерии", "B20") == "TRUE"
    assert ev.cell("Критерии", "C20") == "TRUE"
    b.close()


def test_handoff_production_projection_withholds_numerical_results():
    from water_sample_ranker.handoff.builder import share_safe_production

    result = share_safe_production(
        {
            "status": "PASS",
            "fresh_execution": True,
            "summary": {"field_counts": {"SYNTHETIC": 987654}},
            "updates": {"raw": "PRIVATE_FIXTURE"},
            "source_fingerprints": {"fund": {"sha256": "a" * 64, "bytes": 123, "mtime_ns": 0}},
        }
    )
    assert result["status"] == "PASS" and result["fresh_execution"]
    assert "summary" not in result and "updates" not in result
    assert result["production_rows_and_numbers_included"] is False


def test_excel_rounding_contract_uses_half_up():
    from water_sample_ranker.domain.models import CriterionResult
    from water_sample_ranker.domain.scoring import confirmed_score

    item = CriterionResult(
        2, 0.0000000000005, Truth.TRUE, normalized_score=1, weighted_contribution=0.0000000000005
    )
    assert confirmed_score((item,)) == 0.000000000001


def test_choke_event_later_same_day_python_excel_parity(synthetic):
    first = run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 12))
    assert first.output
    b = load_workbook(first.output)
    s = b["Настройки"]
    for c, v in (
        (7, datetime(2026, 8, 10, 1)),
        (8, 10),
        (9, 12),
        (10, "Подтверждённое время и фактические диаметры в мм"),
    ):
        s.cell(56, c, v)
    ev = Evaluator(b)
    assert ev.cell("Критерии", "H20") == "TRUE"
    b.save(first.output)
    b.close()
    synthetic.paths.update(
        manual_input=str(first.output), output=str(synthetic.root / "same-day.xlsx")
    )
    repeated = run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 12))
    assert repeated.rows[0].criteria[6].state == Truth.TRUE
    assert repeated.rows[0].criteria[6].weighted_contribution == 0.05
    assert repeated.rows[0].criteria[6].source_date == datetime(2026, 8, 10, 1)
    assert [loc.cell for loc in repeated.rows[0].criteria[6].source_locations] == [
        "G56",
        "H56",
        "I56",
        "J56",
    ]
    b = load_workbook(repeated.output)
    ev = Evaluator(b)
    assert ev.cell("Критерии", "H20") == "TRUE"
    b["Настройки"]["G56"] = datetime(2026, 11, 12, 12)
    assert ev.cell("Критерии", "H20") == "TRUE"  # Вся дата расчёта включена.
    b["Настройки"]["G56"] = datetime(2026, 11, 13)
    assert ev.cell("Критерии", "H20") == "UNKNOWN"
    b.close()
