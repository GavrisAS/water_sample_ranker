"""Витрины №7–9: наблюдения и кандидаты без изменения утверждённых состояний."""

import re
from collections import Counter, defaultdict
from dataclasses import replace
from datetime import date, datetime, time
from typing import Any

from water_sample_ranker.domain.dates import (
    calendar_interval,
    cutoff_relation,
    date_defaulting_rule,
    effective_date,
)
from water_sample_ranker.domain.evidence import (
    CriterionEvidence,
    NeighborCandidate,
    ObservationPair,
    ParameterObservation,
    Reason,
    TimelineEvent,
)
from water_sample_ranker.domain.measurements import valid_measurement
from water_sample_ranker.domain.models import Evidence, RankedWell, TargetIdentity, WellHistory
from water_sample_ranker.matching.lifecycle import assign_stage_by_date
from water_sample_ranker.matching.normalize import normalize_field_name, normalize_well_id
from water_sample_ranker.readers.common import parse_number
from water_sample_ranker.readers.monitoring import MonitoringHistory
from water_sample_ranker.readers.optional import OptionalInputs


def owned_history(
    identity: TargetIdentity, histories: tuple[WellHistory, ...]
) -> WellHistory | None:
    matches = [
        h
        for h in histories
        if identity.stage is not None
        and identity.stage in h.stages
        and h.physical_root == identity.fund.base_well_id
        and normalize_field_name(h.field_name) == identity.fund.normalized_field
    ]
    return matches[0] if identity.status == "RESOLVED" and len(matches) == 1 else None


def confirmed_pad(
    identity: TargetIdentity, histories: tuple[WellHistory, ...]
) -> tuple[str | None, tuple]:
    h = owned_history(identity, histories)
    if not h or not h.pad or not h.pad_location:
        return None, ()
    # Only a literal numeric history pad is accepted. No inferred fill/alias.
    if not re.fullmatch(r"\d+(?:\.0+)?", h.pad):
        return None, (h.pad_location,)
    return str(int(float(h.pad))), (h.pad_location,)


def _pair(a: ParameterObservation, b: ParameterObservation) -> ObservationPair:
    assert isinstance(a.measurement.value, (float, int))
    assert isinstance(b.measurement.value, (float, int))
    delta = b.measurement.value - a.measurement.value
    return ObservationPair(
        a,
        b,
        delta,
        "INCREASE" if delta > 0 else "DECREASE" if delta < 0 else "UNCHANGED",
        (b.timestamp - a.timestamp).total_seconds() / 86400,
    )


