from datetime import datetime
from pathlib import Path

import pytest
from openpyxl import Workbook

from water_sample_ranker.config import AppConfig


@pytest.fixture
def synthetic(tmp_path: Path) -> AppConfig:
    data = tmp_path / "data"
    data.mkdir()
    fields = (("Береговое", "БЛУ", 105), ("Пырейное", "ПЛУ", 2), ("Хадырьяхинское", "ХЛУ", 2))
    fund = Workbook()
    fund.remove(fund.active)
    ws = fund.create_sheet("01.10.2026")
    ws["C1"] = datetime(2026, 10, 1)
    for offset, (field, _, count) in enumerate(fields):
        row = 11 + offset * 2
        ws.cell(row, 1, field)
        ws.cell(row, 2, field + " (ПК1)")
        ws.cell(row, 3, count - 1)
        ws.cell(row, 7, 1)
        ws.cell(row + 1, 3, ", ".join(str(i) for i in range(1, count)))
        ws.cell(row + 1, 7, str(count))
    fund.save(data / "версия фонда.xlsx")
    history = Workbook()
    history.remove(history.active)
    for _field, sheet, count in fields:
        ws = history.create_sheet(sheet)
        headers = (
            "№ куста",
            "№ скв.",
            "дата внс",
            "пласт",
            "информация",
            "заканчивание",
            "примечание",
            "ГТМ 1",
            "Дата",
            "Пласт",
            "Отчет",
            "Заканчивание",
            "Примечание",
        )
        for col, h in enumerate(headers, 1):
            ws.cell(2, col, h)
        for i in range(1, count + 1):
            ws.cell(i + 2, 1, 1)
            ws.cell(i + 2, 2, str(i))
            ws.cell(i + 2, 3, datetime(2020, 1, 1))
            ws.cell(i + 2, 4, "ПК1")
    history.save(data / "история новая.xlsx")
    lha = Workbook()
    lha.remove(lha.active)
    base = lha.create_sheet("БД Сибнефтегаз")
    for col, h in {
        3: "Месторождение",
        4: "Номер скважины",
        5: "Пласт",
        7: "Дата и время исследования",
        12: "Дебит воды, литр/сут",
        23: "Данные ЛХА, минерализация, мг/дм3",
        26: "Данные ЛХА, CL-, мг/дм3",
    }.items():
        base.cell(1, col, h)
    slide = lha.create_sheet("Для слайда")
    for col, h in {
        2: "Номер скважины",
        3: "Пласт",
        4: "Месторождение",
        5: "Дата отбора",
        8: "Дебит воды",
        11: "Минерализация",
        14: "Cl-",
        28: "Заключение",
    }.items():
        slide.cell(27, col, h)
    row = 2
    slide_row = 28
    for field, _, count in fields:
        for i in range(1, count + 1):
            for day, cl in ((1, 3000), (10, 6001)):
                for col, value in {
                    3: field,
                    4: str(i),
                    5: "ПК1",
                    7: datetime(2026, 8, day),
                    12: 7000,
                    23: 10000,
                    26: cl,
                }.items():
                    base.cell(row, col, value)
                for col, value in {
                    2: str(i),
                    3: "ПК1",
                    4: field,
                    5: datetime(2026, 8, day),
                    8: 7,
                    11: 10000,
                    14: cl,
                    28: "Т+П?",
                }.items():
                    slide.cell(slide_row, col, value)
                row += 1
                slide_row += 1
    lha.save(data / "анализы версия.xlsx")
    root = Path(__file__).resolve().parents[1]
    return AppConfig(
        tmp_path,
        {
            "calculator": str(root / "templates/Water_Sample_Ranking_Calculator.xlsx"),
            "fund": "data/версия фонда.xlsx",
            "lha": "data/анализы версия.xlsx",
            "well_history": "data/история новая.xlsx",
        },
    )
