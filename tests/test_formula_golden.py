from datetime import date, datetime
from pathlib import Path

import pytest
from openpyxl import load_workbook

from tests.formula_subset import Evaluator
from water_sample_ranker.config import AppConfig
from water_sample_ranker.domain.scoring import confirmed_score
from water_sample_ranker.service import run_update


def test_emitted_formula_golden_propagation_and_unknowns(synthetic: AppConfig) -> None:
    result = run_update(synthetic, target_field="береговое", as_of=date(2026, 11, 5))
    assert result.output is not None
    b = load_workbook(result.output)
    ev = Evaluator(b)
    # Независимый golden: 2 полных месяца, №2, №4, №5; PASS gate №6 без бонуса.
    expected = round(0.2 * 2 / 12 + 0.08 + 0.15 + 0.15, 12)
    assert confirmed_score(result.rows[0].criteria) == expected
    assert ev.cell("Критерии", "B20") == "TRUE"
    assert ev.cell("Критерии", "G20") == "PENDING_CUSTOMER_RULE"
    assert ev.cell("Критерии", "J20") == "DISABLED"
    assert ev.cell("Расчет", "J4") == expected
    assert ev.cell("Расчет", "X4") == pytest.approx(0.25)
    assert ev.cell("Рейтинг", "E4") == expected
    assert ev.cell("Рейтинг", "A4") == 1
    assert ev.cell("Рейтинг", "A5") == 1
    assert ev.cell("Расчет", "Z4") == "Ничья"
    assert ev.cell("Рейтинг", "B107") == "23"
    assert ev.cell("Рейтинг", "B108") == ""
    # Только редактирование книги; сервис больше не вызывается.
    b["Настройки"]["F56"] = "Да"
    assert ev.cell("Критерии", "I20") == "TRUE"
    assert ev.cell("Расчет", "J4") == pytest.approx(expected + 0.1)
    b["Настройки"]["D28"] = 0.23
    assert ev.cell("Расчет", "J4") == pytest.approx(expected + 0.23)
    b["Настройки"]["F56"] = "Нет"
    assert ev.cell("Критерии", "I20") == "FALSE"
    assert ev.cell("Расчет", "J4") == expected
    b["Настройки"]["F56"] = "Не задано"
    assert ev.cell("Критерии", "I20") == "UNKNOWN"
    b["Настройки"]["I22"] = "Нет"
    assert ev.cell("Критерии", "C20") == "DISABLED"
    assert ev.cell("Расчет", "J4") == pytest.approx(expected - 0.08)
    b["Настройки"]["B7"] = datetime(2027, 8, 10)
    assert ev.cell("Критерии", "L20") == 12
    assert ev.cell("Расчет", "O4") == 0.2
    b["Настройки"]["B7"] = datetime(2026, 7, 1)
    assert ev.cell("Критерии", "B20") == "UNKNOWN"
    assert ev.cell("Критерии", "C20") == "DISABLED"
    b.close()


def test_template_unused_capacity_has_no_fake_rank() -> None:
    b = load_workbook(
        Path(__file__).resolve().parents[1] / "templates/Water_Sample_Ranking_Calculator.xlsx"
    )
    ev = Evaluator(b)
    for coord in ("A4", "B4", "E4", "A1003", "E1003", "BB21"):
        assert ev.cell("Рейтинг", coord) == ""
    b.close()
