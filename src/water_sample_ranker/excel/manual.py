"""Ручные отметки: точная identity стадии, архив без переноса между стадиями."""

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from water_sample_ranker.domain.models import RankedWell

OPERATOR_HEADERS = (
    "Ключ",
    "Месторождение",
    "Скважина",
    "Стадия",
    "Пласт",
    "Отбор пробы после ГТМ",
    "Дата увеличения диаметра",
    "Диаметр до мм",
    "Диаметр после мм",
    "Основание диаметра",
)


def stage_token(row: RankedWell) -> str:
    s = row.identity.stage
    if s is None or row.identity.status != "RESOLVED":
        return ""
    return json.dumps(
        [s.target_well_id, s.event_kind.value, s.started_at.to_dict(), s.ordinal],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def identity_key(row: RankedWell) -> str:
    token = stage_token(row)
    return (
        hashlib.sha256(
            json.dumps(
                [
                    row.identity.fund.normalized_field,
                    row.identity.display,
                    token,
                    row.identity.plast,
                ],
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if token
        else ""
    )


def read_marks(path: Path) -> dict[str, tuple[object, ...]]:
    """Пустой шаблон допустим; дубли/неизвестная схема отклоняются до публикации."""
    import xml.etree.ElementTree as ET
    from zipfile import ZipFile

    from openpyxl.utils.cell import range_boundaries

    with ZipFile(path) as z:
        tables = [
            ET.fromstring(z.read(n))
            for n in z.namelist()
            if n.startswith("xl/tables/") and n.endswith(".xml")
        ]
        table = next((t for t in tables if t.get("displayName") == "tblOperator"), None)
        if table is None:
            return {}
        left, top, right, bottom = range_boundaries(table.get("ref", ""))
    b = load_workbook(path, data_only=False, keep_links=False, read_only=True)
    try:
        s = b["Настройки"]
        rows = list(s.iter_rows(min_row=top, max_row=bottom, min_col=left, max_col=right))
        if tuple(c.value for c in rows[0]) != OPERATOR_HEADERS:
            raise ValueError("MANUAL_SCHEMA_CONFLICT: схема ручных отметок")
        marks = {}
        for cells in rows[1:]:
            vals = tuple(c.value for c in cells)
            if not any(v is not None for v in vals):
                continue
            key = vals[0]
            if not isinstance(key, str) or key in marks:
                raise ValueError("MANUAL_DUPLICATE_OR_MISSING_IDENTITY")
            if any(c.data_type == "f" for c in cells):
                raise ValueError("MANUAL_FORMULA_NOT_ALLOWED")
            try:
                expected = json.dumps(
                    [vals[1], str(vals[2]), vals[3], vals[4]],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if key != hashlib.sha256(expected.encode()).hexdigest() or not vals[3]:
                    raise ValueError("MANUAL_IDENTITY_MISMATCH")
                if vals[5] not in {None, "Да", "Нет", "Не задано"}:
                    raise ValueError("MANUAL_GTM_INVALID")
                if vals[6] is not None and not isinstance(vals[6], (date, datetime)):
                    raise ValueError("MANUAL_EVENT_DATE_INVALID")
                for value in vals[7:9]:
                    if value is not None and (
                        type(value) not in {int, float} or not 0 < value < 1000
                    ):
                        raise ValueError("MANUAL_DIAMETER_INVALID")
            except (TypeError, ValueError) as e:
                raise ValueError("MANUAL_CONFLICT: проверьте таблицу отметок") from e
            marks[key] = vals
        return marks
    finally:
        b.close()


def merge_marks(
    rows: tuple[RankedWell, ...], previous: dict[str, tuple[object, ...]]
) -> dict[str, tuple[object, ...]]:
    marks = previous.copy()
    for row in rows:
        key = identity_key(row)
        if key and key not in marks:
            marks[key] = (
                key,
                row.identity.fund.normalized_field,
                row.identity.display,
                stage_token(row),
                row.identity.plast,
                "Не задано",
                None,
                None,
                None,
                None,
            )
    return marks


def read_operator_settings(path: Path) -> dict[str, Any]:
    b = load_workbook(path, data_only=False, keep_links=False, read_only=True)
    try:
        s = b["Настройки"]
        if s["B5"].value != "water-stage-C-provisional":
            return {}
        values = {f"{c}{r}": s[f"{c}{r}"].value for r in range(21, 29) for c in ("D", "I")}
        values["B13"] = s["B13"].value
        for r in range(21, 29):
            v = values[f"D{r}"]
            if (
                type(v) not in {float, int}
                or not 0 <= v <= 1
                or values[f"I{r}"] not in {"Да", "Нет"}
            ):
                raise ValueError("OPERATOR_SETTINGS_INVALID")
        if values["B13"] not in {"Полные календарные месяцы", "Не подтверждено"}:
            raise ValueError("MONTH_MODE_INVALID")
        return values
    finally:
        b.close()


def manual_sources(path: Path) -> dict[str, Path]:
    """Явно выбранный каталог предыдущего пакета; одна книга на месторождение."""
    sources = {}
    for p in sorted(path.glob("*.xlsx")):
        b = load_workbook(p, read_only=True, data_only=False, keep_links=False)
        try:
            field = b["Настройки"]["B8"].value
        finally:
            b.close()
        if not isinstance(field, str) or not field or field in sources:
            raise ValueError("MANUAL_SOURCE_FIELD_CONFLICT")
        sources[field] = p
    if not sources:
        raise ValueError("MANUAL_DIRECTORY_EMPTY")
    return sources


_read_one_marks = read_marks
_read_one_settings = read_operator_settings


def read_marks(path: Path) -> dict[str, tuple[object, ...]]:
    if path.is_file():
        return _read_one_marks(path)
    if not path.is_dir():
        raise ValueError("MANUAL_SOURCE_MISSING")
    marks = {}
    for field, source in manual_sources(path).items():
        for key, values in _read_one_marks(source).items():
            if values[1] != field:
                continue
            if key in marks:
                raise ValueError("MANUAL_DUPLICATE_SOURCE")
            marks[key] = values
    return marks


def read_operator_settings(path: Path, field: str | None = None) -> dict[str, Any]:
    if path.is_file():
        return _read_one_settings(path)
    sources = manual_sources(path)
    if field is None:
        return {}
    return _read_one_settings(sources[field]) if field in sources else {}
