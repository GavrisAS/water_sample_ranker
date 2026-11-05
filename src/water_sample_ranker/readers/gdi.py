"""Summary GDI is identity evidence only; it is not water eligibility."""

from pathlib import Path
from typing import Any

from water_sample_ranker.domain.models import IdentityObservation
from water_sample_ranker.matching.normalize import (
    normalize_field_name,
    normalize_plast,
    normalize_well_id,
)
from water_sample_ranker.readers.common import normalized_header, parse_datetime
from water_sample_ranker.readers.ooxml import Book, Cell


def read_gdi(path: Path) -> tuple[IdentityObservation, ...]:
    records = []
    sheet = "ГДИС_Сибнефтегаз в целом"
    with Book(path) as book:
        if sheet not in book.sheets:
            raise ValueError(f"GDI_SCHEMA_DRIFT: нет {sheet}")
        header = next(iter(book.rows(sheet, max_row=1)), (0, {}))[1]
        required = {
            "D": "месторождение",
            "E": "скважина",
            "H": "пласт",
            "Q": "вид исследования",
            "T": "дата",
        }
        if any(
            normalized_header(header.get(col, Cell("")).value) != label
            for col, label in required.items()
        ):
            raise ValueError("GDI_SCHEMA_DRIFT: заголовки D/E/H/Q/T")
        for row, cells in book.rows(sheet):

            def value(col: str, cells: Any = cells) -> object:
                return cells.get(col, Cell("")).value

            day = parse_datetime(value("T"))
            if day is None or value("E") is None:
                continue
            well = normalize_well_id(value("E"))
            records.append(
                IdentityObservation(
                    str(value("E")),
                    well.target_well_id,
                    normalize_field_name(value("D")),
                    normalize_plast(value("H")),
                    day,
                    str(path),
                    sheet,
                    f"T{row}",
                )
            )
    return tuple(records)