def choke_view(
    identity: TargetIdentity,
    series: tuple[ParameterObservation, ...],
    histories: tuple[WellHistory, ...],
    monitoring: MonitoringHistory,
    as_of: date,
) -> CriterionEvidence:
    base = (Reason.RULE_PENDING, Reason.UNCONFIRMED_WINDOW)
    if identity.status != "RESOLVED" or identity.stage is None:
        return CriterionEvidence(7, (*base, Reason.IDENTITY_UNRESOLVED))
    if monitoring.reasons:
        return CriterionEvidence(7, (*base, *monitoring.reasons))
    selected, rejected = [], []
    for o in series:
        if o.field != identity.fund.normalized_field:
            continue
        well = normalize_well_id(o.display_well)
        if well.base_well_id != identity.fund.base_well_id or o.timestamp.date() > as_of:
            continue
        if not o.plast:
            rejected.append(o)
            continue
        assignment = assign_stage_by_date(
            physical_root=well.base_well_id or "",
            normalized_field=o.field,
            normalized_plast=o.plast,
            observed_at=o.timestamp,
            histories=histories,
            observation_location=o.measurement.location,
            target_identity=well.target_well_id,
        )
        if assignment.stage == identity.stage and (
            well.target_well_id == well.base_well_id
            or well.target_well_id == identity.stage.target_well_id
        ):
            selected.append(o)
        elif assignment.issue or (o.plast == identity.plast and assignment.stage is None):
            rejected.append(o)
    groups: dict[datetime, list[ParameterObservation]] = defaultdict(list)
    for o in selected:
        groups[o.timestamp].append(o)
    reasons: list[str] = list(base)
    ordered: list[ParameterObservation | None] = []
    trusted_pairs = []
    candidates = []
    for stamp in sorted(groups):
        g = groups[stamp]
        # Duplicate rows/columns are a source contract conflict even if values agree.
        if len(g) != 1:
            reasons.append("DUPLICATE_TIMESTAMP_CONFLICT")
            ordered.append(None)
            continue
        o = g[0]
        if o.quality:
            reasons.extend(o.quality)
            ordered.append(None)
            continue
        if ordered and ordered[-1] is not None:
            a = ordered[-1]
            assert a is not None
            # Different literal pads/GSKs are not silently merged into a series.
            if (a.pad, a.gsk) == (o.pad, o.gsk):
                pair = _pair(a, o)
                trusted_pairs.append(pair)
                if pair.delta != 0:
                    candidates.append(pair)
            else:
                reasons.append(Reason.SOURCE_CONFLICT)
        ordered.append(o)
    pair = trusted_pairs[-1] if trusted_pairs else None
    latest_group = groups[max(groups)] if groups else []
    status = (
        "LATEST_COMPARABLE_PAIR" if pair and latest_group == [pair.latest] else "LATEST_BLOCKED"
    )
    if rejected:
        reasons.append(Reason.IDENTITY_UNRESOLVED)
        # A possibly newer unresolved row blocks recency; never substitute an old pair.
        if not pair or max(o.timestamp for o in rejected) >= pair.latest.timestamp:
            status = "IDENTITY_BLOCKS_RECENCY"
    if status != "LATEST_COMPARABLE_PAIR":
        reasons.append(Reason.INCOMPLETE_LATEST_NO_SKIP)
    if not groups:
        reasons.append(Reason.SOURCE_UNAVAILABLE)
    # All trusted adjacent changes are historical observations; only a current pair
    # can be a current candidate. Anomalous cells remain visible in latest rows.
    retained = tuple(o for stamp in sorted(groups)[-2:] for o in groups[stamp])
    retained += tuple(sorted(rejected, key=lambda o: o.timestamp)[-2:])
    return CriterionEvidence(
        7,
        tuple(dict.fromkeys(reasons)),
        retained,
        pair,
        tuple(candidates),
        comparison_status=status,
    )


def event_view(
    identity: TargetIdentity, histories: tuple[WellHistory, ...], plan: OptionalInputs, as_of: date
) -> CriterionEvidence:
    reasons = [Reason.RULE_PENDING, Reason.UNCONFIRMED_WINDOW]
    h = owned_history(identity, histories)
    if not h:
        return CriterionEvidence(
            8, (*reasons, Reason.IDENTITY_UNRESOLVED), optional_source_status=plan.status
        )
    events = []
    for e in h.events:
        d = effective_date(e.event_date) if e.event_date else None
        stamp = datetime.combine(d, time.min) if d else None
        stage = next((s for s in h.stages if s.source_location == e.source_location), None)
        if stage is None and stamp and e.plast:
            stage = assign_stage_by_date(
                physical_root=h.physical_root,
                normalized_field=identity.fund.normalized_field,
                normalized_plast=e.plast,
                observed_at=stamp,
                histories=histories,
                observation_location=e.source_location,
                target_identity=normalize_well_id(h.source_display_well).target_well_id,
            ).stage
        conflict = (
            e.event_date is None
            or stage is None
            or sum(s.started_at == e.event_date for s in h.stages) > 1
        )
        events.append(
            TimelineEvent(
                e.event_kind.value,
                e.event_label,
                e.event_date,
                d,
                date_defaulting_rule(e.event_date) if e.event_date else "UNKNOWN_DATE",
                cutoff_relation(e.event_date, as_of),
                stage.stage_sequence if stage else None,
                "CURRENT_STAGE"
                if stage == identity.stage
                else "HISTORICAL_STAGE"
                if stage
                else "UNRESOLVED",
                e.plast,
                (e.source_location,),
                conflict=conflict,
                calendar_interval=calendar_interval(e.event_date),
                raw_date=e.raw_date,
                date_number_format=e.date_number_format,
            )
        )
    for p in plan.plans:
        if (p.field, p.well, p.stage, p.plast) != (
            identity.fund.normalized_field,
            identity.display,
            identity.stage.stage_sequence if identity.stage else None,
            identity.plast,
        ):
            continue
        d = effective_date(p.date)
        events.append(
            TimelineEvent(
                "PLAN",
                p.label,
                p.date,
                d,
                date_defaulting_rule(p.date),
                cutoff_relation(p.date, as_of),
                p.stage,
                "CURRENT_STAGE",
                p.plast,
                (p.location,),
                "PLAN_ONLY",
                calendar_interval=calendar_interval(p.date),
                raw_date=p.raw_date,
            )
        )
    reasons.append(plan.status)
    return CriterionEvidence(
        8, tuple(reasons), events=tuple(events), optional_source_status=plan.status
    )


