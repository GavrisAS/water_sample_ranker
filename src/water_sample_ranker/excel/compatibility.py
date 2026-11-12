"""Structural checks are independent of native Excel recalculation/rendering."""

import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from water_sample_ranker.excel.layout import (
    ALL_DATA_HEADERS,
    CAPACITY,
    DATA_HEADERS,
    DATA_HEADERS_V2,
    SHEETS,
)
from water_sample_ranker.readers.ooxml import Book, q


def inspect_template(path: Path) -> dict[str, object]:
    with Book(path) as b:
        if tuple(b.sheets) != SHEETS:
            raise ValueError("TEMPLATE_SHEETS_INVALID")
        props = b.zip.read("docProps/core.xml").decode()
        if "water-v0.2-stage-A" not in props:
            raise ValueError("TEMPLATE_NOT_WATER: выберите новый производный шаблон")
        if any(n.startswith(("xl/externalLinks/", "xl/vbaProject")) for n in b.zip.namelist()):
            raise ValueError("TEMPLATE_UNSAFE_LINKS")
        tables = {}
        headers = DATA_HEADERS
        for name in b.zip.namelist():
            if name.startswith("xl/tables/") and name.endswith(".xml"):
                t = ET.fromstring(b.zip.read(name))
                tables[t.attrib["displayName"]] = t.attrib["ref"]
                if t.attrib["displayName"] == "tblData":
                    columns = t.find(q("tableColumns"))
                    assert columns is not None
                    headers = tuple(c.get("name") for c in columns)
                    if headers not in {DATA_HEADERS, DATA_HEADERS_V2, ALL_DATA_HEADERS}:
                        raise ValueError("TEMPLATE_DATA_SCHEMA_INVALID")
        if set(tables) != {"tblData", "tblCriteria", "tblCalculation", "tblRating", "tblOperator"}:
            raise ValueError("TEMPLATE_TABLES_INVALID")
        if (
            tables["tblCalculation"] != f"A3:AA{CAPACITY + 3}"
            or tables["tblRating"] != f"A3:AR{CAPACITY + 3}"
        ):
            raise ValueError("TEMPLATE_CAPACITY_INVALID")
        data_header = next(cells for row, cells in b.rows("Данные", max_row=6) if row == 6)
        from openpyxl.utils import get_column_letter

        actual_headers = tuple(
            data_header[get_column_letter(i)].value for i in range(1, len(headers) + 1)
        )
        if actual_headers != headers:
            raise ValueError("TEMPLATE_VISIBLE_HEADERS_INVALID")
        settings = {row: cells for row, cells in b.rows("Настройки", max_row=29)}
        for row in range(21, 30):
            value = settings[row]["D"].value
            if not isinstance(value, (int, float)) or not 0 <= value <= 1:
                raise ValueError("TEMPLATE_WEIGHTS_INVALID")
        # Проверять расчётный контракт, разрешая только документированный ввод.
        for sheet, columns in (("Расчет", ("J", "K")), ("Рейтинг", ("A", "E"))):
            xml = ET.fromstring(b.zip.read(b.sheets[sheet]))
            cells = {c.get("r"): c for c in xml.iter(q("c"))}
            for row in range(4, CAPACITY + 4):
                for col in columns:
                    cell = cells.get(f"{col}{row}")
                    formula = cell.find(q("f")) if cell is not None else None
                    if formula is None or not formula.text:
                        raise ValueError("PRELIMINARY_FORMULA_MISSING")
                    expected = (
                        (
                            f'IF(A{row}="","",ROUND(SUM(O{row}:W{row}),12))'
                            if col == "J"
                            else f'IF(A{row}="","",1+COUNTIF($J$4:$J${CAPACITY + 3},">"&J{row}))'
                        )
                        if sheet == "Расчет"
                        else None
                    )
                    if expected is not None and formula.text != expected:
                        raise ValueError("PRELIMINARY_FORMULA_CONFLICT")
        formulas = 0
        for name in b.sheets.values():
            x = ET.fromstring(b.zip.read(name))
            for f in x.iter(q("f")):
                text = f.text or ""
                if any(
                    token in text for token in ("#REF!", "EXP(", "LN(", "ГДИ", "ВГФ", "ГДМ", "[1]")
                ):
                    raise ValueError("STALE_OR_INVALID_FORMULA")
                formulas += 1
        if not any(
            n.startswith("xl/charts/chart") and n.endswith(".xml") for n in b.zip.namelist()
        ):
            raise ValueError("TEMPLATE_CHART_MISSING")
    return {
        "sheets": list(SHEETS),
        "tables": tables,
        "formulas": formulas,
        "capacity": CAPACITY,
        "data_schema": "water-stage-c-v3"
        if headers == ALL_DATA_HEADERS
        else "water-evidence-v2"
        if headers == DATA_HEADERS_V2
        else "water-data-v1",
        "visual": "VISUAL_REVIEW_PENDING",
        "native_recalc": "NOT_RUN",
    }


def changed_parts(source: Path, output: Path) -> list[str]:
    with ZipFile(source) as a, ZipFile(output) as b:
        return [n for n in a.namelist() if a.read(n) != b.read(n)]
