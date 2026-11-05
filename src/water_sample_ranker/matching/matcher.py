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
    if undated:
        return SamplePair(
            reason="UNDATED_SAMPLE_BLOCKS_RECENCY",
            conflict=True,
            evidence=tuple(e for s in undated for e in s.evidence),
        )
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
        "EXACT_DUPLICATES_COLLAPSED"
        if any(len(g) > 1 for g in groups.values())
        else "LATEST_TWO_CHRONOLOGICAL_NO_SKIP"
        if selected
        else "NO_SAMPLE",
        evidence=tuple(all_evidence),
    )