def _cl(row: RankedWell, as_of: date) -> Evidence | None:
    s = row.pair.last
    if (
        row.identity.status != "RESOLVED"
        or not s
        or not s.date
        or s.date.date() > as_of
        or row.identity.stage is None
        or s.date.date() < effective_date(row.identity.stage.started_at)
        or s.field != row.identity.fund.normalized_field
        or s.plast != row.identity.plast
        or s.source != "database"
        or s.chlorine is None
        or not valid_measurement(s.chlorine)
        or "chlorine" in s.invalid_measurements
    ):
        return None
    # Source columns are fixed by the database contract; retain exact raw/cache.
    return next(
        (
            e
            for e in s.evidence
            if e.location.cell
            and e.location.cell.rstrip("0123456789") == "Z"
            and e.status == "VALID"
            and e.unit == "мг/дм³"
            and parse_number(e.value) == s.chlorine
        ),
        None,
    )


def neighbor_view(
    row: RankedWell,
    rows: tuple[RankedWell, ...],
    histories: tuple[WellHistory, ...],
    geo: OptionalInputs,
    as_of: date,
) -> CriterionEvidence:
    reasons = [Reason.RULE_PENDING, "MAP_DISTANCE_RULE_UNCONFIRMED"]
    pad, origins = confirmed_pad(row.identity, histories)
    matches = []
    cl = _cl(row, as_of)
    if pad is None:
        reasons.append(Reason.NO_CONFIRMED_PAD)
    elif cl is None:
        reasons.append("NO_CONFIRMED_LATEST_CL")
    elif row.pair.last and row.pair.last.chlorine is not None and row.pair.last.chlorine < 2000:
        for other in rows:
            if (
                other.identity.fund.normalized_field != row.identity.fund.normalized_field
                or other.identity.plast != row.identity.plast
                or row.identity.plast is None
                or other.identity.fund.base_well_id == row.identity.fund.base_well_id
            ):
                continue
            opad, olocs = confirmed_pad(other.identity, histories)
            ocl = _cl(other, as_of)
            if (
                opad != pad
                or ocl is None
                or not other.pair.last
                or other.pair.last.chlorine is None
                or other.pair.last.chlorine < 2000
            ):
                continue
            assert row.pair.last.date and other.pair.last.date
            matches.append(
                NeighborCandidate(
                    other.identity.display,
                    pad,
                    row.identity.plast,
                    cl,
                    ocl,
                    row.pair.last.date,
                    other.pair.last.date,
                    (*origins, *olocs, *row.identity.evidence, *other.identity.evidence),
                )
            )
    else:
        reasons.append("TARGET_NOT_BELOW_CL_THRESHOLD")
    points = tuple(
        p.location
        for p in geo.points
        if row.identity.stage
        and (p.field, p.well, p.stage, p.plast)
        == (
            row.identity.fund.normalized_field,
            row.identity.display,
            row.identity.stage.stage_sequence,
            row.identity.plast,
        )
    )
    reasons.append(geo.status)
    return CriterionEvidence(
        9,
        tuple(reasons),
        neighbors=tuple(matches),
        locations=(*origins, *points),
        optional_source_status=geo.status if points or not geo.points else "NO_EXACT_GEO_IDENTITY",
    )


