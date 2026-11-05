from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from water_sample_ranker.criteria.rules import (
    chlorine,
    disputed,
    duration,
    separator,
    technical,
    water_rate,
)
from water_sample_ranker.domain.models import Sample, SamplePair, Truth


def sample(**kwargs: object) -> Sample:
    s = Sample(
        "береговое", "501", "501", "ПК1", datetime(2026, 8, 10), 6001, 10000, 7000, "Т+П", ()
    )
    return replace(s, **kwargs)


def pair(**kwargs: object) -> SamplePair:
    return SamplePair(sample(**kwargs), sample(date=datetime(2026, 8, 1), chlorine=3000))


@pytest.mark.parametrize(
    "prev,last,min_prev,min_last,days,expected",
    [
        (3000, 6001, 10000, 10000, 8, Truth.TRUE),
        (2000, 5001, 10000, 10000, 8, Truth.FALSE),
        (8000, 11001, 10000, 10000, 8, Truth.FALSE),
        (7999, 11000, 10000, 10000, 8, Truth.TRUE),
        (3000, 5000, 10000, 10000, 8, Truth.FALSE),
        (3000, 6001, 22000, 10000, 8, Truth.FALSE),
        (3000, 6001, 10000, 22000, 8, Truth.FALSE),
        (3000, 6001, 10000, 10000, 7, Truth.FALSE),
        (3000, None, 10000, 10000, 8, Truth.UNKNOWN),
        (3000, 6001, None, 10000, 8, Truth.UNKNOWN),
        (-1, 6001, 10000, 10000, 8, Truth.UNKNOWN),
        (3000, -1, 10000, 10000, 8, Truth.UNKNOWN),
        (3000, 6001, -1, 10000, 8, Truth.UNKNOWN),
        (3000, 6001, 10000, -1, 8, Truth.UNKNOWN),
        (3000, 6001, 0, 0, 8, Truth.TRUE),
        (0, 6001, 10000, 10000, 8, Truth.FALSE),
    ],
)
def test_chlorine_literal_boundaries(prev, last, min_prev, min_last, days, expected) -> None:
    last_date = datetime(2026, 8, 20)
    p = SamplePair(
        sample(date=last_date, chlorine=last, mineralization=min_last),
        sample(date=last_date - timedelta(days=days), chlorine=prev, mineralization=min_prev),
    )
    assert chlorine(p, 2).state == expected
    assert chlorine(p, 2).normalized_score is None
    assert chlorine(p, 2).weighted_contribution is None


@pytest.mark.parametrize(
    "prev,last,days,expected",
    [
        (1999, 2000, 8, Truth.TRUE),
        (2000, 2001, 8, Truth.FALSE),
        (1999, 1999, 8, Truth.FALSE),
        (1999, 2000, 7, Truth.FALSE),
        (None, 2000, 8, Truth.UNKNOWN),
        (-1, 2000, 8, Truth.UNKNOWN),
        (1999, -1, 8, Truth.UNKNOWN),
        (0, 2000, 8, Truth.TRUE),
    ],
)
def test_genesis_threshold(prev, last, days, expected) -> None:
    a = sample(chlorine=last)
    assert a.date is not None
    p = SamplePair(a, sample(chlorine=prev, date=a.date - timedelta(days=days)))
    assert chlorine(p, 3).state == expected


@pytest.mark.parametrize(
    "q,cl,expected",
    [
        (1000, 3000, Truth.FALSE),
        (1000.01, 2000, Truth.FALSE),
        (1000.01, 2000.01, Truth.TRUE),
        (None, 4000, Truth.UNKNOWN),
        (-1, 4000, Truth.UNKNOWN),
        (7000, -1, Truth.UNKNOWN),
        (-1, 0, Truth.UNKNOWN),
        (0, 4000, Truth.FALSE),
        (7000, 0, Truth.FALSE),
        (1e100, 1e100, Truth.TRUE),
    ],
)
def test_water_liters(q, cl, expected) -> None:
    assert water_rate(pair(water_l_day=q, chlorine=cl)).state == expected


