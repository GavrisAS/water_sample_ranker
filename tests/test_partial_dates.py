from __future__ import annotations

from datetime import datetime

import pytest

from water_sample_ranker.domain.dates import (
    date_defaulting_rule,
    definitely_after,
    definitely_before,
    effective_calendar_date,
    effective_date,
    overlaps_same_precision_window,
    parse_partial_date,
)
from water_sample_ranker.domain.models import DatePrecision, PartialDate


@pytest.mark.parametrize(
    ("raw", "number_format", "expected"),
    [
        (3.2002, "General", PartialDate(2002, 3, None, DatePrecision.MONTH)),
        (8.2023, "General", PartialDate(2023, 8, None, DatePrecision.MONTH)),
        (7.202, "0.0000", PartialDate(2020, 7, None, DatePrecision.MONTH)),
        (
            datetime(2025, 9, 16),
            "dd.mm.yyyy",
            PartialDate(2025, 9, 16, DatePrecision.DAY),
        ),
        (2015, "General", PartialDate(2015, None, None, DatePrecision.YEAR)),
    ],
)
def test_partial_date_retains_source_precision(
    raw: object, number_format: str, expected: PartialDate
) -> None:
    assert parse_partial_date(raw, number_format) == expected


@pytest.mark.parametrize("raw", [13.2020, 0.2020, 7.202, "13.2020", "7.202"])
def test_invalid_or_under_precision_month_year_is_not_guessed(raw: object) -> None:
    assert parse_partial_date(raw, "General") is None


def test_partial_month_defaults_to_first_day_for_business_comparisons() -> None:
    launch = PartialDate(2023, 6, None, DatePrecision.MONTH)
    later = datetime(2023, 8, 15)
    same_month = datetime(2023, 6, 15)

    assert definitely_after(later, launch)
    assert not definitely_before(later, launch)
    assert definitely_after(same_month, launch)
    assert not overlaps_same_precision_window(same_month, launch)
    assert effective_date(launch).isoformat() == "2023-06-01"
    assert date_defaulting_rule(launch) == "month_to_first_day"


def test_partial_month_dates_keep_provenance_but_compare_by_effective_day() -> None:
    june = PartialDate(2023, 6, None, DatePrecision.MONTH)
    same_june = PartialDate(2023, 6, None, DatePrecision.MONTH)
    july = PartialDate(2023, 7, None, DatePrecision.MONTH)

    assert effective_date(june) == effective_date(same_june)
    assert overlaps_same_precision_window(june, same_june)
    assert definitely_before(june, july)


def test_year_date_defaults_to_january_first_including_leap_year() -> None:
    year = PartialDate(2024, None, None, DatePrecision.YEAR)

    assert effective_date(year).isoformat() == "2024-01-01"
    assert date_defaulting_rule(year) == "year_to_january_first"


def test_day_precision_and_leap_day_remain_unchanged() -> None:
    leap_day = PartialDate(2024, 2, 29, DatePrecision.DAY)

    assert effective_date(leap_day).isoformat() == "2024-02-29"
    assert effective_calendar_date(leap_day).isoformat() == "2024-02-29"
