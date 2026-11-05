"""Вымышленные DEMO identities: факты, кандидаты и Truth проверяются раздельно."""

import json
from dataclasses import replace
from datetime import date, datetime

import pytest
from openpyxl import Workbook

from water_sample_ranker.criteria.rules import WEIGHTS, evaluate
from water_sample_ranker.diagnostics import (
    attach_evidence,
    choke_view,
    event_view,
    freshness,
    neighbor_view,
)
from water_sample_ranker.domain.models import (
    DatePrecision,
    Evidence,
    FundCategory,
    FundWell,
    PartialDate,
    RankedWell,
    Sample,
    SamplePair,
    SourceLocation,
    TargetIdentity,
    Truth,
    WellEvent,
    WellEventKind,
    WellHistory,
    WellStage,
)
from water_sample_ranker.readers.monitoring import MonitoringHistory, read_history
from water_sample_ranker.readers.optional import read_optional

DAY = date(2026, 11, 5)
LOC = SourceLocation("synthetic.xlsx", "DEMO", "A3")


def identity(well="DEMO-A", field="береговое", plast="ПК1", pad="1"):
    stage = WellStage(
        field,
        well,
        well,
        well,
        plast,
        WellEventKind.INITIAL,
        PartialDate(2020, 1, 1, DatePrecision.DAY),
        0,
        LOC,
        (),
    )
    history = WellHistory(field, well, well, (), (stage,), pad, LOC)
    fund = FundWell(
        well, well, field, field, plast, FundCategory.PRODUCING_GAS, DAY, 3, "C", well, plast
    )
    return TargetIdentity(fund, well, plast, stage, "RESOLVED", "EXACT", (LOC,)), history


def monitoring(tmp_path, values=(10, 12, None), dates=None, wells=("DEMO-A",), unit="мм"):
    w = Workbook()
    s = w.active
    s.title = "Техрежим скважин"
    for cell, value in {
        "A1": "Береговое",
        "A2": "Параметр",
        "E2": "Объект",
        "B3": "ГСК",
        "C3": "куста",
        "D3": "скв.",
    }.items():
        s[cell] = value
    for col, stamp in enumerate(
        dates or (datetime(2026, 10, d) for d in range(1, len(values) + 1)), 6
    ):
        s.cell(3, col, stamp)
        s.cell(1, col, datetime(2026, 1, 1))  # Wrong row must never become observation date.
    for n, well in enumerate(wells, 4):
        for col, value in enumerate(
            (f"Типоразмер штуцера, {unit}", "ГСК-DEMO", "К-1", well, "ПК1", *values), 1
        ):
            s.cell(n, col, value)
    path = tmp_path / "synthetic-monitoring.xlsx"
    w.save(path)
    return path


def view(tmp_path, values, **kwargs):
    i, h = identity()
    m = read_history(monitoring(tmp_path, values, **kwargs), DAY)
    return choke_view(i, m.observations, (h,), m, DAY), m


def test_reader_actual_date_row_and_no_skip_incomplete_latest(tmp_path):
    v, m = view(tmp_path, (10, 12, None))
    assert m.observations[0].timestamp == datetime(2026, 10, 1)
    assert m.observations[0].measurement.raw == 10
    assert m.observations[0].measurement.unit == "мм"
    assert m.observations[0].header_location.cell == "F3"
    assert v.pair and v.pair.delta == 2
    assert v.comparison_status == "LATEST_BLOCKED"
    assert "MISSING" in v.reasons
    assert v.observations[-1].measurement.value is None


@pytest.mark.parametrize("sentinel", [0, 66, 109, "66", "109"])
def test_suspect_cells_are_barriers_without_threshold_invention(tmp_path, sentinel):
    v, _ = view(tmp_path, (10, sentinel, 12))
    assert v.pair is None and not v.change_candidates
    assert "SUSPECT_VALUE_SEMANTICS" in v.reasons
    assert v.comparison_status == "LATEST_BLOCKED"


def test_mass_zero_group_has_date_frequency_share_and_provenance(tmp_path):
    path = monitoring(tmp_path, (0,), wells=tuple(f"DEMO-{n}" for n in range(118)))
    m = read_history(path, DAY)
    assert m.choke_rows == 118
    assert m.groups[0]["count"] == 118 and m.groups[0]["share"] == 1
    assert m.groups[0]["date"] == "2026-10-01T00:00:00"
    assert m.groups[0]["all_rows_zero"]
    assert m.groups[0]["header_locations"][0].cell == "F3"
    assert all("ANOMALOUS_SERIES" in o.quality for o in m.observations)


