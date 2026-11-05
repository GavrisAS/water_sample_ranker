"""Calendar uncertainty belongs to diagnostics, independently of lifecycle policy."""

import json
from dataclasses import asdict, replace
from datetime import date, datetime

import pytest

from tests.test_stage_b_evidence import DAY, LOC, identity, ranked
from water_sample_ranker.diagnostics import event_view, evidence_aggregates
from water_sample_ranker.domain.dates import calendar_interval, cutoff_relation, parse_partial_date
from water_sample_ranker.domain.models import DatePrecision, PartialDate, WellEvent, WellEventKind
from water_sample_ranker.excel.writer import diagnostic_values
from water_sample_ranker.readers.optional import OptionalInputs, read_optional


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-11-04", "ON_OR_BEFORE_CUTOFF"),
        ("2026-11-05", "ON_OR_BEFORE_CUTOFF"),
        ("2026-11-06", "AFTER_CUTOFF"),
        ("10.2026", "ON_OR_BEFORE_CUTOFF"),
        ("12.2026", "AFTER_CUTOFF"),
        ("11.2026", "STRADDLES_CUTOFF"),
        ("2025", "ON_OR_BEFORE_CUTOFF"),
        ("2027", "AFTER_CUTOFF"),
        ("2026", "STRADDLES_CUTOFF"),
        (None, "UNKNOWN_DATE"),
        ("31.02.2026", "UNKNOWN_DATE"),
        ("unknown", "UNKNOWN_DATE"),
    ],
)
def test_history_and_plan_intervals_keep_identity_and_unknown(tmp_path, raw, expected):
    i, h = identity()
    d = parse_partial_date(raw)
    event = WellEvent("береговое", "DEMO-A", "DEMO-A", "ПК1", WellEventKind.PVR, "DEMO", d, LOC, ())
    h = replace(h, events=(event,))
    plan = OptionalInputs()
    if d:
        p = tmp_path / "plan.json"
        p.write_text(
            json.dumps(
                {
                    "schema": "water-gtm-plan-v1",
                    "records": [
                        {
                            "field": "береговое",
                            "well": "DEMO-A",
                            "stage": 0,
                            "plast": "ПК1",
                            "date": raw,
                            "precision": d.precision.value,
                            "label": "DEMO plan",
                        }
                    ],
                }
            )
        )
        plan = read_optional(p, "gtm-plan")
    view = event_view(i, (h,), plan, DAY)
    for e in view.events:
        assert e.cutoff_relation == expected
        assert e.partial_date == d
        assert e.calendar_interval == calendar_interval(d)
        assert e.locations
    assert view.events[0].locations == (LOC,)
    row, _ = ranked("DEMO-A", 1800)
    row = replace(row, diagnostic_evidence=(view,))
    assert all(c.state == "UNKNOWN" for c in row.criteria[6:])
    assert all(c.normalized_score is None for c in row.criteria)
    private = json.loads(json.dumps(asdict(view), default=str))
    assert private["events"][0]["cutoff_relation"] == expected
    aggregate = evidence_aggregates((row,))
    assert aggregate["8"]["event_cutoff"] == {expected: len(view.events)}
    assert "DEMO-A" not in json.dumps(aggregate) and "synthetic.xlsx" not in json.dumps(aggregate)
    text = diagnostic_values(row)[2]
    if d:
        assert "условная effective_date=" in text
        assert d.precision.value in text
    # Neither cutoff interpretation nor display mutates historical stage assignment.
    assert i.stage == identity()[0].stage


def test_leap_month_boundaries_and_explicit_time_policy():
    leap = calendar_interval(parse_partial_date("02.2024"))
    assert leap and leap.lower == date(2024, 2, 1) and leap.upper == date(2024, 2, 29)
    assert leap.relation(date(2024, 2, 28)) == "STRADDLES_CUTOFF"
    assert leap.relation(date(2024, 2, 29)) == "ON_OR_BEFORE_CUTOFF"
    assert (
        cutoff_relation(parse_partial_date("29.02.2024"), date(2024, 2, 29))
        == "ON_OR_BEFORE_CUTOFF"
    )
    assert parse_partial_date("29.02.2023") is None
    assert calendar_interval(datetime(2024, 2, 29, 12)) is None
    assert calendar_interval("2024") is None
    exact = calendar_interval(date(2024, 2, 29))
    assert exact and exact.lower == date(2024, 2, 29)
    with pytest.raises(TypeError):
        leap.relation(datetime(2024, 2, 29))


def test_conflicting_fact_dates_are_retained_and_never_resolve_truth():
    i, h = identity()
    d = PartialDate(2026, None, None, DatePrecision.YEAR)
    stages = (
        i.stage,
        replace(i.stage, started_at=d, stage_sequence=1),
        replace(i.stage, started_at=d, stage_sequence=2),
    )
    event = WellEvent("береговое", "DEMO-A", "DEMO-A", "ПК1", WellEventKind.PVR, "DEMO", d, LOC, ())
    view = event_view(i, (replace(h, stages=stages, events=(event,)),), OptionalInputs(), DAY)
    assert view.events[0].conflict
    assert view.events[0].cutoff_relation == "STRADDLES_CUTOFF"


@pytest.mark.parametrize("raw,precision", [("11.2026", "day"), ("29.02.2023", "day"), ("", "year")])
def test_incomplete_or_conflicting_plan_date_fails_whole_contract(tmp_path, raw, precision):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "schema": "water-gtm-plan-v1",
                "records": [
                    {
                        "field": "береговое",
                        "well": "DEMO-A",
                        "stage": 0,
                        "plast": "ПК1",
                        "date": raw,
                        "precision": precision,
                        "label": "DEMO",
                    }
                ],
            }
        )
    )
    plan = read_optional(path, "gtm-plan")
    assert plan.status == "SCHEMA_DRIFT" and not plan.plans