def attach_evidence(
    rows: tuple[RankedWell, ...],
    histories: tuple[WellHistory, ...],
    monitoring: MonitoringHistory,
    plan: OptionalInputs,
    geo: OptionalInputs,
    as_of: date,
) -> tuple[RankedWell, ...]:
    # Index once; do not scan the large monitoring history for every target.
    index: dict[tuple[str, str | None], list[ParameterObservation]] = defaultdict(list)
    for o in monitoring.observations:
        index[o.field, normalize_well_id(o.display_well).base_well_id].append(o)
    return tuple(
        replace(
            r,
            diagnostic_evidence=(
                choke_view(
                    r.identity,
                    tuple(index[r.identity.fund.normalized_field, r.identity.fund.base_well_id]),
                    histories,
                    monitoring,
                    as_of,
                ),
                event_view(r.identity, histories, plan, as_of),
                neighbor_view(r, rows, histories, geo, as_of),
            ),
        )
        for r in rows
    )


def evidence_aggregates(rows: tuple[RankedWell, ...]) -> dict[str, Any]:
    """Только количества и коды; никаких номеров/координат/сырых значений."""
    return {
        str(n): {
            "rows": len(rows),
            "reasons": dict(
                Counter(
                    code
                    for r in rows
                    for e in r.diagnostic_evidence
                    if e.number == n
                    for code in e.reasons
                )
            ),
            "pairs": sum(
                e.pair is not None for r in rows for e in r.diagnostic_evidence if e.number == n
            ),
            "changes": sum(
                len(e.change_candidates)
                for r in rows
                for e in r.diagnostic_evidence
                if e.number == n
            ),
            "events": sum(
                len(e.events) for r in rows for e in r.diagnostic_evidence if e.number == n
            ),
            "neighbors": sum(
                len(e.neighbors) for r in rows for e in r.diagnostic_evidence if e.number == n
            ),
            "comparison_status": dict(
                Counter(
                    e.comparison_status
                    for r in rows
                    for e in r.diagnostic_evidence
                    if e.number == n
                )
            ),
            "event_kinds": dict(
                Counter(
                    t.kind
                    for r in rows
                    for e in r.diagnostic_evidence
                    if e.number == n
                    for t in e.events
                )
            ),
            "event_cutoff": dict(
                Counter(
                    t.cutoff_relation
                    for r in rows
                    for e in r.diagnostic_evidence
                    if e.number == n
                    for t in e.events
                )
            ),
            "event_stages": dict(
                Counter(
                    t.stage_relation
                    for r in rows
                    for e in r.diagnostic_evidence
                    if e.number == n
                    for t in e.events
                )
            ),
        }
        for n in (7, 8, 9)
    }


def freshness(rows: tuple[RankedWell, ...], as_of: date) -> dict[str, Any]:
    confirmed = [r.pair.last.date.date() for r in rows if r.pair.last and r.pair.last.date]
    ages = [(as_of - d).days for d in confirmed if d <= as_of]
    return {
        "confirmed_latest_count": len(confirmed),
        "unknown_latest_count": len(rows) - len(confirmed),
        "by_calendar_month": dict(sorted(Counter(d.strftime("%Y-%m") for d in confirmed).items())),
        "age_days_descriptive": {
            label: sum(lo <= age < hi for age in ages)
            for label, lo, hi in (
                ("[0,31)", 0, 31),
                ("[31,91)", 31, 91),
                ("[91,181)", 91, 181),
                ("[181,366)", 181, 366),
                ("[366,infinity)", 366, float("inf")),
            )
        },
        "policy": "DESCRIPTIVE_ONLY_NO_EXPIRY_OR_SCORE",
    }