@pytest.mark.parametrize(
    "values,unit,reason",
    [
        ((10, -2), "мм", "INVALID_MEASUREMENT"),
        ((10, 12), "cm", "INVALID_UNIT"),
        ((10, "=F4+2"), "мм", "FORMULA_NO_CACHE"),
    ],
)
def test_units_invalid_cache_are_explicit(tmp_path, values, unit, reason):
    v, _ = view(tmp_path, values, unit=unit)
    assert v.pair is None and reason in v.reasons
    assert not v.change_candidates


def test_duplicate_dates_are_not_collapsed_or_bridged(tmp_path):
    v, _ = view(tmp_path, (10, 11, 12), dates=[datetime(2026, 10, 1)] * 2 + [datetime(2026, 10, 2)])
    assert "DUPLICATE_TIMESTAMP_CONFLICT" in v.reasons
    assert v.pair is None


def test_cutoff_and_large_non_sentinel_are_observations_only(tmp_path):
    v, m = view(
        tmp_path,
        (12, 150, 200),
        dates=[datetime(2026, 10, 1), datetime(2026, 11, 1), datetime(2026, 12, 1)],
    )
    assert m.future_cells == 1
    assert v.pair and v.pair.delta == 138 and v.pair.direction == "INCREASE"
    assert v.pair.interval_days == 31
    assert v.comparison_status == "LATEST_COMPARABLE_PAIR"
    assert "UNCONFIRMED_WINDOW" in v.reasons


def test_schema_drift_declines_entire_history(tmp_path):
    from openpyxl import load_workbook

    p = monitoring(tmp_path, (12, 14))
    w = load_workbook(p)
    w.active["D3"] = "Неизвестная колонка"
    w.save(p)
    m = read_history(p, DAY)
    assert m.reasons == ("SCHEMA_DRIFT",) and not m.observations


def test_field_bore_plast_and_stage_separation(tmp_path):
    v, m = view(tmp_path, (10, 12))
    i, h = identity()
    assert v.pair
    for changed in (
        replace(i, fund=replace(i.fund, normalized_field="пырейное")),
        replace(i, plast="ПК2", stage=replace(i.stage, plast="ПК2")),
        replace(i, status="AMBIGUOUS"),
    ):
        assert choke_view(changed, m.observations, (h,), m, DAY).pair is None
    side, sh = identity("DEMO-A_ГС")
    assert choke_view(side, m.observations, (h, sh), m, DAY).pair is None
    # A lifecycle transition splits even same-plast measurements.
    boundary = PartialDate(2026, 10, 2, DatePrecision.DAY)
    newer = replace(i.stage, started_at=boundary, stage_sequence=1, event_kind=WellEventKind.PVLG)
    nh = replace(h, stages=(i.stage, newer))
    assert choke_view(replace(i, stage=newer), m.observations, (nh,), m, DAY).pair is None


def test_history_fact_plan_precision_cutoff_and_other(tmp_path):
    i, h = identity()
    events = tuple(
        WellEvent("береговое", "DEMO-A", "DEMO-A", "ПК1", kind, label, d, LOC, ())
        for kind, label, d in (
            (WellEventKind.PVR, "ПВР", PartialDate(2024, None, None, DatePrecision.YEAR)),
            (WellEventKind.OTHER, "DEMO событие", PartialDate(2026, 12, None, DatePrecision.MONTH)),
            (WellEventKind.KLK, "КЛК", None),
        )
    )
    h = replace(h, events=events)
    p = tmp_path / "synthetic-plan.json"
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
                        "date": "2026-12-05",
                        "precision": "day",
                        "label": "DEMO план",
                    }
                ],
            }
        )
    )
    plan = read_optional(p, "gtm-plan")
    v = event_view(i, (h,), plan, DAY)
    assert len(v.events) == 4
    assert v.events[0].date_policy == "year_to_january_first"
    assert v.events[1].kind == "OTHER" and v.events[1].cutoff_relation == "AFTER_CUTOFF"
    assert v.events[2].conflict and v.events[2].effective_date is None
    assert v.events[-1].origin == "PLAN_ONLY"
    assert "UNCONFIRMED_WINDOW" in v.reasons
    assert event_view(replace(i, status="AMBIGUOUS"), (h,), plan, DAY).events == ()
    missing = event_view(i, (h,), read_optional(None, "gtm-plan"), DAY)
    assert missing.optional_source_status == "SOURCE_UNAVAILABLE" and len(missing.events) == 3


@pytest.mark.parametrize(
    "bad",
    [
        {},
        {"schema": "foreign", "records": []},
        {"schema": "water-gtm-plan-v1", "records": [{"field": "DEMO"}]},
    ],
)
def test_optional_schema_never_best_effort(tmp_path, bad):
    p = tmp_path / "synthetic-invalid.json"
    p.write_text(json.dumps(bad))
    assert read_optional(p, "gtm-plan").status == "SCHEMA_DRIFT"
    assert read_optional(p, "geodata").status == "SCHEMA_DRIFT"


