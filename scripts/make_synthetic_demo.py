"""Создать новый полностью вымышленный набор DEMO и три книги для демонстрации B."""

import argparse
import json
import shutil
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook

from water_sample_ranker.config import TEMPLATE, AppConfig, save_config
from water_sample_ranker.service import run_update_all


def generate(destination: Path, *, year: int = 2026, extended: bool = False) -> AppConfig:
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    data = destination / "data"
    data.mkdir()
    (destination / "templates").mkdir()
    root = Path(__file__).resolve().parents[1]
    shutil.copyfile(root / TEMPLATE, destination / TEMPLATE)
    fields = (("Береговое", "БЛУ"), ("Пырейное", "ПЛУ"), ("Хадырьяхинское", "ХЛУ"))
    fund, history, lha, monitor = (Workbook() for _ in range(4))
    for w in (history, lha):
        w.remove(w.active)
    fs = fund.active
    fs.title = "01.10.2026"
    fs["C1"] = datetime(2026, 10, 1)
    base, slide = lha.create_sheet("БД Сибнефтегаз"), lha.create_sheet("Для слайда")
    for col, name in {
        3: "Месторождение",
        4: "Номер скважины",
        5: "Пласт",
        7: "Дата и время исследования",
        12: "Дебит воды, литр/сут",
        23: "Данные ЛХА, минерализация, мг/дм3",
        26: "Данные ЛХА, CL-, мг/дм3",
    }.items():
        base.cell(1, col, name)
    for col, name in {
        2: "Номер скважины",
        3: "Пласт",
        4: "Месторождение",
        5: "Дата отбора",
        8: "Дебит воды",
        11: "Минерализация",
        14: "Cl-",
        28: "Заключение",
    }.items():
        slide.cell(27, col, name)
    sample_row = 2
    for index, (field, sheet) in enumerate(fields):
        n = 11 + index * 2
        for col, value in {1: field, 2: field + " (ПК1)", 3: 2, 7: 0, 9: 0, 11: 2}.items():
            fs.cell(n, col, value)
        fs.cell(n + 1, 3, "DEMO-ALFA, DEMO-BETA" + (", DEMO-GAMMA" if extended else ""))
        fs.cell(n, 3, 3 if extended else 2)
        fs.cell(n + 1, 7, None)
        hs = history.create_sheet(sheet)
        names = (
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
        for col, name in enumerate(names, 1):
            hs.cell(2, col, name)
        for row, (well, cl) in enumerate(
            (
                ("DEMO-ALFA", 1000),
                ("DEMO-BETA", 4000),
                *((("DEMO-GAMMA", None),) if extended else ()),
            ),
            3,
        ):
            for col, value in {
                1: 1,
                2: well,
                3: datetime(2020, 1, 1),
                4: "ПК1",
                8: "DEMO-OTHER",
                9: "2024",
                10: "ПК1",
            }.items():
                hs.cell(row, col, value)
            if well == "DEMO-GAMMA":
                continue
            for day in (1, 12):
                stamp = datetime(2026, 8, day)
                for col, value in {
                    3: field,
                    4: well,
                    5: "ПК1",
                    7: stamp,
                    12: 2000,
                    23: 10000,
                    26: cl,
                }.items():
                    base.cell(sample_row, col, value)
                for col, value in {
                    2: well,
                    3: "ПК1",
                    4: field,
                    5: stamp,
                    8: 2,
                    11: 10000,
                    14: cl,
                    28: "К+Т?",
                }.items():
                    slide.cell(sample_row + 26, col, value)
                sample_row += 1
    for col, value in {2: "ИТОГО", 3: 3, 4: 0, 7: 3, 8: 0, 9: 3, 10: 0, 11: 6}.items():
        fs.cell(17, col, value)
    ms = monitor.active
    ms.title = "Техрежим скважин"
    for cell, value in {
        "A1": "Береговое",
        "A2": "Параметр",
        "E2": "Объект",
        "B3": "ГСК",
        "C3": "куста",
        "D3": "скв.",
    }.items():
        ms[cell] = value
    for col, day in enumerate((1, 2, 3, 10), 6):
        ms.cell(3, col, datetime(2026, 10, day))
    for row, values in enumerate((("DEMO-ALFA", 10, 14, 66, 0), ("DEMO-BETA", 12, 16, 109, 0)), 4):
        for col, value in enumerate(
            ("Типоразмер штуцера, мм", "ГСК-DEMO", "К-1", values[0], "ПК1", *values[1:]), 1
        ):
            ms.cell(row, col, value)
    paths = {"calculator": str(TEMPLATE)}
    for role, w in (
        ("fund", fund),
        ("well_history", history),
        ("lha", lha),
        ("monitoring", monitor),
    ):
        if year != 2026:
            for sheet in w:
                if sheet.title == "01.10.2026":
                    sheet.title = f"01.10.{year}"
                for row in sheet:
                    for cell in row:
                        if isinstance(cell.value, datetime):
                            cell.value = cell.value.replace(year=cell.value.year + year - 2026)
        p = data / f"synthetic-{role}.xlsx"
        w.save(p)
        w.close()
        paths[role] = str(p.relative_to(destination))
    config = AppConfig(destination, paths)
    save_config(config)
    return config


def synthetic_optional() -> dict[str, dict]:
    identity = {"field": "береговое", "well": "DEMO-ALFA", "stage": 0, "plast": "ПК1"}
    return {
        "gtm_plan": {
            "schema": "water-gtm-plan-v1",
            "records": [
                identity | {"date": "2026-12-01", "precision": "day", "label": "DEMO-PLAN"}
            ],
        },
        "geodata": {
            "schema": "water-geodata-v1",
            "records": [
                identity
                | {
                    "x": 0.1,
                    "y": 0.2,
                    "crs": "EPSG:4326",
                    "coordinate_source": "SYNTHETIC_NO_FIELD_MAP",
                    "version": "DEMO-v1",
                }
            ],
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output-dir", type=Path, required=True)
    args = p.parse_args()
    config = generate(args.output_dir, year=2042, extended=True)
    results = run_update_all(config, as_of=date(2042, 11, 5), output_dir=config.root / "outputs")
    print(
        json.dumps(
            {
                "synthetic_only": True,
                "rows": [len(r.rows) for r in results],
                "outputs": [str(r.output) for r in results],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
