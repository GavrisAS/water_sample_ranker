from dataclasses import replace
from datetime import date, datetime

from water_sample_ranker.domain.models import (
    DatePrecision,
    Evidence,
    FundCategory,
    FundWell,
    PartialDate,
    Sample,
    SourceLocation,
    WellEvent,
    WellEventKind,
    WellHistory,
    WellStage,
)
from water_sample_ranker.matching.current_plast import resolve_current
from water_sample_ranker.matching.lifecycle import propagate_stage_identities
from water_sample_ranker.matching.matcher import select_pair


def history() -> WellHistory:
    location = SourceLocation("synthetic.xlsx", "БЛУ", "C3")
    stages = tuple(
        WellStage(
            "Береговое",
            "501",
            "501",
            "501" if i == 0 else None,
            plast,
            kind,
            PartialDate(year, 1, 1, DatePrecision.DAY),
            i,
            location,
            (),
        )
        for i, (year, plast, kind) in enumerate(
            (
                (2020, "ПК1", WellEventKind.INITIAL),
                (2022, "ПК20", WellEventKind.ZBS),
                (2024, "ПК1", WellEventKind.PVLG),
            )
        )
    )
    return propagate_stage_identities(WellHistory("Береговое", "501", "501", (), stages))


def well(**kwargs: object) -> FundWell:
    f = FundWell(
        "501",
        "501",
        "Береговое",
        "береговое",
        "Береговое (ПК1)",
        FundCategory.INACTIVE_PREVIOUS_YEARS,
        date(2026, 10, 1),
        12,
        "H",
        target_well_id="501",
        object_plast_hint="ПК1",
    )
    return replace(f, **kwargs)


def observation(
    year: int, well: str = "501", field: str = "береговое", cl: float | None = 3000
) -> Sample:
    return Sample(
        field,
        well,
        "501",
        "ПК1",
        datetime(year, 8, 1),
        cl,
        10000,
        10,
        "К",
        (Evidence(SourceLocation("lha.xlsx", "БД Сибнефтегаз", f"G{year}"), year, year),),
    )


def test_inactive_pvlg_keeps_display_and_rejects_old_root_history() -> None:
    h = history()
    identity = resolve_current(well(), (h,), (), date(2026, 11, 5))
    assert identity.status == "RESOLVED"
    assert identity.display == "501_ЗБС"
    assert identity.stage is not None and identity.stage.stage_sequence == 2
    records = (
        observation(2021),
        observation(2024),
        observation(2025),
        observation(2026, field="пырейное"),
    )
    pair = select_pair(identity, records, (h,), date(2026, 11, 5))
    assert pair.last is not None and pair.last.date == datetime(2025, 8, 1)
    assert pair.previous is not None and pair.previous.date == datetime(2024, 8, 1)


def test_future_incomplete_latest_and_duplicate_conflicts_do_not_fall_back() -> None:
    h = history()
    identity = resolve_current(well(), (h,), (), date(2026, 11, 5))
    current = observation(2026, cl=None)
    records = (observation(2024), observation(2025), current, observation(2027))
    pair = select_pair(identity, records, (h,), date(2026, 11, 5))
    assert pair.last == current  # Incomplete latest is retained.
    conflict = select_pair(
        identity, (*records, observation(2026, cl=8000)), (h,), date(2026, 11, 5)
    )
    assert conflict.conflict and conflict.reason == "DUPLICATE_TIMESTAMP_CONFLICT"
    undated = select_pair(
        identity, (*records, replace(current, date=None)), (h,), date(2026, 11, 5)
    )
    assert undated.conflict and undated.last is None


def test_exact_duplicates_keep_all_source_cells() -> None:
    h = history()
    identity = resolve_current(well(), (h,), (), date(2026, 11, 5))
    a = observation(2026)
    b = replace(
        a, evidence=(Evidence(SourceLocation("lha.xlsx", "БД Сибнефтегаз", "G999"), 2026, 2026),)
    )
    pair = select_pair(identity, (observation(2025), a, b), (h,), date(2026, 11, 5))
    assert not pair.conflict and len(pair.evidence) == 3
    assert pair.reason == "EXACT_DUPLICATES_COLLAPSED"


def test_mzs_and_fields_are_separate_and_ambiguous_history_is_not_joined() -> None:
    h = history()
    identity = resolve_current(well(), (h, h), (), date(2026, 11, 5))
    assert identity.status == "AMBIGUOUS"
    mzs = resolve_current(
        well(raw_display="501 МЗС", target_well_id="501_МЗС"), (h,), (), date(2026, 11, 5)
    )
    assert mzs.status != "RESOLVED"
    other = resolve_current(well(normalized_field="пырейное"), (h,), (), date(2026, 11, 5))
    assert other.status != "RESOLVED"


def test_undated_lifecycle_boundary_blocks_assignment() -> None:
    h = history()
    event = WellEvent(
        "Береговое",
        "501",
        "501",
        "ПК1",
        WellEventKind.PNLG,
        "ПНЛГ",
        None,
        SourceLocation("base.xlsx", "БЛУ", "I3"),
        (),
    )
    h = replace(h, events=(event,))
    identity = resolve_current(well(), (h,), (), date(2026, 11, 5))
    assert identity.status == "AMBIGUOUS"


def test_non_pc_fund_objects_do_not_accept_a_different_family() -> None:
    from water_sample_ranker.matching.fund_scope import parse_fund_object_scope

    scope = parse_fund_object_scope("Береговое (АТ8-9)")
    assert scope.compatible("АТ8") and scope.compatible("АТ9")
    assert not scope.compatible("ПК8") and not scope.compatible("АТ10")
    scope = parse_fund_object_scope("Береговое (БТ2-1)")
    assert scope.exact_plast == "БТ2-1"
    assert not scope.compatible("ПК2")


def test_at_sublayers_preserve_primary_but_bt_label_remains_exact() -> None:
    from water_sample_ranker.matching.normalize import normalize_plast

    assert normalize_plast("АТ8-1-1") == "АТ8"
    assert normalize_plast("АТ8-1-2") == "АТ8"
    assert normalize_plast("БТ2-1") == "БТ2-1"
    assert normalize_plast("АТ8+9") != "АТ8"
