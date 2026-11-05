"""Structural checks are independent of native Excel recalculation/rendering."""

import xml.etree.ElementTree as ET
from pathlib import Path
from zipfile import ZipFile

from water_sample_ranker.criteria.rules import WEIGHTS
from water_sample_ranker.excel.layout import CAPACITY, DATA_HEADERS, DATA_HEADERS_V2, SHEETS
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
                    if headers not in {DATA_HEADERS, DATA_HEADERS_V2}:
                        raise ValueError("TEMPLATE_DATA_SCHEMA_INVALID")
        if set(tables) != {"tblData", "tblCriteria", "tblCalculation", "tblRating"}:
            raise ValueError("TEMPLATE_TABLES_INVALID")
        if (
            tables["tblCalculation"] != f"A3:N{CAPACITY + 3}"
            or tables["tblRating"] != f"A3:I{CAPACITY + 3}"
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
        if tuple(settings[row]["D"].value for row in range(21, 30)) != WEIGHTS:
            raise ValueError("TEMPLATE_WEIGHTS_INVALID")
        for sheet, columns in (("Расчет", ("J", "K")), ("Рейтинг", ("A", "E"))):
            x = ET.fromstring(b.zip.read(b.sheets[sheet]))
            cells = {c.get("r"): c for c in x.iter(q("c"))}
            for row in range(4, CAPACITY + 4):
                for col in columns:
                    c = cells.get(f"{col}{row}")
                    f = c.find(q("f")) if c is not None else None
                    if f is None or f.text != '""':
                        raise ValueError("UNAPPROVED_SCORE_OR_RANK")
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
        "data_schema": "water-evidence-v2" if headers == DATA_HEADERS_V2 else "water-data-v1",
        "visual": "VISUAL_REVIEW_PENDING",
        "native_recalc": "PENDING",
    }


def changed_parts(source: Path, output: Path) -> list[str]:
    with ZipFile(source) as a, ZipFile(output) as b:
        return [n for n in a.namelist() if a.read(n) != b.read(n)]