@pytest.mark.parametrize(
    "q,expected",
    [
        (6911.99, "Надым"),
        (6912, "сепаратор МЗС"),
        (0, "Надым"),
        (-1, "UNKNOWN"),
        (1e100, "сепаратор МЗС"),
        (None, "UNKNOWN"),
    ],
)
def test_separator_inclusive(q, expected) -> None:
    assert separator(pair(water_l_day=q), "ПК1") == expected
    assert separator(pair(water_l_day=q), "ПК20") == "NOT_APPLICABLE"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Т", Truth.TRUE),
        ("Т+К", Truth.TRUE),
        ("Т+П?", Truth.TRUE),
        ("К+Т", Truth.TRUE),
        ("П+Т", Truth.FALSE),
        ("К", Truth.FALSE),
        ("пр.Т", Truth.UNKNOWN),
        (None, Truth.UNKNOWN),
    ],
)
def test_technical_and_disputed(text, expected) -> None:
    p = SamplePair(sample(conclusion=text))
    assert technical(p, well="501").state == expected
    assert disputed(p).state == (
        Truth.UNKNOWN if text is None else Truth.TRUE if "?" in text else Truth.FALSE
    )


def test_23_exception_is_not_global() -> None:
    p = SamplePair(sample(well="23", conclusion="Т"))
    assert technical(p, well="23", field="береговое").state == Truth.UNKNOWN
    assert technical(p, well="23", field="пырейное").state == Truth.TRUE
    assert (
        technical(p, well="23", field="береговое", confirmed_scope=("береговое", "23", "ПК1")).state
        == Truth.NOT_APPLICABLE
    )
    assert (
        technical(p, well="23", field="пырейное", confirmed_scope=("береговое", "23", "ПК1")).state
        == Truth.TRUE
    )


def test_no_sample_and_duration_have_no_invented_score() -> None:
    empty, days, months = duration(SamplePair(), date(2026, 11, 5))
    assert (empty.state, days, months) == (Truth.UNKNOWN, None, None)
    known, days, months = duration(pair(), date(2026, 11, 5))
    assert days == 87 and months == 2
    assert known.state == Truth.UNKNOWN and known.normalized_score is None


def test_previous_only_conflict_does_not_discard_confirmed_latest_facts() -> None:
    p = SamplePair(
        last=sample(conclusion="Т+П?"), conflict=True, reason="DUPLICATE_TIMESTAMP_CONFLICT"
    )
    assert chlorine(p, 2).state == Truth.UNKNOWN
    assert chlorine(p, 3).state == Truth.UNKNOWN
    assert water_rate(p).state == Truth.TRUE
    assert technical(p, well="501").state == Truth.TRUE
    assert disputed(p).state == Truth.TRUE
    assert separator(p, "ПК1") == "сепаратор МЗС"
    _, days, months = duration(p, date(2026, 11, 5))
    assert (days, months) == (87, 2)
    empty = SamplePair(conflict=True, reason="DUPLICATE_TIMESTAMP_CONFLICT")
    assert water_rate(empty).state == Truth.UNKNOWN
    assert duration(empty, date(2026, 11, 5))[1] is None


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf"), -0.1])
@pytest.mark.parametrize("field", ["chlorine", "mineralization", "water_l_day"])
def test_nonfinite_or_negative_measurements_never_form_negative_decisions(bad, field):
    p = pair(**{field: bad})
    affected = {"chlorine": (2, 3, 6), "mineralization": (2,), "water_l_day": (6,)}[field]
    for number in affected:
        c = water_rate(p) if number == 6 else chlorine(p, number)
        assert c.state == Truth.UNKNOWN
        assert c.normalized_score is None and c.weighted_contribution is None
    if field == "water_l_day":
        assert separator(p, "ПК1") == "UNKNOWN"


@pytest.mark.parametrize("offset", [-365, -1, 1, 365])
def test_agreed_decisions_are_invariant_under_common_date_translation(offset):
    p = pair(conclusion="Т?")
    assert p.last is not None and p.previous is not None
    assert p.last.date is not None and p.previous.date is not None
    shifted = SamplePair(
        replace(p.last, date=p.last.date + timedelta(days=offset)),
        replace(p.previous, date=p.previous.date + timedelta(days=offset)),
    )
    for decision in (
        lambda x: chlorine(x, 2),
        lambda x: chlorine(x, 3),
        lambda x: technical(x, well="501"),
        disputed,
        water_rate,
    ):
        a, b = decision(p), decision(shifted)
        assert a.state == b.state
        assert a.raw_value == b.raw_value
        assert a.normalized_score is b.normalized_score is None


@pytest.mark.parametrize("seconds,expected", [(0, Truth.FALSE), (1, Truth.TRUE)])
def test_seven_days_boundary_uses_elapsed_time(seconds, expected):
    last = sample(date=datetime(2026, 8, 20), chlorine=6001)
    assert last.date is not None
    previous = sample(date=last.date - timedelta(days=7, seconds=seconds), chlorine=3000)
    assert chlorine(SamplePair(last, previous), 2).state == expected