def ranked(well, cl, **kwargs):
    i, h = identity(well, **kwargs)
    ev = Evidence(SourceLocation("synthetic.xlsx", "БД Сибнефтегаз", "Z2"), cl, cl, "мг/дм³")
    s = Sample(
        i.fund.normalized_field,
        well,
        well,
        i.plast,
        datetime(2026, 10, 1),
        cl,
        10000,
        1000,
        None,
        (ev,),
    )
    pair = SamplePair(last=s, evidence=(ev,))
    criteria, days, months = evaluate(i, pair, SamplePair(), DAY)
    return RankedWell(i, pair, SamplePair(), criteria, days, months, "UNKNOWN", 0.1, 0.9), h


def test_same_pad_is_candidate_with_both_cl_dates_not_truth():
    a, ah = ranked("DEMO-A", 1900)
    b, bh = ranked("DEMO-B", 2000)
    rows = attach_evidence(
        (a, b),
        (ah, bh),
        MonitoringHistory(),
        read_optional(None, "gtm-plan"),
        read_optional(None, "geodata"),
        DAY,
    )
    v = rows[0].diagnostic_evidence[2]
    assert len(v.neighbors) == 1 and v.neighbors[0].neighbor == "DEMO-B"
    assert v.neighbors[0].target_date == v.neighbors[0].neighbor_date
    assert v.neighbors[0].target_chlorine.location.cell == "Z2"
    assert all(c.state == Truth.UNKNOWN for r in rows for c in r.criteria[6:])
    assert all(c.normalized_score is None for r in rows for c in r.criteria)
    assert tuple(c.weight for c in rows[0].criteria) == WEIGHTS


@pytest.mark.parametrize(
    "change",
    [
        "pad",
        "plast",
        "field",
        "identity",
        "unknown_cl",
        "bad_cl",
        "future",
        "conflict",
        "same_root",
    ],
)
def test_unconfirmed_neighbors_never_become_candidates(change):
    a, ah = ranked("DEMO-A", 1900)
    b, bh = ranked("DEMO-B", 2100)
    if change in {"pad", "plast", "field"}:
        b, bh = ranked(
            "DEMO-B", 2100, **{change: {"pad": "2", "plast": "ПК2", "field": "пырейное"}[change]}
        )
    if change == "identity":
        b = replace(b, identity=replace(b.identity, status="AMBIGUOUS"))
    if change in {"unknown_cl", "bad_cl", "future"}:
        b = replace(
            b,
            pair=replace(
                b.pair,
                last=replace(
                    b.pair.last,
                    **(
                        {"chlorine": None}
                        if change == "unknown_cl"
                        else {"chlorine": -10}
                        if change == "bad_cl"
                        else {"date": datetime(2026, 12, 1)}
                    ),
                ),
            ),
        )
    if change == "conflict":
        b = replace(b, pair=SamplePair(conflict=True))
    if change == "same_root":
        b = replace(
            b, identity=replace(b.identity, fund=replace(b.identity.fund, base_well_id="DEMO-A"))
        )
    assert not neighbor_view(a, (a, b), (ah, bh), read_optional(None, "geodata"), DAY).neighbors


def test_no_pad_or_coordinates_is_unknown_and_age_is_descriptive(tmp_path):
    a, ah = ranked("DEMO-A", 1900, pad=None)
    v = neighbor_view(a, (a,), (ah,), read_optional(None, "geodata"), DAY)
    assert "NO_CONFIRMED_PAD" in v.reasons
    p = tmp_path / "synthetic-geo.json"
    payload = {
        "schema": "water-geodata-v1",
        "records": [
            {
                "field": "береговое",
                "well": "DEMO-A",
                "stage": 0,
                "plast": "ПК1",
                "x": 0.1,
                "y": 0.2,
                "crs": "EPSG:4326",
                "coordinate_source": "SYNTHETIC",
                "version": "demo-v1",
            }
        ],
    }
    p.write_text(json.dumps(payload))
    geo = read_optional(p, "geodata")
    assert geo.status == "AVAILABLE_RULE_PENDING"
    v = neighbor_view(a, (a,), (ah,), geo, DAY)
    assert v.locations and "MAP_DISTANCE_RULE_UNCONFIRMED" in v.reasons
    payload["records"][0]["x"] = 999
    p.write_text(json.dumps(payload))
    assert read_optional(p, "geodata").status == "SCHEMA_DRIFT"
    f = freshness((a,), DAY)
    assert f["by_calendar_month"] == {"2026-10": 1}
    assert f["age_days_descriptive"]["[31,91)"] == 1


