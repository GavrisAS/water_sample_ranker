from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tests.formula_subset import Evaluator
from water_sample_ranker.config import AppConfig
from water_sample_ranker.service import run_update


def test_emitted_formula_golden_propagation_and_unknowns(synthetic: AppConfig) -> None:
    result = run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 5))
    assert result.output is not None
    b = load_workbook(result.output, data_only=False)
    ev = Evaluator(b)
    assert ev.cell("Критерии", "B20") == "UNKNOWN"
    assert ev.cell("Критерии", "C20") == "TRUE"
    assert ev.cell("Расчет", "A108") == "105"
    assert ev.cell("Расчет", "A109") == ""
    assert ev.cell("Расчет", "H4") == pytest.approx(0.60)
    assert ev.cell("Расчет", "I4") == pytest.approx(0.60)
    assert ev.cell("Расчет", "F4") == 4
    assert ev.cell("Рейтинг", "B108") == "105"
    assert ev.cell("Рейтинг", "E4") == "" and ev.cell("Рейтинг", "A4") == ""
    assert ev.cell("Рейтинг", "K21") == "" and ev.cell("Рейтинг", "L21") == ""
    # Change imported criterion states: dependent formulas follow, unknowns never become 0 scores.
    headers = {c.value: c.column for c in b["Данные"][6]}
    for i in range(1, 10):
        b["Данные"].cell(7, headers[f"Критерий {i}"], "UNKNOWN")
    assert ev.cell("Расчет", "I4") == 0  # Zero evidence coverage is valid, zero score is not.
    assert ev.cell("Расчет", "J4") == "" and ev.cell("Расчет", "K4") == ""
    b["Данные"].cell(7, headers["Критерий 2"], "TRUE")
    b["Данные"].cell(7, headers["Критерий 3"], "NOT_APPLICABLE")
    assert ev.cell("Расчет", "I4") == pytest.approx(0.08 / 0.88)
    b.close()


def test_template_unused_capacity_has_no_fake_rank() -> None:
    b = load_workbook(
        Path(__file__).resolve().parents[1] / "templates/Water_Sample_Ranking_Calculator.xlsx"
    )
    ev = Evaluator(b)
    for coord in ("A4", "B4", "E4", "A1003", "E1003", "L21"):
        assert ev.cell("Рейтинг", coord) == ""
    b.close()
