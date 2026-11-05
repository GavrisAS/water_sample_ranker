"""Reader for the matrix-shaped operational well fund workbook."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter

from water_sample_ranker.domain.models import (
    DiagnosticIssue,
    FundCategory,
    FundParseResult,
    FundWell,
    IssueSeverity,
)
from water_sample_ranker.matching.lifecycle import attach_active_fund_branches
from water_sample_ranker.matching.normalize import (
    extract_exact_plast_hint,
    normalize_field_name,
    normalize_well_id,
)
from water_sample_ranker.readers.common import (
    as_text,
    cell_location,
    coordinate,
    display_value,
    openpyxl_read_warnings,
    parse_datetime,
    sheet_location,
)

DATE_SHEET_RE = re.compile(r"^\d{2}\.\d{2}\.\d{4}$")
_COUNT_RE = re.compile(r"^\d+(?:\.0+)?$")
_NUMERIC_TOKEN_RE = re.compile(r"^\d+$")

_CATEGORIES: tuple[tuple[int, FundCategory], ...] = (
    (3, FundCategory.PRODUCING_GAS),
    (4, FundCategory.STOPPED_IN_REPORTING_MONTH),
    (7, FundCategory.INACTIVE_CURRENT_YEAR),
    (8, FundCategory.INACTIVE_PREVIOUS_YEARS),
)


def _sheet_date(title: str) -> date | None:
    if not DATE_SHEET_RE.fullmatch(title.strip()):
        return None
    parsed = parse_datetime(title.strip())
    return parsed.date() if parsed else None


def _expected_count(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    text = as_text(value)
    if text is None or _COUNT_RE.fullmatch(text) is None:
        return None
    return int(float(text))


def _split_confirmed_list_delimiters(text: str) -> list[str]:
    """Split only delimiters confirmed by the fund source format."""

    return [part.strip() for part in re.split(r"[,;\n\r]+", text) if part.strip()]


def _split_numeric_whitespace(parts: list[str]) -> tuple[list[str], bool]:
    """Build the conservative numeric-whitespace candidate for parsed parts."""

    candidate: list[str] = []
    changed = False
    for part in parts:
        whitespace_parts = [piece for piece in re.split(r"\s+", part) if piece]
        # Keep forms such as ``54 ГС`` intact.  Numeric whitespace is only a
        # candidate here; the caller must confirm it with an expected count.
        if len(whitespace_parts) > 1 and all(
            _NUMERIC_TOKEN_RE.fullmatch(piece) for piece in whitespace_parts
        ):
            candidate.extend(whitespace_parts)
            changed = True
        else:
            candidate.append(part)
    return candidate, changed


def parse_well_list_cell(value: object) -> list[str]:
    """Parse mixed Excel list cells while preserving string leading zeroes."""

    if value is None:
        return []
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [display_value(value)]
    text = str(value).replace("\u00a0", " ").strip()
    return _split_confirmed_list_delimiters(text) if text else []


def parse_well_list_cell_with_expected_count(
    value: object, expected_count: int | None
) -> tuple[list[str], bool]:
    """Parse a fund list and apply numeric-whitespace split only when confirmed.

    The boolean result reports whether the conservative whitespace correction was
    applied and is used to emit the corresponding reader diagnostic.
    """

    parsed = parse_well_list_cell(value)
    if expected_count is None or len(parsed) == expected_count:
        return parsed, False
    candidate, changed = _split_numeric_whitespace(parsed)
    if changed and len(candidate) == expected_count:
        return candidate, True
    return parsed, False


def _is_total_label(value: object) -> bool:
    text = as_text(value)
    return text is not None and ("ВСЕГО" in text.upper() or "ИТОГО" in text.upper())


def _select_sheet(
    workbook: Any, source_file: str, as_of: date | None = None
) -> tuple[Any | None, date | None, list[DiagnosticIssue]]:
    issues: list[DiagnosticIssue] = []
    candidates: list[tuple[date, str, object]] = []
    for worksheet in workbook.worksheets:  # type: ignore[attr-defined]
        title = str(worksheet.title).strip()
        if title.upper().startswith("RSPN"):
            continue
        sheet_date = _sheet_date(title)
        if sheet_date is not None and (as_of is None or sheet_date <= as_of):
            candidates.append((sheet_date, title, worksheet))
    if not candidates:
        issues.append(
            DiagnosticIssue(
                code="FUND_DATE_SHEET_NOT_FOUND",
                severity=IssueSeverity.ERROR,
                message="В фонде не найден лист с именем строго в формате DD.MM.YYYY.",
                source="fund",
                locations=(sheet_location(source_file, None),),
            )
        )
        return None, None, issues
    selected_date = max(item[0] for item in candidates)
    selected = [item for item in candidates if item[0] == selected_date]
    if len(selected) > 1:
        issues.append(
            DiagnosticIssue(
                code="FUND_DUPLICATE_DATE_SHEETS",
                severity=IssueSeverity.WARNING,
                message=(
                    f"Несколько листов фонда имеют дату {selected_date:%d.%m.%Y}; "
                    f"выбран {selected[0][1]!r}."
                ),
                source="fund",
                locations=(sheet_location(source_file, selected[0][1]),),
            )
        )
    return selected[0][2], selected_date, issues


def read_fund(path: Path, as_of: date | None = None) -> FundParseResult:
    """Read the newest dated fund sheet and build one record per C/D/G/H item."""

    issues: list[DiagnosticIssue] = []
    source_file = str(path.resolve())
    with openpyxl_read_warnings() as caught:
        workbook = load_workbook(path, read_only=False, data_only=True, keep_links=False)
    issues.extend(
        [
            DiagnosticIssue(
                code="OPENPYXL_WARNING",
                severity=IssueSeverity.WARNING,
                message=str(warning.message),
                source="fund",
                locations=(sheet_location(source_file, None),),
            )
            for warning in caught
        ]
    )

    try:
        worksheet, fund_date, selection_issues = _select_sheet(workbook, source_file, as_of)
        issues.extend(selection_issues)
        if worksheet is None or fund_date is None:
            return FundParseResult((), fund_date, None, tuple(issues))

        parsed_c1 = parse_datetime(worksheet["C1"].value)
        c1_date = parsed_c1.date() if parsed_c1 is not None else None
        if c1_date != fund_date:
            c1_text = c1_date.strftime("%d.%m.%Y") if c1_date else "не указана"
            issues.append(
                DiagnosticIssue(
                    code="FUND_DATE_MISMATCH",
                    severity=IssueSeverity.ERROR,
                    message=(
                        f"Дата C1 ({c1_text}) не совпадает с датой листа {fund_date:%d.%m.%Y}."
                    ),
                    source="fund",
                    coordinate="C1",
                    locations=(cell_location(source_file, worksheet.title, 1, 2),),
                )
            )

        wells: list[FundWell] = []
        current_field = ""
        ignored_j_reported = False
        separator_locations: set[str] = set()
        for row_number in range(1, worksheet.max_row + 1):
            object_value = worksheet.cell(row_number, 2).value
            object_name = as_text(object_value)
            if object_name is None or _is_total_label(object_name) or object_name.isdigit():
                continue
            if row_number >= worksheet.max_row:
                continue
            field_value = as_text(worksheet.cell(row_number, 1).value)
            if field_value is not None:
                current_field = field_value
            counts = {
                column: _expected_count(worksheet.cell(row_number, column).value)
                for column, _ in _CATEGORIES
            }
            list_values = {
                column: worksheet.cell(row_number + 1, column).value for column, _ in _CATEGORIES
            }
            ignored_j = parse_well_list_cell(worksheet.cell(row_number + 1, 10).value)
            if ignored_j and not ignored_j_reported:
                issues.append(
                    DiagnosticIssue(
                        code="FUND_CATEGORY_J_IGNORED",
                        severity=IssueSeverity.INFO,
                        message=(
                            "Колонка J (освоение/ожидание освоения) не включена в фонд "
                            "рейтингования: по исходному README рейтингованию подлежит "
                            "действующий + бездействующий фонд."
                        ),
                        source="fund",
                        coordinate=coordinate(row_number + 1, 9),
                        locations=(cell_location(source_file, worksheet.title, row_number + 1, 9),),
                    )
                )
                ignored_j_reported = True
            has_fund_shape = any(count is not None for count in counts.values())
            if not has_fund_shape:
                continue
            if not current_field:
                issues.append(
                    DiagnosticIssue(
                        code="FUND_FIELD_MISSING",
                        severity=IssueSeverity.ERROR,
                        message=f"Для объекта {object_name!r} не определено месторождение/ЛУ.",
                        source="fund",
                        coordinate=coordinate(row_number, 1),
                        locations=(cell_location(source_file, worksheet.title, row_number, 1),),
                    )
                )
            object_hint = extract_exact_plast_hint(object_name)
            for column, category in _CATEGORIES:
                list_value = list_values[column]
                expected = counts[column]
                list_cell = coordinate(row_number + 1, column - 1)
                parsed, separator_normalized = parse_well_list_cell_with_expected_count(
                    list_value, expected
                )
                if separator_normalized and list_cell not in separator_locations:
                    issues.append(
                        DiagnosticIssue(
                            code="FUND_MISSING_SEPARATOR_NORMALIZED",
                            severity=IssueSeverity.INFO,
                            message=(
                                "Обнаружены два числовых идентификатора, разделенные "
                                "пробелом; значение разделено на две скважины на основании "
                                "структуры текущего фонда."
                            ),
                            source="fund",
                            coordinate=list_cell,
                            locations=(
                                cell_location(
                                    source_file,
                                    worksheet.title,
                                    row_number + 1,
                                    column - 1,
                                ),
                            ),
                        )
                    )
                    separator_locations.add(list_cell)
                if expected is not None and expected != len(parsed):
                    issues.append(
                        DiagnosticIssue(
                            code="FUND_COUNT_MISMATCH",
                            severity=IssueSeverity.WARNING,
                            message=(
                                f"Категория {category.value!r}, объект {object_name!r}: в строке "
                                f"указано {expected}, распознано {len(parsed)} элементов."
                            ),
                            source="fund",
                            coordinate=coordinate(row_number, column - 1),
                            locations=(
                                cell_location(
                                    source_file,
                                    worksheet.title,
                                    row_number,
                                    column - 1,
                                ),
                            ),
                        )
                    )
                for raw_value in parsed:
                    normalized = normalize_well_id(raw_value)
                    wells.append(
                        FundWell(
                            raw_display=raw_value,
                            base_well_id=normalized.base_well_id,
                            field_name=current_field,
                            normalized_field=normalize_field_name(current_field),
                            target_well_id=normalized.target_well_id,
                            object_name=object_name,
                            source_category=category,
                            fund_date=fund_date,
                            source_row=row_number + 1,
                            source_column=get_column_letter(column),
                            object_plast_hint=object_hint,
                            source_file=source_file,
                            source_sheet=worksheet.title,
                            field_cell=coordinate(row_number, 0),
                            well_cell=coordinate(row_number + 1, column - 1),
                            plast_cell=coordinate(row_number, 1),
                        )
                    )
                    if normalized.base_well_id is None:
                        issues.append(
                            DiagnosticIssue(
                                code="FUND_WELL_ID_UNRESOLVED",
                                severity=IssueSeverity.WARNING,
                                message=f"Не удалось нормализовать номер скважины {raw_value!r}.",
                                source="fund",
                                coordinate=coordinate(row_number + 1, column - 1),
                                locations=(
                                    cell_location(
                                        source_file,
                                        worksheet.title,
                                        row_number + 1,
                                        column - 1,
                                    ),
                                ),
                            )
                        )
        duplicate_wells: dict[tuple[str | None, str], list[FundWell]] = {}
        for well in wells:
            if well.target_well_id is not None:
                duplicate_wells.setdefault((well.target_well_id, well.normalized_field), []).append(
                    well
                )
        for (well_id, field_name), occurrences in duplicate_wells.items():
            count = len(occurrences)
            if count > 1:
                issues.append(
                    DiagnosticIssue(
                        code="FUND_DUPLICATE_WELL",
                        severity=IssueSeverity.WARNING,
                        message=(
                            f"В выбранном фонде целевая идентичность {well_id} повторяется "
                            f"{count} раз "
                            f"в поле {field_name!r}."
                        ),
                        source="fund",
                        well_id=well_id,
                        locations=tuple(
                            location for well in occurrences for location in well.locations
                        ),
                    )
                )
        return FundParseResult(
            attach_active_fund_branches(tuple(wells)), fund_date, worksheet.title, tuple(issues)
        )
    finally:
        workbook.close()
