"""Lossless partial calendar dates and conservative temporal comparisons."""

from __future__ import annotations

import re
from calendar import monthrange
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum

from water_sample_ranker.domain.models import DatePrecision, PartialDate

_YEAR_ONLY = re.compile(r"^(\d{4})$")
_MONTH_YEAR = re.compile(r"^(\d{1,2})[./](\d{4})$")
_FORMAT_DECIMALS = re.compile(r"0\.([0#]+)")


def parse_partial_date(value: object, number_format: str = "General") -> PartialDate | None:
    """Parse Excel date cells without manufacturing a day for partial dates.

    Numeric month/year cells are read as ``M.YYYY``. For formatted numeric
    cells, the decimal places from ``number_format`` determine the displayed
    year digits (for example, 7.202 with ``0.0000`` is 7.2020).
    """

    if isinstance(value, datetime):
        return PartialDate(value.year, value.month, value.day, DatePrecision.DAY)
    if isinstance(value, date):
        return PartialDate(value.year, value.month, value.day, DatePrecision.DAY)
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            number = Decimal(str(value))
        except InvalidOperation:
            return None
        if not number.is_finite():
            return None
        if number == number.to_integral_value():
            year = int(number)
            return _make_partial_date(year, None, None, DatePrecision.YEAR)
        format_match = _FORMAT_DECIMALS.search(number_format)
        exponent = number.as_tuple().exponent
        places = (
            len(format_match.group(1))
            if format_match
            else max(0, -exponent)
            if isinstance(exponent, int)
            else 0
        )
        displayed = number.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
        month_part, _, year_part = f"{displayed:.{places}f}".partition(".")
        if not year_part:
            return None
        return _month_year(int(month_part), year_part)

    text = str(value).strip()
    year_match = _YEAR_ONLY.fullmatch(text)
    if year_match:
        return _make_partial_date(int(year_match.group(1)), None, None, DatePrecision.YEAR)
    month_match = _MONTH_YEAR.fullmatch(text)
    if month_match:
        return _month_year(int(month_match.group(1)), month_match.group(2))
    for pattern in ("%d.%m.%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text, pattern)
            return PartialDate(parsed.year, parsed.month, parsed.day, DatePrecision.DAY)
        except ValueError:
            continue
    return None


def _make_partial_date(
    year: int,
    month: int | None,
    day: int | None,
    precision: DatePrecision,
) -> PartialDate | None:
    if year < 1 or year > 9999:
        return None
    try:
        return PartialDate(year, month, day, precision)
    except ValueError:
        return None


def _month_year(month: int, year_digits: str) -> PartialDate | None:
    if len(year_digits) != 4:
        return None
    return _make_partial_date(int(year_digits), month, None, DatePrecision.MONTH)


DateLike = PartialDate | date | datetime


class CutoffRelation(StrEnum):
    ON_OR_BEFORE = "ON_OR_BEFORE_CUTOFF"
    AFTER = "AFTER_CUTOFF"
    STRADDLES = "STRADDLES_CUTOFF"
    UNKNOWN = "UNKNOWN_DATE"


@dataclass(frozen=True, slots=True)
class CalendarInterval:
    """Inclusive calendar bounds; no time zone or first-day assumption."""

    lower: date
    upper: date
    precision: DatePrecision

    def relation(self, cutoff: date) -> CutoffRelation:
        if type(cutoff) is not date:
            raise TypeError("CUTOFF_REQUIRES_CALENDAR_DATE")
        if self.upper <= cutoff:
            return CutoffRelation.ON_OR_BEFORE
        if self.lower > cutoff:
            return CutoffRelation.AFTER
        return CutoffRelation.STRADDLES


def calendar_interval(value: object) -> CalendarInterval | None:
    """Unsupported/absent dates are unknown. datetime needs an explicit caller policy."""
    if type(value) is date:
        return CalendarInterval(value, value, DatePrecision.DAY)
    if not isinstance(value, PartialDate):
        return None
    if value.precision is DatePrecision.YEAR:
        return CalendarInterval(date(value.year, 1, 1), date(value.year, 12, 31), value.precision)
    if value.precision is DatePrecision.MONTH and value.month is not None:
        return CalendarInterval(
            date(value.year, value.month, 1),
            date(value.year, value.month, monthrange(value.year, value.month)[1]),
            value.precision,
        )
    if value.precision is DatePrecision.DAY and value.month is not None and value.day is not None:
        exact = date(value.year, value.month, value.day)
        return CalendarInterval(exact, exact, value.precision)
    return None


def cutoff_relation(value: object, cutoff: date) -> CutoffRelation:
    interval = calendar_interval(value)
    return interval.relation(cutoff) if interval else CutoffRelation.UNKNOWN


def effective_calendar_date(value: DateLike) -> date:
    """Return the approved business day while retaining source precision."""

    if isinstance(value, PartialDate):
        return effective_date(value)
    return value.date() if isinstance(value, datetime) else value


def effective_date(value: PartialDate) -> date:
    """Apply the customer-approved first-day rule while retaining raw precision."""

    if value.precision is DatePrecision.YEAR:
        return date(value.year, 1, 1)
    if value.precision is DatePrecision.MONTH:
        assert value.month is not None
        return date(value.year, value.month, 1)
    assert value.month is not None and value.day is not None
    return date(value.year, value.month, value.day)


def date_defaulting_rule(value: PartialDate) -> str:
    """Describe the effective-date rule separately from source precision."""

    return {
        DatePrecision.DAY: "source_day_unchanged",
        DatePrecision.MONTH: "month_to_first_day",
        DatePrecision.YEAR: "year_to_january_first",
    }[value.precision]


def date_window(value: DateLike) -> tuple[datetime, datetime]:
    """Return the business-effective calendar day while source precision stays intact."""

    if isinstance(value, PartialDate):
        effective = effective_date(value)
        exact = datetime.combine(effective, time.min)
        return exact, exact.replace(hour=23, minute=59, second=59, microsecond=999999)
    if isinstance(value, datetime):
        return value, value
    exact = datetime.combine(value, time.min)
    return exact, exact.replace(hour=23, minute=59, second=59, microsecond=999999)


def definitely_before(left: DateLike, right: DateLike) -> bool:
    """Return true only if every instant in left precedes every instant in right."""

    _, left_end = date_window(left)
    right_start, _ = date_window(right)
    return left_end < right_start


def definitely_after(left: DateLike, right: DateLike) -> bool:
    """Return true only if every instant in left follows every instant in right."""

    left_start, _ = date_window(left)
    _, right_end = date_window(right)
    return left_start > right_end


def overlaps_same_precision_window(left: DateLike, right: DateLike) -> bool:
    """Return true when source precision does not establish a strict order."""

    return not definitely_before(left, right) and not definitely_after(left, right)
