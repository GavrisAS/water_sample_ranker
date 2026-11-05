"""Water-specific LHA mapping; no old per-metric maximum or VGF policy."""

import re
from pathlib import Path

from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, from_excel

from water_sample_ranker.domain.measurements import valid_measurement
from water_sample_ranker.domain.models import (
    DiagnosticIssue,
    Evidence,
    IssueSeverity,
    Sample,
    SourceLocation,
)
from water_sample_ranker.matching.normalize import (
    normalize_field_name,
    normalize_plast,
    normalize_well_id,
)
from water_sample_ranker.readers.common import normalized_header, parse_datetime, parse_number
from water_sample_ranker.readers.ooxml import Book, Cell

SCHEMAS = {
    "БД Сибнефтегаз": (
        2,
        {
            "field": "C",
            "well": "D",
            "plast": "E",
            "date": "G",
            "water": "L",
            "mineralization": "W",
            "chlorine": "Z",
        },
    ),
    "Для слайда": (
        28,
        {
            "field": "D",
            "well": "B",
            "plast": "C",
            "date": "E",
            "water": "H",
            "mineralization": "K",
            "chlorine": "N",
            "conclusion": "AB",
        },
    ),
}
UNITS = {"water": "л/сут", "chlorine": "мг/дм³", "mineralization": "мг/дм³"}


def read_lha(path: Path) -> tuple[tuple[Sample, ...], tuple[DiagnosticIssue, ...]]:
    records: list[Sample] = []
    issues: list[DiagnosticIssue] = []
    with Book(path) as book:
        for name, (start, mapping) in SCHEMAS.items():
            if name not in book.sheets:
                issues.append(
                    DiagnosticIssue(
                        "LHA_SHEET_MISSING",
                        IssueSeverity.ERROR,
                        f"Нет листа {name}",
                        locations=(SourceLocation(str(path), name),),
                    )
                )
                continue
            headers = {n: cells for n, cells in book.rows(name, max_row=start - 1)}
            header = headers.get(1 if start == 2 else 27, {})
            expected = {
                "field": "месторождение",
                "plast": "пласт",
                "well": "номер скважины",
                "date": "дата и время исследования" if start == 2 else "дата отбора",
            }
            if start == 28:
                # Подтверждено по реальному источнику: AB27, без поиска соседних колонок.
                expected["conclusion"] = "заключение"
            mismatches = tuple(
                SourceLocation(str(path), name, f"{mapping[k]}{start - 1}")
                for k, v in expected.items()
                if header.get(mapping[k], Cell("")).status != "VALID"
                or header.get(mapping[k], Cell("")).formula is not None
                or normalized_header(header.get(mapping[k], Cell("")).value) != v
            )
            if mismatches:
                issues.append(
                    DiagnosticIssue(
                        "LHA_SCHEMA_DRIFT",
                        IssueSeverity.ERROR,
                        "Не подтверждены фиксированные заголовки ЛХА, включая AB27 заключения",
                        locations=mismatches,
                    )
                )
                continue
            numeric_headers = {
                "water": "дебитводы",
                "mineralization": "минерализация",
                "chlorine": "cl",
            }

            def compact(value: object) -> str:
                return re.sub(r"[\s,\-]", "", normalized_header(value))

            if any(
                token not in compact(header.get(mapping[key], Cell("")).value)
                for key, token in numeric_headers.items()
            ):
                issues.append(
                    DiagnosticIssue(
                        "LHA_MEASUREMENT_SCHEMA_DRIFT",
                        IssueSeverity.ERROR,
                        "Не подтверждены колонки воды/минерализации/хлора",
                        locations=(SourceLocation(str(path), name),),
                    )
                )
                continue
            for row, cells in book.rows(name):
                if row < start:
                    continue
                selected = {
                    key: cells.get(col, Cell(f"{col}{row}")) for key, col in mapping.items()
                }
                well_raw = selected["well"].value
                if well_raw is None or str(well_raw).startswith("#") or well_raw == 0:
                    continue
                parsed = normalize_well_id(well_raw)
                if not parsed.target_well_id or not parsed.base_well_id:
                    continue
                d = selected["date"].value
                if isinstance(d, (int, float)) and not isinstance(d, bool):
                    try:
                        d = from_excel(
                            d, CALENDAR_MAC_1904 if book.epoch1904 else CALENDAR_WINDOWS_1900
                        )
                    except ValueError, OverflowError:
                        d = None
                measured = parse_datetime(d)
                # Formula placeholders evaluate to 1900/zero; they are not observations.
                if measured is not None and measured.year < 1950:
                    measured = None
                evidence: list[Evidence] = []
                measurements: dict[str, float | None] = {}
                invalid: list[str] = []
                for key, cell in selected.items():
                    unit = "м³/сут" if key == "water" and start == 28 else UNITS.get(key, "")
                    status = cell.status if key != "date" or measured else "INVALID_DATE"
                    if key in UNITS:
                        number = parse_number(cell.value) if cell.status == "VALID" else None
                        # Отсутствие/cache/error сохраняют собственный статус; отрицательное
                        # число — отдельный семантический дефект конкретного исследования.
                        if number is not None and not valid_measurement(number):
                            status = "INVALID_MEASUREMENT"
                            invalid.append(key)
                            issues.append(
                                DiagnosticIssue(
                                    "INVALID_MEASUREMENT",
                                    IssueSeverity.WARNING,
                                    f"Отрицательное измерение {key}; зависимые признаки UNKNOWN",
                                    source=name,
                                    coordinate=cell.coordinate,
                                    well_id=parsed.target_well_id,
                                    locations=(SourceLocation(str(path), name, cell.coordinate),),
                                )
                            )
                            number = None
                        measurements[key] = number
                    evidence.append(
                        Evidence(
                            SourceLocation(str(path), name, cell.coordinate),
                            cell.formula if cell.formula else cell.value,
                            cell.value,
                            unit,
                            status,
                            cell.formula,
                            "day" if key == "date" and measured else None,
                            key if key in UNITS else None,
                        )
                    )
                if measured is None:
                    issues.append(
                        DiagnosticIssue(
                            "LHA_INVALID_DATE",
                            IssueSeverity.WARNING,
                            "Дата пробы отсутствует/невалидна; актуальность неизвестна",
                            locations=(
                                SourceLocation(str(path), name, selected["date"].coordinate),
                            ),
                        )
                    )
                water = measurements["water"]
                if start == 28 and water is not None:
                    water *= 1000
                conclusion_cell = selected.get("conclusion", Cell(""))
                conclusion = (
                    str(conclusion_cell.value)
                    if conclusion_cell.status == "VALID"
                    and conclusion_cell.value not in (None, "", 0)
                    else None
                )
                records.append(
                    Sample(
                        normalize_field_name(selected["field"].value or ""),
                        parsed.target_well_id,
                        parsed.base_well_id,
                        normalize_plast(selected["plast"].value),
                        measured,
                        measurements["chlorine"],
                        measurements["mineralization"],
                        water,
                        conclusion,
                        tuple(evidence),
                        "database" if start == 2 else "slide",
                        tuple(invalid),
                    )
                )
    return tuple(records), tuple(issues)
