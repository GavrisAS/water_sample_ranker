"""Select two chronological observations of one confirmed history, never metric-wise."""

from collections import defaultdict
from datetime import date, datetime

from water_sample_ranker.domain.models import Sample, SamplePair, TargetIdentity, WellHistory
from water_sample_ranker.matching.lifecycle import assign_stage_by_date


def select_pair(
    identity: TargetIdentity,
    records: tuple[Sample, ...],
    histories: tuple[WellHistory, ...],
    as_of: date,
    source: str = "database",
) -> SamplePair:
    if identity.status != "RESOLVED" or identity.stage is None or identity.plast is None:
        return SamplePair(reason=identity.reason)
    candidates: list[Sample] = []
    undated: list[Sample] = []
    for s in records:
        if (
            s.source != source
            or s.field != identity.fund.normalized_field
            or s.root != identity.fund.base_well_id
            or s.plast != identity.plast
        ):
            continue
        if s.date is None:
            # An undated row with matching exact identity can be a newer incomplete sample.
            if s.well in {identity.display, identity.fund.target_well_id}:
                undated.append(s)
            continue
        if s.date.date() > as_of:
            continue
        assignment = assign_stage_by_date(
            physical_root=s.root,
            normalized_field=s.field,
            normalized_plast=s.plast,
            observed_at=s.date,
            histories=histories,
            target_identity=s.well,
            observation_location=s.evidence[0].location,
        )
        if assignment.stage == identity.stage:
            candidates.append(s)
    groups: defaultdict[datetime, list[Sample]] = defaultdict(list)
    for s in candidates:
        assert s.date is not None
        groups[s.date].append(s)
    selected: list[Sample] = []
    all_evidence = []
    for stamp in sorted(groups, reverse=True):
        group = groups[stamp]
        all_evidence.extend(e for s in group for e in s.evidence)
        fingerprints = {
            (
                s.chlorine,
                s.mineralization,
                s.water_l_day,
                s.conclusion,
                # Разные плохие значения и пропуск не превращаются в точные дубли.
                s.invalid_measurements,
                tuple(
                    (e.measurement_field, e.value)
                    for e in s.evidence
                    if e.status == "INVALID_MEASUREMENT"
                ),
            )
            for s in group
        }
        if len(fingerprints) != 1:
            return SamplePair(
                last=selected[0] if selected else None,
                reason="DUPLICATE_TIMESTAMP_CONFLICT",
                conflict=True,
                evidence=tuple(all_evidence),
            )
        selected.append(group[0])
        if len(selected) == 2:
            break
    return SamplePair(
        selected[0] if selected else None,
        selected[1] if len(selected) > 1 else None,
        "UNDATED_SKIPPED; LATEST_TWO_CHRONOLOGICAL_NO_SKIP"
        if undated
        else "EXACT_DUPLICATES_COLLAPSED"
        if any(len(g) > 1 for g in groups.values())
        else "LATEST_TWO_CHRONOLOGICAL_NO_SKIP"
        if selected
        else "NO_SAMPLE",
        evidence=tuple(all_evidence + [e for s in undated for e in s.evidence]),
    )


def current_samples(
    identity: TargetIdentity,
    records: tuple[Sample, ...],
    histories: tuple[WellHistory, ...],
    as_of: date,
) -> tuple[Sample, ...]:
    """Только датированные пробы доказанной актуальной стадии и пласта."""
    if identity.status != "RESOLVED" or identity.stage is None or identity.plast is None:
        return ()
    selected = []
    for s in records:
        if (
            s.source != "database"
            or s.field != identity.fund.normalized_field
            or s.root != identity.fund.base_well_id
            or s.plast != identity.plast
            or not s.date
            or s.date.date() > as_of
        ):
            continue
        stage = assign_stage_by_date(
            physical_root=s.root,
            normalized_field=s.field,
            normalized_plast=s.plast,
            observed_at=s.date,
            histories=histories,
            target_identity=s.well,
            observation_location=s.evidence[0].location,
        ).stage
        if stage == identity.stage:
            selected.append(s)
    return tuple(selected)


def complementary_gate(pair: SamplePair, samples: tuple[Sample, ...]) -> SamplePair:
    """Объединение только двух уникальных, дополняющих замеров в пределах 7 суток."""
    from dataclasses import replace

    from water_sample_ranker.criteria.rules import invalid

    s = pair.last
    if not s or not s.date or invalid(s, ("water", "chlorine")):
        return pair
    if (s.chlorine is None) == (s.water_l_day is None):
        return pair
    candidates = [
        p
        for p in samples
        if p.date
        and p.date != s.date
        and 0 < (s.date - p.date).total_seconds() <= 7 * 86400
        and (p.field, p.root, p.plast) == (s.field, s.root, s.plast)
    ]
    # Конкурирующие кандидаты не сортируются произвольно и не игнорируются.
    if len(candidates) != 1:
        return replace(
            pair,
            last=None,
            reason="GATE_COMPLEMENT_AMBIGUOUS" if candidates else "GATE_COMPLEMENT_UNAVAILABLE",
        )
    p = candidates[0]
    if invalid(p, ("water", "chlorine")) or not (
        (s.chlorine is None and p.chlorine is not None and p.water_l_day is None)
        or (s.water_l_day is None and p.water_l_day is not None and p.chlorine is None)
    ):
        return replace(pair, last=None, reason="GATE_COMPLEMENT_NOT_EXCLUSIVE")
    return SamplePair(
        replace(
            s,
            chlorine=s.chlorine if s.chlorine is not None else p.chlorine,
            water_l_day=s.water_l_day if s.water_l_day is not None else p.water_l_day,
        ),
        previous=p,
        reason="GATE_COMPLEMENT_EXACT_STAGE_WITHIN_7_DAYS",
        evidence=(*s.evidence, *p.evidence),
    )
