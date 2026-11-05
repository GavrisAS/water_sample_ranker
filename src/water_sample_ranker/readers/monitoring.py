"""Diagnostic-only GSS view: values are never criterion-7 decisions."""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from water_sample_ranker.domain.evidence import ParameterObservation, Reason
from water_sample_ranker.domain.models import Evidence, SourceLocation
from water_sample_ranker.matching.normalize import normalize_field_name, normalize_plast
from water_sample_ranker.readers.common import (
    as_text,
    normalized_header,
    parse_datetime,
    parse_number,
)
from water_sample_ranker.readers.ooxml import Book, Cell

SHEET = "Техрежим скважин"


@dataclass(frozen=True, slots=True)
class MonitoringHistory:
    observations: tuple[ParameterObservation, ...] = ()
    reasons: tuple[str, ...] = ()
    groups: tuple[dict[str, Any], ...] = ()
    choke_rows: int = 0
    future_cells: int = 0


def read_history(path: Path, as_of: date) -> MonitoringHistory:
    """Контракт GSS-v1: A1 field, A2/E2, B3:D3, даты F3→, A параметр.

    0/66/109 — именованная группа подозрений из обследования источника, не
    физический порог. Они сохраняются, но не являются доверенными диаметрами.
    Ни формулы, ни внешние ссылки не выполняются; читается только cache.
    """
    with Book(path) as book:
        if SHEET not in book.sheets:
            return MonitoringHistory(reasons=(Reason.SCHEMA_DRIFT,))
        headers = dict(book.rows(SHEET, 3))
        h1, h2, h3 = (headers.get(i, {}) for i in (1, 2, 3))

        def value(cells: dict[str, Cell], col: str) -> object:
            c = cells.get(col)
            return c.value if c and c.status == "VALID" else None

        field = normalize_field_name(value(h1, "A") or "")
        if (
            field not in {"береговое", "пырейное", "хадырьяхинское"}
            or normalized_header(value(h2, "A")) != "параметр"
            or normalized_header(value(h2, "E")) != "объект"
            or tuple(normalized_header(value(h3, c)) for c in ("B", "C", "D"))
            != ("гск", "куста", "скв.")
        ):
            return MonitoringHistory(reasons=(Reason.SCHEMA_DRIFT,))
        dates: dict[str, datetime] = {}
        for col, cell in h3.items():
            if col in {"A", "B", "C", "D", "E"}:
                continue
            stamp = parse_datetime(cell.value) if cell.status == "VALID" else None
            if not stamp:
                return MonitoringHistory(reasons=(Reason.SCHEMA_DRIFT,))
            # Book preserves Excel serials; support the workbook's explicit epoch.
            if book.epoch1904 and isinstance(cell.value, (int, float)):
                from datetime import timedelta

                stamp += timedelta(days=1462)
            if not 2000 <= stamp.year <= 2100:
                return MonitoringHistory(reasons=(Reason.SCHEMA_DRIFT,))
            dates[col] = stamp
        if not dates:
            return MonitoringHistory(reasons=(Reason.SCHEMA_DRIFT,))
        observations = []
        counts: dict[datetime, Counter[str]] = {}
        choke_rows = future = 0
        for n, cells in book.rows(SHEET):
            label = normalized_header(value(cells, "A"))
            if n <= 3 or "штуцера" not in label:
                continue
            choke_rows += 1
            unit = "мм" if label in {"типоразмер штуцера, мм", "размер штуцера, мм"} else ""
            locations = tuple(SourceLocation(str(path), SHEET, f"{c}{n}") for c in "BCDE")
            for col, stamp in dates.items():
                if stamp.date() > as_of:
                    future += 1
                    continue
                cell = cells.get(col, Cell(f"{col}{n}"))
                number = parse_number(cell.value) if cell.status == "VALID" else None
                quality = []
                if cell.status != "VALID":
                    quality.append(cell.status)
                elif number is None or number < 0:
                    quality.append("INVALID_MEASUREMENT")
                if not unit:
                    quality.append("INVALID_UNIT")
                if number in {0, 66, 109}:
                    quality.append(Reason.SUSPECT_VALUE_SEMANTICS)
                counts.setdefault(stamp, Counter())[
                    str(int(number)) if number in {0, 66, 109} else "other"
                ] += 1
                observations.append(
                    ParameterObservation(
                        field,
                        as_text(value(cells, "D")) or "",
                        as_text(value(cells, "B")),
                        as_text(value(cells, "C")),
                        as_text(value(cells, "E")),
                        normalize_plast(value(cells, "E")),
                        stamp,
                        Evidence(
                            SourceLocation(str(path), SHEET, cell.coordinate),
                            cell.value,
                            number,
                            unit,
                            cell.status,
                            cell.formula,
                            "day",
                            "choke",
                        ),
                        SourceLocation(str(path), SHEET, f"{col}3"),
                        locations,
                        tuple(quality),
                    )
                )
        groups = tuple(
            {
                "date": stamp.isoformat(),
                "value_group": sentinel,
                "count": counter[sentinel],
                "total": sum(counter.values()),
                "share": counter[sentinel] / sum(counter.values()),
                "all_rows_zero": counter["0"] == choke_rows,
                "header_locations": tuple(
                    SourceLocation(str(path), SHEET, f"{c}3")
                    for c, d in dates.items()
                    if d == stamp
                ),
            }
            for stamp, counter in sorted(counts.items())
            for sentinel in ("0", "66", "109")
            if counter[sentinel]
        )
        mass_dates = {stamp for stamp, counter in counts.items() if counter["0"] == choke_rows}
        observations = [
            replace(o, quality=(*o.quality, Reason.ANOMALOUS_SERIES))
            if o.timestamp in mass_dates
            else o
            for o in observations
        ]
        return MonitoringHistory(
            tuple(observations),
            () if choke_rows else (Reason.SCHEMA_DRIFT,),
            groups,
            choke_rows,
            future,
        )


def diagnose(path: Path) -> dict[str, Any]:
    labels = []
    dates = []
    anomalies = Counter()
    zero_by_column = Counter()
    choke_rows = 0
    with Book(path) as b:
        for row, cells in b.rows("Техрежим скважин"):
            texts = [str(c.value or "") for c in cells.values()]
            if row < 12:
                labels.append(
                    {
                        "row": row,
                        "cells": {k: c.value for k, c in cells.items() if c.value is not None},
                    }
                )
                for col, c in cells.items():
                    d = parse_datetime(c.value)
                    if d and 2000 <= d.year <= 2100:
                        dates.append({"column": col, "date": d.isoformat()})
            if not any("ипоразмер штуцера" in text for text in texts):
                continue
            choke_rows += 1
            for col, c in cells.items():
                if col in {"A", "B", "C", "D", "E"}:
                    continue
                numeric = parse_number(c.value)
                if numeric in (0, 66, 109):
                    anomalies[str(int(numeric))] += 1
                    if numeric == 0:
                        zero_by_column[col] += 1
    return {
        "diagnostic_only": True,
        "criterion_7": "UNKNOWN",
        "choke_rows": choke_rows,
        "sentinel_frequencies": dict(anomalies),
        "zero_by_column": dict(zero_by_column),
        "date_headers": dates,
        "header_preview": labels,
        "reason": "Window, mass zeros and 66/109 semantics require confirmation",
    }