def test_literal_pad_change_is_not_one_comparable_series(tmp_path):
    _, m = view(tmp_path, (10, 12))
    i, h = identity()
    series = (m.observations[0], replace(m.observations[1], pad="К-DEMO-OTHER"))
    v = choke_view(i, series, (h,), m, DAY)
    assert v.pair is None and "SOURCE_CONFLICT" in v.reasons


def test_cached_formula_and_1904_epoch_are_read_without_execution(tmp_path):
    import xml.etree.ElementTree as ET
    from zipfile import ZipFile

    from openpyxl import load_workbook
    from openpyxl.utils.datetime import CALENDAR_MAC_1904

    p = monitoring(tmp_path, (10, "=F4+2"))
    w = load_workbook(p)
    w.epoch = CALENDAR_MAC_1904
    w.save(p)
    with ZipFile(p) as z:
        payloads = {n: z.read(n) for n in z.namelist()}
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    xml = ET.fromstring(payloads["xl/worksheets/sheet1.xml"])
    cell = next(c for c in xml.iter(ns + "c") if c.get("r") == "G4")
    cache = cell.find(ns + "v")
    assert cache is not None
    cache.text = "12"
    payloads["xl/worksheets/sheet1.xml"] = ET.tostring(xml)
    with ZipFile(p, "w") as z:
        for n, data in payloads.items():
            z.writestr(n, data)
    m = read_history(p, DAY)
    assert m.observations[0].timestamp == datetime(2026, 10, 1)
    assert m.observations[-1].measurement.formula == "=F4+2"
    assert m.observations[-1].measurement.value == 12 and not m.observations[-1].quality


@pytest.mark.parametrize(
    "kind", ["precision", "stage_bool", "extra", "duplicate_key", "crs", "coordinate_bool"]
)
def test_optional_contract_rejects_semantic_drift(tmp_path, kind):
    from scripts.make_synthetic_demo import synthetic_optional

    role = "geodata" if kind in {"crs", "coordinate_bool"} else "gtm_plan"
    payload = synthetic_optional()[role]
    record = payload["records"][0]
    if kind == "precision":
        record["precision"] = "year"
    elif kind == "stage_bool":
        record["stage"] = True
    elif kind == "extra":
        record["guess"] = "FORBIDDEN"
    elif kind == "crs":
        record["crs"] = "UNSPECIFIED"
    elif kind == "coordinate_bool":
        record["x"] = True
    text = json.dumps(payload)
    if kind == "duplicate_key":
        text = text.replace('"stage": 0', '"stage": 0, "stage": 1')
    p = tmp_path / "synthetic-drift.json"
    p.write_text(text)
    result = read_optional(p, role.replace("_", "-"))
    assert result.status == "SCHEMA_DRIFT" and not result.plans and not result.points


@pytest.mark.parametrize("role", ["gtm-plan", "geodata"])
def test_optional_size_bound_survives_stale_stat(tmp_path, monkeypatch, role):
    from pathlib import Path
    from types import SimpleNamespace

    p = tmp_path / "synthetic-large.json"
    p.write_bytes(b" " * (4 * 1024**2 + 1))
    original = Path.stat

    def stale_stat(path, *args, **kwargs):
        actual = original(path, *args, **kwargs)
        if path == p:
            return SimpleNamespace(st_mode=actual.st_mode, st_size=1)
        return actual

    monkeypatch.setattr(Path, "stat", stale_stat)
    result = read_optional(p, role)
    assert result.status == "SCHEMA_DRIFT" and not result.plans and not result.points


@pytest.mark.parametrize("role", ["gtm_plan", "geodata"])
@pytest.mark.parametrize("bad", ["duplicate_key", "second_record", "schema_type", "invalid_utf8"])
def test_optional_rejects_whole_source_after_valid_record(tmp_path, role, bad):
    from scripts.make_synthetic_demo import synthetic_optional

    payload = synthetic_optional()[role]
    if bad == "schema_type":
        payload["schema"] = []
    if bad == "second_record":
        payload["records"].append({"field": "DEMO"})
    raw = json.dumps(payload).encode()
    if bad == "duplicate_key":
        raw = raw.replace(b'"stage": 0', b'"stage": 0, "stage": 1')
    if bad == "invalid_utf8":
        raw += b"\xff"
    path = tmp_path / "synthetic-invalid.json"
    path.write_bytes(raw)
    result = read_optional(path, role.replace("_", "-"))
    assert result.status == "SCHEMA_DRIFT" and not result.plans and not result.points
